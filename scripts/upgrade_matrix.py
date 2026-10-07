#!/usr/bin/env python3
"""rice upgrade matrix (RICE-UPGRADE-MATRIX-001): every published release,
upgraded to the candidate through the real lifecycle, must converge to
exactly what a fresh install of the candidate produces -- and uninstalling
either must leave nothing but the project.

Private release tooling, like release.py: never exported, never shipped.
It only ever READS other builds' verdicts, so -- as in release.py -- the
verifier's and uninstall's words are assembled at runtime here, keeping the
"only verify.py / uninstall.py say these" greps honest. Every path and
value another module owns (the verified-version file, the marker, the deny
path, the release branch prefix, SemVer, Matt's artifacts) is read from
that module.

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
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence  # noqa: E402
import export_public  # noqa: E402
import reconcile  # noqa: E402
import settings_template  # noqa: E402
from verify import VERSION_FILE  # noqa: E402  -- read only, to see what another build stamped

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_REPO = f"https://github.com/{export_public.MARKETPLACE_REPO}.git"
VERDICTS = ("PROJECT DATA CHANGED", "LEGACY RESIDUE", "BLOCKED", "CLEAN")  # worst first
DATA, RESIDUE, BLOCKED, CLEAN = VERDICTS

MARKER = f".claude/{reconcile.MARKER_NAME}"
SETTINGS = ".claude/settings.json"
SHARED = ("CLAUDE.md", "AGENTS.md")  # files rice and the project both write into
FINAL = "FINAL-" + "VERIFIED"
UNINSTALLED = "RICE-" + "UNINSTALLED"

MATT_BLOCK = (
    f"{evidence.MATT_HEADING}\n"
    "\n"
    "### Issue tracker\n"
    "\n"
    "Issues live in GitHub Issues. See `docs/agents/issue-tracker.md`.\n"
    "\n"
    f"{evidence.MATT_TRIAGE_HEADING}\n"
    "\n"
    "Default vocabulary. See `docs/agents/triage-labels.md`.\n"
    "\n"
    "### Domain docs\n"
    "\n"
    "Single-context. See `docs/agents/domain.md`.\n"
)
MATT_DOCS = {rel: f"# {Path(rel).stem}\n" for rel in (*evidence.MATT_REQUIRED_DOCS, evidence.MATT_TRIAGE_DOC)}

# The two project shapes. `files` are what the project has before rice;
# `answers` are the OWNER's two bootstrap choices for that repo.
PROJECTS = {
    "bare": {
        "files": {"README.md": "# demo\n", "src/app.py": "print('hello')\n"},
        "answers": ["--archive", "1", "--output-style", settings_template.RICE_STYLE],
    },
    "rich": {
        "files": {
            "README.md": "# demo\n",
            "src/app.py": "print('hello')\n",
            "CLAUDE.md": "# Project notes\n\nRun the tests with `make test`.\n",
            "AGENTS.md": "# Agents\n\nNever commit generated files.\n",
            SETTINGS: json.dumps({
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


def releases(public_repo: str) -> tuple[list[str], str | None]:
    """(every `release/<semver>` on the public repository, oldest first,
    error). Read from the remote each time: the supported set is whatever
    has been published, never a list somebody keeps."""
    prefix = f"refs/heads/{settings_template.RELEASE_BRANCH_PREFIX}"
    rc, out = _run(["git", "ls-remote", "--heads", public_repo, f"{prefix}*"], timeout=120)
    if rc != 0:
        return [], f"cannot list release branches of {public_repo}: {out.strip()[-300:]}"
    refs = [line.split()[-1] for line in out.splitlines() if line.split()]
    versions = [ref[len(prefix):] for ref in refs if ref.startswith(prefix)]
    return sorted((v for v in versions if settings_template.semver(v)), key=settings_template.semver), None


def fetch_release(public_repo: str, version: str, dest: Path) -> str | None:
    """Check out exactly that release branch's tree into `dest` (which must
    not exist). None on success, else why not."""
    branch = settings_template.release_ref(version)
    rc, out = _run(["git", "clone", "-q", "--depth", "1", "--single-branch", "--branch", branch,
                    public_repo, str(dest)], timeout=300)
    if rc != 0:
        return f"cannot fetch {branch}: {out.strip()[-300:]}"
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
        settings = self.work / SETTINGS
        when = max(time.time(), settings.stat().st_mtime if settings.exists() else 0.0) + 1.0
        ts = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        self._probes += 1
        tid = f"matrix-probe-{self._probes}"
        records = [
            {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": evidence.GIT_DENY_PROBE}}]}},
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "Permission to use Bash has been denied."}]}},
        ]
        (tdir / f"{tid}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def bootstrap(self, build: Build, answers: list[str]) -> str:
        """`/rice:bootstrap` as the skill runs it, including the one loop the
        lifecycle itself asks for: run the git probe when the verifier says
        it has no denial on record, then run the same command again. Returns
        the last run's output."""
        out = ""
        for _ in range(3):
            _, out = _run([sys.executable, "-I", "-B", str(build.root / "scripts" / "rice.py"), "bootstrap",
                           str(self.work), *answers], cwd=self.work, env=self.env(build))
            if "git-deny:behaves: NOT_VERIFIED" not in out:
                break
            self.deny_git_probe()
        return out

    def stop_hook(self, build: Build) -> str:
        payload = json.dumps({"cwd": str(self.work), "hook_event_name": "Stop", "stop_hook_active": False,
                              "session_id": "upgrade-matrix"})
        return _run([sys.executable, "-I", "-B", str(build.root / "scripts" / "bootstrap_continue.py")],
                    cwd=self.work, env=self.env(build), stdin=payload)[1]

    def uninstall(self, build: Build) -> str:
        return _run([sys.executable, "-I", "-B", str(build.root / "scripts" / "uninstall.py"), str(self.work)],
                    cwd=self.work, env=self.env(build))[1]

    def final(self, build: Build) -> bool:
        """Did `build` finish this repo: its version stamped, no bootstrap waiting."""
        stamped = self.work / VERSION_FILE
        return (stamped.exists() and stamped.read_text(encoding="utf-8").strip() == build.version
                and not (self.work / MARKER).exists())

    def snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.work)): p.read_bytes() for p in sorted(self.work.rglob("*"))
                if p.is_file() and ".git" not in p.relative_to(self.work).parts}

    def copy(self, base: Path) -> "Sandbox":
        twin = Sandbox(base, self.tools)
        shutil.rmtree(twin.work)
        shutil.copytree(self.work, twin.work, symlinks=True)
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
    """Matt's official setup, as it writes: its block appended to the
    CLAUDE.md that already exists, plus its docs."""
    claude_md = sb.work / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    if evidence.MATT_HEADING not in text:
        sep = "" if (not text or text.endswith("\n")) else "\n"
        claude_md.write_text(f"{text}{sep}\n{MATT_BLOCK}" if text else MATT_BLOCK, encoding="utf-8")
    for rel, body in MATT_DOCS.items():
        p = sb.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def _install(build: Build, sb: Sandbox, shape: str) -> tuple[bool, str]:
    """A fresh install of `build` on the seeded project, Matt included,
    finished by that build's own Stop continuation. (ok, what happened)."""
    out = sb.bootstrap(build, PROJECTS[shape]["answers"])
    if not (sb.work / MARKER).exists():
        return False, f"rice {build.version} bootstrap did not reach the Matt hand-off: {_last(out)}"
    _matt_setup(sb)
    out = sb.stop_hook(build)
    if not sb.final(build):
        return False, f"rice {build.version} Stop continuation did not finalize: {_last(out)}"
    sb.git("add", "-A")
    sb.git("commit", "-q", "-m", f"rice {build.version} bootstrap + Matt")
    return True, f"{FINAL} {build.version}"


def _last(out: str) -> str:
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return lines[-1][:240] if lines else "(no output)"


# --------------------------------------------------------------------------
# what the project owns, and how a repo is compared with it
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Project:
    """What the project owns once Matt has run."""
    files: dict     # rel -> bytes: the project's own files and Matt's docs
    settings: dict  # the project's own settings.json ({} when it has none)
    lines: dict     # SHARED file -> its project-owned non-blank lines, in order

    @classmethod
    def of(cls, shape: str) -> "Project":
        files = dict(PROJECTS[shape]["files"])
        settings = json.loads(files.pop(SETTINGS)) if SETTINGS in files else {}
        shared = {rel: _nonblank(files.pop(rel, None)) for rel in SHARED}
        shared["CLAUDE.md"] += _nonblank(MATT_BLOCK)
        files.update(MATT_DOCS)
        return cls({k: v.encode("utf-8") for k, v in files.items()}, settings, shared)


@dataclass
class Finding:
    kind: str  # DATA / RESIDUE / BLOCKED
    text: str


_ABSENT = object()


def _settings(snap: dict) -> dict:
    raw = snap.get(SETTINGS)
    if raw is None:
        return {}
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


def _nonblank(text: str | None) -> list[str]:
    return [l for l in (text or "").splitlines() if l.strip()]


def _lines(snap: dict, rel: str) -> list[str]:
    return _nonblank(snap.get(rel, b"").decode("utf-8", "replace"))


def _in_order(needle: list, hay: list) -> bool:
    """Every item of `needle`, each matched by its own later item of `hay`."""
    it = iter(hay)
    return all(any(x == y for y in it) for x in needle)


def _differences(a: dict, b: dict):
    """(key, a's value, b's value) wherever they disagree; _ABSENT when missing."""
    for key in sorted(set(a) | set(b), key=str):
        x, y = a.get(key, _ABSENT), b.get(key, _ABSENT)
        if x != y:
            yield key, x, y


def _project_survived(snap: dict, project: Project, where: str) -> list[Finding]:
    found = [Finding(DATA, f"{where}: project file {rel} changed or missing")
             for rel, data in project.files.items() if snap.get(rel) != data]
    have = _leaves(_settings(snap))
    for path, value in _leaves(project.settings).items():
        got = have.get(path, _ABSENT)
        if path == settings_template.DENY_PATH:
            if not (isinstance(got, list) and _in_order(value, got)):
                found.append(Finding(DATA, f"{where}: project deny rules not all kept in order ({value} vs {got})"))
        elif got != value:
            found.append(Finding(DATA, f"{where}: project setting {path}={value!r} changed or missing"))
    found += [Finding(DATA, f"{where}: project lines of {rel} not all kept in order")
              for rel in SHARED if not _in_order(project.lines[rel], _lines(snap, rel))]
    return found


def _converged(up: dict, fresh: dict, where: str) -> list[Finding]:
    """The upgraded repo must BE a fresh install: same files, same bytes,
    same settings (compared as JSON -- rice writes it, key order is not
    meaning). Anything else is state an older rice left that the candidate
    did not take back, or state the candidate failed to bring."""
    found = []
    for rel, a, b in _differences({k: v for k, v in up.items() if k != SETTINGS},
                                  {k: v for k, v in fresh.items() if k != SETTINGS}):
        found.append(Finding(RESIDUE, f"{where}: {rel} " + (
            "left behind (a fresh install has no such file)" if b is _ABSENT else
            "missing (a fresh install has it)" if a is _ABSENT else "differs from a fresh install")))
    for path, a, b in _differences(_leaves(_settings(up)), _leaves(_settings(fresh))):
        found.append(Finding(RESIDUE, f"{where}: settings {path}" + (
            f"={a!r} left behind (a fresh install has no such key)" if b is _ABSENT else
            f" missing (a fresh install has {b!r})" if a is _ABSENT else
            f" is {a!r}, a fresh install has {b!r}")))
    return found


def _only_the_project(snap: dict, project: Project, where: str) -> list[Finding]:
    """After uninstall, exactly the project and nothing of rice's."""
    found = [Finding(RESIDUE, f"{where}: {rel} is still there")
             for rel in sorted(set(snap) - set(project.files) - set(SHARED) - {SETTINGS})]
    left = {path: a for path, a, _ in _differences(_leaves(_settings(snap)), _leaves(project.settings))
            if a is not _ABSENT}
    if left:
        found.append(Finding(RESIDUE, f"{where}: settings not back to the project's own: {left}"))
    for rel in SHARED:
        extra = [l for l in _lines(snap, rel) if l not in project.lines[rel]]
        if extra:
            found.append(Finding(RESIDUE, f"{where}: {rel} still carries {extra}"))
    return found


def _check(candidate: Build, shape: str, subject: Sandbox, fresh: Sandbox | None, base: Path) -> list[Finding]:
    """Everything wrong with `subject` once the candidate is done with it.
    `fresh` is the fresh candidate install to compare an upgraded repo
    with; None when `subject` is that fresh install."""
    project = Project.of(shape)
    found = []
    final = subject.final(candidate)
    if not final:
        found.append(Finding(BLOCKED, f"{shape}: the candidate did not reach {FINAL} {candidate.version}"))
    snap = subject.snapshot()
    found += _project_survived(snap, project, shape)
    if fresh is not None:
        drift = _converged(snap, fresh.snapshot(), shape)
        found += drift
        if final and drift:
            found.append(Finding(RESIDUE, f"{shape}: the candidate said {FINAL} with this residue present "
                                          "-- a verifier/migration defect"))
    gone = subject.copy(base / "uninstalled")
    out = gone.uninstall(candidate)
    if _last(out) != UNINSTALLED:
        found.append(Finding(RESIDUE, f"{shape}: uninstall: {_last(out)}"))
    after = gone.snapshot()
    found += _project_survived(after, project, f"{shape} after uninstall")
    found += _only_the_project(after, project, f"{shape} after uninstall")
    return found


# --------------------------------------------------------------------------
# the matrix
# --------------------------------------------------------------------------

@dataclass
class Row:
    source: str                                     # "release/0.1.2" or "fresh"
    findings: list = field(default_factory=list)
    lifecycle: dict = field(default_factory=dict)   # shape -> what each build's lifecycle reached

    @property
    def verdict(self) -> str:
        return min((f.kind for f in self.findings), key=VERDICTS.index, default=CLEAN)


def clean(rows: list[Row]) -> bool:
    """The matrix's one word: there are rows, and every one is CLEAN."""
    return bool(rows) and all(r.verdict == CLEAN for r in rows)


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

    fresh_row, fresh = Row("fresh"), {}
    for shape in PROJECTS:
        sb = Sandbox(workdir / f"fresh-{shape}", tools)
        _seed(sb, shape)
        ok, what = _install(candidate, sb, shape)
        fresh_row.lifecycle[shape] = what
        if not ok:
            fresh_row.findings.append(Finding(BLOCKED, f"{shape}: {what}"))
            continue
        fresh[shape] = sb
        fresh_row.findings += _check(candidate, shape, sb, None, workdir / f"fresh-{shape}-check")

    rows = [fresh_row]
    for version in versions:
        row = Row(settings_template.release_ref(version))
        rows.append(row)
        old_root = workdir / "releases" / version
        problem = fetch_release(public_repo, version, old_root)
        if problem:
            row.findings.append(Finding(BLOCKED, problem))
            continue
        old = Build.at(old_root)
        for shape in PROJECTS:
            base = workdir / f"{version}-{shape}"
            sb = Sandbox(base, tools)
            _seed(sb, shape)
            ok, what = _install(old, sb, shape)
            if not ok:
                row.lifecycle[shape] = what
                row.findings.append(Finding(BLOCKED, f"{shape}: {what}"))
                continue
            out = sb.bootstrap(candidate, [])
            row.lifecycle[shape] = f"{what} -> " + (f"{FINAL} {candidate.version}" if sb.final(candidate)
                                                    else _last(out))
            if shape not in fresh:
                row.findings.append(Finding(BLOCKED, f"{shape}: no fresh candidate install to compare with"))
                continue
            row.findings += _check(candidate, shape, sb, fresh[shape], base / "check")
    return rows, None


def render(rows: list[Row], error: str | None = None) -> str:
    if error:
        return f"UPGRADE-MATRIX NOT CLEAN -- {error}"
    lines = [f"{'source':<16} {'verdict':<22} lifecycle"]
    for r in rows:
        lines.append(f"{r.source:<16} {r.verdict:<22} " + "; ".join(f"{s}: {w}" for s, w in r.lifecycle.items()))
        lines.extend(f"    - [{f.kind}] {f.text}" for f in r.findings)
    bad = [r for r in rows if r.verdict != CLEAN]
    lines.append(f"UPGRADE-MATRIX CLEAN ({len(rows)} rows)" if clean(rows)
                 else f"UPGRADE-MATRIX NOT CLEAN ({len(bad)} of {len(rows)} rows)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="every published rice release, upgraded to a candidate build")
    ap.add_argument("--candidate", default=str(ROOT), help="the candidate build's root (default: this repo)")
    ap.add_argument("--public-repo", default=PUBLIC_REPO)
    ap.add_argument("--workdir", help="keep the sandboxes here (default: a temp dir)")
    args = ap.parse_args(argv)
    rows, err = run(Path(args.candidate), args.public_repo, Path(args.workdir) if args.workdir else None)
    print(render(rows, err))
    return 0 if not err and clean(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
