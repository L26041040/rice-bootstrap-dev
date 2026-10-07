#!/usr/bin/env python3
"""rice upgrade matrix (RICE-UPGRADE-MATRIX-001): every published release,
upgraded to the candidate through the real lifecycle, must converge to
exactly what a fresh install of the candidate produces -- and uninstalling
either must leave nothing but the project.

Private release tooling, like release.py: never exported, never shipped.
It only ever READS other builds' verdicts, so -- as in release.py -- the
verifier's and uninstall's words are assembled at runtime here, keeping the
"only verify.py / uninstall.py say these" greps honest.

Rows. One per public `release/<semver>` branch -- enumerated from the
public repository at run time, never kept as a list here -- plus `fresh`.
(Should the supported range ever need a floor, `releases()` is the one
place it would go; there is none today.)

Each row runs twice, on two project shapes: `bare` (a repo with no Claude
configuration) and `rich` (project-owned settings, CLAUDE.md, AGENTS.md
content, a project deny rule identical to one of rice's, other plugins).

  1. The release under test bootstraps the project with ITS OWN shipped CLI
     and Stop hook, the way the OWNER's sessions ran it: `rice.py bootstrap`,
     the harness-denied git probe it asks for, Matt's setup, the Stop
     continuation stamping that release's final verdict, a commit.
  2. The candidate re-runs `rice.py bootstrap` with no OWNER answers, as the
     README's update path says (I15), until it verifies final.
  3. The result is compared with a fresh candidate install of the same
     project; both are uninstalled with the candidate's own uninstall and
     compared with the project.

The only stand-ins are the two external probes every build makes --
`claude plugin list --json` and `rtk --version` -- answered by tiny
executables first on PATH, so every build runs unmodified.

Verdict per row, worst first:

  PROJECT DATA CHANGED  a project-owned byte or setting did not survive
  LEGACY RESIDUE        the upgraded repo differs from a fresh install, or
                        uninstall leaves rice state behind -- including when
                        the candidate still verified final (a verifier or
                        migration defect, never a success)
  BLOCKED               a lifecycle step did not reach its verdict
  CLEAN                 none of the above
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_public  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_REPO = f"https://github.com/{export_public.MARKETPLACE_REPO}.git"
SEMVER = re.compile(r"\d+\.\d+\.\d+")
VERDICTS = ("PROJECT DATA CHANGED", "LEGACY RESIDUE", "BLOCKED", "CLEAN")  # worst first
DATA, RESIDUE, BLOCKED, CLEAN = VERDICTS

GIT_DENY_PROBE = "git branch --show-current"  # what every build's verifier asks the harness to deny
VERSION_FILE = ".claude/rice-" + "version"
MARKER = ".claude/WAITING_FOR_MATT"
FINAL = "FINAL-" + "VERIFIED"
UNINSTALLED = "RICE-" + "UNINSTALLED"

MATT_BLOCK = (
    "## Agent skills\n"
    "\n"
    "### Issue tracker\n"
    "\n"
    "Issues live in GitHub Issues. See `docs/agents/issue-tracker.md`.\n"
    "\n"
    "### Triage labels\n"
    "\n"
    "Default vocabulary. See `docs/agents/triage-labels.md`.\n"
    "\n"
    "### Domain docs\n"
    "\n"
    "Single-context. See `docs/agents/domain.md`.\n"
)
MATT_DOCS = {
    "docs/agents/issue-tracker.md": "# Issue tracker: GitHub\n",
    "docs/agents/triage-labels.md": "# Triage labels\n",
    "docs/agents/domain.md": "# Domain docs\n",
}

# The two project shapes. `files` are project-owned bytes; `answers` are the
# OWNER's two bootstrap choices for that repo.
PROJECTS = {
    "bare": {
        "files": {"README.md": "# demo\n", "src/app.py": "print('hello')\n"},
        "answers": ["--archive", "1", "--output-style", "rice:老弟"],
    },
    "rich": {
        "files": {
            "README.md": "# demo\n",
            "src/app.py": "print('hello')\n",
            "CLAUDE.md": "# Project notes\n\nRun the tests with `make test`.\n",
            "AGENTS.md": "# Agents\n\nNever commit generated files.\n",
            ".claude/settings.json": json.dumps({
                "permissions": {"allow": ["Bash(make test)"], "deny": ["Bash(rm -rf *)", "Bash(git rebase*)"]},
                "env": {"PROJECT_FLAG": "1"},
                "outputStyle": "Explanatory",
                "enabledPlugins": {"other@acme": True},
                "extraKnownMarketplaces": {"acme": {"source": {"source": "github", "repo": "acme/plugins"}}},
                "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "true"}]}]},
            }, indent=4) + "\n",
        },
        "answers": ["--archive", "0", "--output-style", "none"],
    },
}

_FAKE_CLAUDE = '''#!{python} -IB
import json, os, sys
if sys.argv[1:4] == ["plugin", "list", "--json"]:
    print(json.dumps([
        {{"id": "rice@rice-bootstrap", "version": os.environ["FAKE_RICE_VERSION"], "scope": "user",
          "enabled": True, "installPath": os.environ["FAKE_RICE_ROOT"]}},
        {{"id": "mattpocock-skills@mattpocock", "version": "1.3.1", "scope": "user",
          "enabled": True, "installPath": os.environ["FAKE_MATT_ROOT"]}},
    ]))
    sys.exit(0)
sys.stderr.write("upgrade-matrix stand-in: unsupported claude " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(2)
'''
_FAKE_RTK = '''#!{python} -IB
print("rtk 0.46.0 (upgrade-matrix stand-in)")
'''


# --------------------------------------------------------------------------
# the builds
# --------------------------------------------------------------------------

@dataclass
class Build:
    root: Path
    version: str

    @classmethod
    def at(cls, root: Path) -> "Build":
        data = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return cls(Path(root).resolve(), str(data.get("version", "?")))


def _run(cmd, *, cwd=None, env=None, stdin=None, timeout=600) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, f"timed out: {' '.join(map(str, cmd))}"


def _semver_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def releases(public_repo: str) -> tuple[list[str], str | None]:
    """(every `release/<semver>` on the public repository, oldest first,
    error). Read from the remote each time: the supported set is whatever
    has been published, never a list somebody keeps."""
    rc, out = _run(["git", "ls-remote", "--heads", public_repo, "refs/heads/release/*"], timeout=120)
    if rc != 0:
        return [], f"cannot list release branches of {public_repo}: {out.strip()[-300:]}"
    found = []
    for line in out.splitlines():
        ref = line.split()[-1] if line.split() else ""
        version = ref.removeprefix("refs/heads/release/")
        if ref.startswith("refs/heads/release/") and SEMVER.fullmatch(version):
            found.append(version)
    return sorted(found, key=_semver_key), None


def fetch_release(public_repo: str, version: str, dest: Path) -> str | None:
    """Check out exactly `release/<version>`'s tree into `dest` (which must
    not exist). None on success, else why not."""
    rc, out = _run(["git", "clone", "-q", "--depth", "1", "--single-branch", "--branch", f"release/{version}",
                    public_repo, str(dest)], timeout=300)
    if rc != 0:
        return f"cannot fetch release/{version}: {out.strip()[-300:]}"
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return None


# --------------------------------------------------------------------------
# one sandboxed repo, driven by one build at a time
# --------------------------------------------------------------------------

class Sandbox:
    """A project repo plus the environment a session would give a build:
    its own config dir (transcripts live there), its own HOME, and the
    probe stand-ins first on PATH."""

    def __init__(self, base: Path, tools: Path):
        self.base = base
        self.work = base / "work"
        self.cfg = base / "cfg"
        self.home = base / "home"
        self.tools = tools
        for d in (self.work, self.cfg, self.home):
            d.mkdir(parents=True, exist_ok=True)
        self._probes = 0

    def env(self, build: Build) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE", "RICE_", "GIT_", "PYTHON"))}
        env.update({
            "PATH": f"{self.tools / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "CLAUDE_CONFIG_DIR": str(self.cfg),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.tools / "gitconfig"),
            "FAKE_RICE_VERSION": build.version,
            "FAKE_RICE_ROOT": str(build.root),
            "FAKE_MATT_ROOT": str(self.tools / "matt"),
        })
        return env

    def git(self, *args) -> tuple[int, str]:
        return _run(["git", "-C", str(self.work), *args],
                    env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.tools / "gitconfig")})

    def deny_git_probe(self) -> None:
        """What the harness records when the model runs the verifier's git
        probe in a rice repo: a Bash tool_use and its permission denial, in
        this repo's transcript directory, after the latest settings change."""
        tdir = self.cfg / "projects" / str(self.work.resolve()).replace("/", "-")
        tdir.mkdir(parents=True, exist_ok=True)
        settings = self.work / ".claude" / "settings.json"
        when = max(time.time(), settings.stat().st_mtime if settings.exists() else 0.0) + 1.0
        ts = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self._probes += 1
        tid = f"matrix-probe-{self._probes}"
        records = [
            {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": GIT_DENY_PROBE}}]}},
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "Permission to use Bash has been denied."}]}},
        ]
        (tdir / f"{tid}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def bootstrap(self, build: Build, answers: list[str]) -> tuple[int, str]:
        """`/rice:bootstrap` as the skill runs it, including the one loop the
        lifecycle itself asks for: run the git probe when the verifier says
        it has no denial on record, then run the same command again."""
        rc, out = 1, ""
        for _ in range(3):
            rc, out = _run([sys.executable, "-I", "-B", str(build.root / "scripts" / "rice.py"), "bootstrap",
                            str(self.work), *answers], cwd=self.work, env=self.env(build))
            if "git-deny:behaves: NOT_VERIFIED" not in out:
                break
            self.deny_git_probe()
        return rc, out

    def stop_hook(self, build: Build) -> tuple[int, str]:
        payload = json.dumps({"cwd": str(self.work), "hook_event_name": "Stop", "stop_hook_active": False,
                              "session_id": "upgrade-matrix"})
        return _run([sys.executable, "-I", "-B", str(build.root / "scripts" / "bootstrap_continue.py")],
                    cwd=self.work, env=self.env(build), stdin=payload)

    def uninstall(self, build: Build) -> tuple[int, str]:
        return _run([sys.executable, "-I", "-B", str(build.root / "scripts" / "uninstall.py"), str(self.work)],
                    cwd=self.work, env=self.env(build))

    def snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.work)): p.read_bytes() for p in sorted(self.work.rglob("*"))
                if p.is_file() and ".git" not in p.relative_to(self.work).parts}

    def copy(self, base: Path) -> "Sandbox":
        twin = Sandbox(base, self.tools)
        shutil.rmtree(twin.work)
        shutil.copytree(self.work, twin.work, symlinks=True)
        shutil.rmtree(twin.cfg)
        shutil.copytree(self.cfg, twin.cfg, symlinks=True)
        return twin


def _tools(base: Path) -> Path:
    """The probe stand-ins, a git identity, and a Matt plugin root that
    ships `triage` (so Matt's real output -- triage labels included -- is
    what every build is asked to find)."""
    tools = base / "tools"
    (tools / "bin").mkdir(parents=True, exist_ok=True)
    for name, body in (("claude", _FAKE_CLAUDE), ("rtk", _FAKE_RTK)):
        p = tools / "bin" / name
        p.write_text(body.format(python=sys.executable), encoding="utf-8")
        p.chmod(0o755)
    (tools / "gitconfig").write_text("[user]\n\tname = upgrade matrix\n\temail = matrix@local\n"
                                     "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    triage = tools / "matt" / "skills" / "engineering" / "triage"
    triage.mkdir(parents=True, exist_ok=True)
    (triage / "SKILL.md").write_text("---\nname: triage\n---\n", encoding="utf-8")
    return tools


def _seed(sb: Sandbox, shape: str) -> None:
    for rel, text in PROJECTS[shape]["files"].items():
        p = sb.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    sb.git("init", "-q")
    sb.git("add", "-A")
    sb.git("commit", "-q", "-m", "project")


def _matt_setup(sb: Sandbox) -> None:
    """Matt's official setup, as it writes: its `## Agent skills` block
    appended to the CLAUDE.md that already exists, plus its docs."""
    claude_md = sb.work / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    if "## Agent skills" not in text:
        sep = "" if (not text or text.endswith("\n")) else "\n"
        claude_md.write_text(f"{text}{sep}\n{MATT_BLOCK}" if text else MATT_BLOCK, encoding="utf-8")
    for rel, body in MATT_DOCS.items():
        p = sb.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------
# the lifecycle, end to end, for one build
# --------------------------------------------------------------------------

@dataclass
class Finding:
    kind: str  # DATA / RESIDUE / BLOCKED
    text: str


@dataclass
class Row:
    source: str                  # "release/0.1.2" or "fresh"
    verdict: str = CLEAN
    findings: list = field(default_factory=list)
    shapes: dict = field(default_factory=dict)  # shape -> one-line summary

    def add(self, kind: str, text: str) -> None:
        self.findings.append(Finding(kind, text))
        if VERDICTS.index(kind) < VERDICTS.index(self.verdict):
            self.verdict = kind


def _install(build: Build, sb: Sandbox, shape: str) -> tuple[bool, str]:
    """A fresh install of `build` on the seeded project, Matt included,
    finished by that build's own Stop continuation. (ok, what happened)."""
    rc, out = sb.bootstrap(build, PROJECTS[shape]["answers"])
    if not (sb.work / MARKER).exists() and f"{FINAL} {build.version}" not in out:
        return False, f"rice {build.version} bootstrap did not reach the Matt hand-off: {_last(out)}"
    _matt_setup(sb)
    rc, out = sb.stop_hook(build)
    stamped = (sb.work / VERSION_FILE).read_text(encoding="utf-8").strip() if (sb.work / VERSION_FILE).exists() else None
    if stamped != build.version or (sb.work / MARKER).exists():
        return False, f"rice {build.version} Stop continuation did not finalize (rc={rc}): {_last(out)}"
    sb.git("add", "-A")
    sb.git("commit", "-q", "-m", f"rice {build.version} bootstrap + Matt")
    return True, f"{FINAL} {build.version}"


def _last(out: str) -> str:
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return lines[-1][:240] if lines else "(no output)"


# --------------------------------------------------------------------------
# comparison: what the project owns, and what a fresh install looks like
# --------------------------------------------------------------------------

def _settings(snap: dict) -> dict | None:
    raw = snap.get(".claude/settings.json")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"<unreadable settings.json>": raw.decode("utf-8", "replace")}
    return data if isinstance(data, dict) else {"<non-object settings.json>": data}


def _leaves(d, prefix: str = "") -> dict:
    """{dotted path: value}; a list is one value, a nested empty dict is a
    leaf (a container somebody left behind), the top-level {} is nothing."""
    if isinstance(d, dict) and (d or not prefix):
        out = {}
        for k, v in d.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else k))
        return out
    return {prefix: d}


_ABSENT = object()


def _nonblank(text: str | None) -> list[str]:
    return [l for l in (text or "").splitlines() if l.strip()]


def _is_subsequence(needle: list, hay: list) -> bool:
    it = iter(hay)
    return all(any(x == y for y in it) for x in needle)


def _project(shape: str) -> dict:
    """What the project owns once Matt has run: its own files, Matt's docs,
    its settings, and the lines of CLAUDE.md / AGENTS.md that are not rice's."""
    files = dict(PROJECTS[shape]["files"])
    settings = json.loads(files.pop(".claude/settings.json")) if ".claude/settings.json" in files else None
    claude = files.pop("CLAUDE.md", None)
    agents = files.pop("AGENTS.md", None)
    files.update(MATT_DOCS)
    return {"files": {k: v.encode("utf-8") for k, v in files.items()}, "settings": settings,
            "claude_md": _nonblank(claude) + _nonblank(MATT_BLOCK), "agents_md": _nonblank(agents)}


def _project_survived(snap: dict, project: dict, row: Row, where: str) -> None:
    for rel, data in project["files"].items():
        if snap.get(rel) != data:
            row.add(DATA, f"{where}: project file {rel} changed or missing")
    have = _leaves(_settings(snap) or {})
    for path, value in _leaves(project["settings"] or {}).items():
        if path == "permissions.deny":
            got = have.get(path)
            got = got if isinstance(got, list) else []
            if Counter(value) - Counter(got) or not _is_subsequence(value, got):
                row.add(DATA, f"{where}: project deny rules not all kept in order ({value} vs {got})")
        elif have.get(path, _ABSENT) != value:
            row.add(DATA, f"{where}: project setting {path}={value!r} changed or missing")
    for rel, key in (("CLAUDE.md", "claude_md"), ("AGENTS.md", "agents_md")):
        lines = _nonblank(snap.get(rel, b"").decode("utf-8", "replace"))
        if not _is_subsequence(project[key], lines):
            row.add(DATA, f"{where}: project lines of {rel} not all kept in order")


def _converged(up: dict, fresh: dict, row: Row, where: str) -> None:
    """The upgraded repo must BE a fresh install: same files, same bytes,
    same settings (compared as JSON -- rice writes it, key order is not
    meaning). Anything else is state an older rice left that the candidate
    did not take back, or state the candidate failed to bring."""
    for rel in sorted(set(up) | set(fresh)):
        a, b = up.get(rel), fresh.get(rel)
        if a == b:
            continue
        if rel == ".claude/settings.json":
            ua, fb = _leaves(_settings(up) or {}), _leaves(_settings(fresh) or {})
            for path in sorted(set(ua) | set(fb)):
                if path not in fb:
                    row.add(RESIDUE, f"{where}: settings {path}={ua[path]!r} left behind (a fresh install has no such key)")
                elif path not in ua:
                    row.add(RESIDUE, f"{where}: settings {path} missing (a fresh install has {fb[path]!r})")
                elif ua[path] != fb[path]:
                    row.add(RESIDUE, f"{where}: settings {path} is {ua[path]!r}, a fresh install has {fb[path]!r}")
        elif b is None:
            row.add(RESIDUE, f"{where}: {rel} left behind (a fresh install has no such file)")
        elif a is None:
            row.add(RESIDUE, f"{where}: {rel} missing (a fresh install has it)")
        else:
            row.add(RESIDUE, f"{where}: {rel} differs from a fresh install")


def _only_the_project(snap: dict, project: dict, row: Row, where: str) -> None:
    """After uninstall, exactly the project and nothing of rice's."""
    for rel in sorted(set(snap) - set(project["files"]) - {"CLAUDE.md", "AGENTS.md", ".claude/settings.json"}):
        row.add(RESIDUE, f"{where}: {rel} is still there")
    mine = _leaves(project["settings"] or {})
    left = {p: v for p, v in _leaves(_settings(snap) or {}).items() if mine.get(p, _ABSENT) != v}
    if left:
        row.add(RESIDUE, f"{where}: settings not back to the project's own: {left}")
    for rel, key in (("CLAUDE.md", "claude_md"), ("AGENTS.md", "agents_md")):
        lines = _nonblank(snap.get(rel, b"").decode("utf-8", "replace"))
        if lines != project[key]:
            extra = [l for l in lines if l not in project[key]]
            if extra:
                row.add(RESIDUE, f"{where}: {rel} still carries {extra}")


# --------------------------------------------------------------------------
# the matrix
# --------------------------------------------------------------------------

def _fresh(candidate: Build, base: Path, tools: Path, shape: str) -> tuple[Sandbox | None, str]:
    sb = Sandbox(base, tools)
    _seed(sb, shape)
    ok, what = _install(candidate, sb, shape)
    return (sb if ok else None), what


def _check_row(row: Row, candidate: Build, shape: str, subject: Sandbox, fresh: Sandbox, base: Path,
               upgraded: bool) -> None:
    project = _project(shape)
    snap = subject.snapshot()
    stamped = snap.get(VERSION_FILE, b"").decode().strip()
    if stamped != candidate.version or MARKER in snap:
        row.add(BLOCKED, f"{shape}: not {FINAL} {candidate.version} (verified version {stamped or 'absent'})")
    _project_survived(snap, project, row, f"{shape}")
    if upgraded:
        _converged(snap, fresh.snapshot(), row, f"{shape}")
    gone = subject.copy(base / "uninstalled")
    rc, out = gone.uninstall(candidate)
    if _last(out) != UNINSTALLED:
        row.add(RESIDUE, f"{shape}: uninstall: {_last(out)}")
    after = gone.snapshot()
    _project_survived(after, project, row, f"{shape} after uninstall")
    _only_the_project(after, project, row, f"{shape} after uninstall")


def run(candidate_root: Path, public_repo: str = PUBLIC_REPO, workdir: Path | None = None) -> tuple[list[Row], str | None]:
    """(one Row per published release plus `fresh`, error). `error` means
    the matrix could not even be assembled -- treat as not clean."""
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="rice-upgrade-matrix-"))
    workdir.mkdir(parents=True, exist_ok=True)
    tools = _tools(workdir)
    # the candidate runs from its own copy: nothing the matrix does can touch what ships
    cand_root = workdir / "candidate"
    shutil.copytree(candidate_root, cand_root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    candidate = Build.at(cand_root)
    versions, err = releases(public_repo)
    if err:
        return [], err

    rows: list[Row] = []
    fresh_row = Row("fresh")
    fresh: dict[str, Sandbox] = {}
    for shape in PROJECTS:
        sb, what = _fresh(candidate, workdir / f"fresh-{shape}", tools, shape)
        fresh_row.shapes[shape] = what
        if sb is None:
            fresh_row.add(BLOCKED, f"{shape}: {what}")
            continue
        fresh[shape] = sb
        _check_row(fresh_row, candidate, shape, sb, sb, workdir / f"fresh-{shape}-check", upgraded=False)

    for version in versions:
        row = Row(f"release/{version}")
        rows.append(row)
        old_root = workdir / "releases" / version
        problem = fetch_release(public_repo, version, old_root)
        if problem:
            row.add(BLOCKED, problem)
            continue
        old = Build.at(old_root)
        for shape in PROJECTS:
            base = workdir / f"{version}-{shape}"
            sb = Sandbox(base, tools)
            _seed(sb, shape)
            ok, what = _install(old, sb, shape)
            if not ok:
                row.add(BLOCKED, f"{shape}: {what}")
                continue
            rc, out = sb.bootstrap(candidate, [])
            final = f"{FINAL} {candidate.version}" in out
            row.shapes[shape] = f"{what} -> {f'{FINAL} {candidate.version}' if final else _last(out)}"
            if shape not in fresh:
                row.add(BLOCKED, f"{shape}: no fresh candidate install to compare with")
                continue
            before = len([f for f in row.findings if f.kind == RESIDUE])
            _check_row(row, candidate, shape, sb, fresh[shape], base / "check", upgraded=True)
            if final and any(f.kind == RESIDUE for f in row.findings[before:]):
                row.add(RESIDUE, f"{shape}: the candidate said {FINAL} with this residue present "
                                 "-- a verifier/migration defect")
    return [fresh_row] + rows, None


def render(rows: list[Row], error: str | None = None) -> str:
    if error:
        return f"UPGRADE-MATRIX NOT CLEAN -- {error}"
    lines = [f"{'source':<16} {'verdict':<22} lifecycle"]
    for r in rows:
        lines.append(f"{r.source:<16} {r.verdict:<22} " + "; ".join(f"{s}: {w}" for s, w in r.shapes.items()))
        lines.extend(f"    - [{f.kind}] {f.text}" for f in r.findings)
    bad = [r for r in rows if r.verdict != CLEAN]
    lines.append(f"UPGRADE-MATRIX NOT CLEAN ({len(bad)} of {len(rows)} rows)" if bad or not rows
                 else f"UPGRADE-MATRIX CLEAN ({len(rows)} rows)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="every published rice release, upgraded to a candidate build")
    ap.add_argument("--candidate", default=str(ROOT), help="the candidate build's root (default: this repo)")
    ap.add_argument("--public-repo", default=PUBLIC_REPO)
    ap.add_argument("--workdir", help="keep the sandboxes here (default: a temp dir)")
    args = ap.parse_args(argv)
    rows, err = run(Path(args.candidate), args.public_repo, Path(args.workdir) if args.workdir else None)
    print(render(rows, err))
    return 0 if not err and rows and all(r.verdict == CLEAN for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
