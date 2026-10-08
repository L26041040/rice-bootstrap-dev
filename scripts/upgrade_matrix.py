#!/usr/bin/env python3
"""rice upgrade matrix (RICE-UPGRADE-MATRIX-001, RICE-UPGRADE-PATH-001): every
published release, upgraded to the candidate through the real lifecycle --
directly, and along the path a long-lived repo took through every later
release -- must converge to exactly what a fresh install of the candidate
produces, and uninstalling either must leave nothing but the project.

Private release tooling, like release.py: never exported, never shipped.
It only ever READS other builds' verdicts, so -- as in release.py -- the
verifier's and uninstall's words are assembled at runtime here, keeping the
"only verify.py / uninstall.py say these" greps honest. Every path and
value another module owns (the verified-version file, the marker, the deny
path, the release branch prefix, SemVer, Matt's artifacts) is read from
that module.

Rows. The public `release/<semver>` branches are enumerated from the public
repository at run time, never kept as a list here. (Should the supported
range ever need a floor, `releases()` is the one place it would go; there
is none today.) For every release there is a **direct** row, release ->
candidate; for every release but the newest there is also a **historical**
row, release -> each later release in order -> candidate: the path a
long-lived repo actually took, each hop run by that release's own build.
Plus `fresh`: the candidate alone.

Each row runs twice, on two project shapes: `bare` (a repo with no Claude
configuration) and `rich` (project-owned settings, CLAUDE.md, AGENTS.md
content, a project deny rule identical to one of rice's, other plugins).

  1. The first release bootstraps the project with ITS OWN shipped CLI and
     Stop hook, the way the OWNER's sessions ran it: `rice.py bootstrap`,
     the harness-denied git probe it asks for, Matt's setup, the Stop
     continuation stamping that release's final verdict, a commit.
  2. Each later release on the path, and finally the candidate, re-runs its
     own `rice.py bootstrap` with no OWNER answers, as the README's update
     path says (I15), and must verify final before the next hop.
  3. The result is compared with a fresh candidate install of the same
     project; the candidate's bootstrap is run once more and must change
     nothing; both are uninstalled with the candidate's own uninstall and
     compared with the project.

The only stand-ins are the two external probes every build makes --
`claude plugin list --json` and `rtk --version` -- answered by tiny
executables first on PATH, so every build runs unmodified.

Verdict per row, worst first; the first three block a release:

  PROJECT DATA CHANGED   a project-owned byte or setting did not survive
  HARMFUL RESIDUE        the upgraded repo differs from a fresh install, or
                         uninstall leaves rice state behind -- including when
                         the candidate still verified final (a verifier or
                         migration defect, never a success)
  BLOCKED                a lifecycle step did not reach its verdict
  BENIGN LEGACY RESIDUE  only the differences `BENIGN_HUSKS` names, and only
                         when they were already there when the candidate
                         arrived: a known, inert leftover whose ownership no
                         build can prove, recorded as such in RICE-SPEC-001
                         §15 -- reported, never "fixed" by guessing, and not
                         a blocker. The same object emptied by the candidate
                         itself is the candidate's to remove: harmful.
  CLEAN                  none of the above
"""
import argparse
import json
import math
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
VERDICTS = ("PROJECT DATA CHANGED", "HARMFUL RESIDUE", "BLOCKED", "BENIGN LEGACY RESIDUE", "CLEAN")  # worst first
DATA, RESIDUE, BLOCKED, BENIGN, CLEAN = VERDICTS
BLOCKING = VERDICTS[:3]

# The one leftover classified benign, exactly: an EMPTY object at one of the
# two containers 0.1.2/0.1.3 created for their project-scope declaration (the
# containers of reconcile's legacy paths) that 0.1.4-0.1.6 emptied -- but did
# not remove -- when they migrated that declaration away. An empty object
# there has no effect, and nothing distinguishes it from one the project wrote
# itself, so no build may delete it (RICE-SPEC-001 §15, known limits). It is
# benign only when it was already empty when the candidate arrived
# (`_leftover` checks the repo as the candidate found it): an object the
# candidate's own migration emptied is the candidate's to remove, and leaving
# it is harmful.
BENIGN_HUSKS = frozenset(path.split(".")[0] for path in
                         (reconcile.LEGACY_MARKETPLACE_PATH, reconcile.LEGACY_ENABLED_PLUGIN_PATH))

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
                # "sonnet" is the value pre-release rice once wrote here: the
                # project's all the same (RICE-OWNERSHIP-FIX-001)
                "env": {"PROJECT_FLAG": "1", "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"},
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
        this repo's transcript directory, dated now -- never ahead of the
        clock, so a later hop that rewrites settings.json has to ask for the
        probe again, exactly as a real session would (the record's
        millisecond is rounded up only so it is not older than a settings
        write made in the same millisecond)."""
        tdir = self.cfg / "projects" / str(self.work.resolve()).replace("/", "-")
        tdir.mkdir(parents=True, exist_ok=True)
        settings = self.work / SETTINGS
        when = max(time.time(), settings.stat().st_mtime if settings.exists() else 0.0)
        when = math.ceil(when * 1000) / 1000
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

    def finished(self, build: Build, out: str) -> bool:
        """Did `build` just finish this repo: its own run said so (its final
        verdict is in `out` -- so a build that did nothing is never credited
        with a stamp an earlier hop of the same version left), its version is
        stamped, and no bootstrap is waiting."""
        stamped = self.work / VERSION_FILE
        return (f"{FINAL} {build.version}" in out
                and stamped.exists() and stamped.read_text(encoding="utf-8").strip() == build.version
                and not (self.work / MARKER).exists())

    def settings(self) -> dict:
        return _settings(self.snapshot())

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
    if not sb.finished(build, out):
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
    kind: str  # one of VERDICTS but CLEAN
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


def _leftover(where: str, path: str, value, before: dict) -> Finding:
    """A settings value the candidate left where a fresh install has none.
    Benign only when it is exactly a `BENIGN_HUSKS` entry AND `before` -- the
    settings as the candidate found them -- already held it that way: then
    no build can prove it rice's. Emptied by the candidate itself, it is the
    candidate's to remove."""
    if path in BENIGN_HUSKS and value == {} and before.get(path, _ABSENT) == value:
        return Finding(BENIGN, f"{where}: settings {path}={value!r} left behind -- already empty when the "
                               "candidate arrived (an earlier release's migration left it; SPEC-001 §15)")
    return Finding(RESIDUE, f"{where}: settings {path}={value!r} left behind (a fresh install has no such key)")


def _converged(up: dict, fresh: dict, where: str, before: dict) -> list[Finding]:
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
        if b is _ABSENT:
            found.append(_leftover(where, path, a, before))
        else:
            found.append(Finding(RESIDUE, f"{where}: settings {path}" + (
                f" missing (a fresh install has {b!r})" if a is _ABSENT else
                f" is {a!r}, a fresh install has {b!r}")))
    return found


def _only_the_project(snap: dict, project: Project, where: str, before: dict) -> list[Finding]:
    """After uninstall, exactly the project and nothing of rice's."""
    found = [Finding(RESIDUE, f"{where}: {rel} is still there")
             for rel in sorted(set(snap) - set(project.files) - set(SHARED) - {SETTINGS})]
    for path, a, b in _differences(_leaves(_settings(snap)), _leaves(project.settings)):
        if a is _ABSENT:
            continue  # the project's own key, missing: _project_survived has reported it
        found.append(_leftover(where, path, a, before) if b is _ABSENT else
                     Finding(RESIDUE, f"{where}: settings {path} is {a!r}, the project's own is {b!r}"))
    for rel in SHARED:
        extra = [l for l in _lines(snap, rel) if l not in project.lines[rel]]
        if extra:
            found.append(Finding(RESIDUE, f"{where}: {rel} still carries {extra}"))
    return found


def _check(candidate: Build, shape: str, subject: Sandbox, fresh: Sandbox | None, base: Path,
           before: dict) -> list[Finding]:
    """Everything wrong with `subject` once the candidate has finished it.
    `fresh` is the fresh candidate install to compare an upgraded repo with
    (None when `subject` is that fresh install); `before` is the settings
    as the candidate found them, which decides what may count as benign."""
    project = Project.of(shape)
    before = _leaves(before)
    snap = subject.snapshot()
    found = _project_survived(snap, project, shape)
    if fresh is not None:
        drift = _converged(snap, fresh.snapshot(), shape, before)
        found += drift
        if any(f.kind == RESIDUE for f in drift):
            found.append(Finding(RESIDUE, f"{shape}: the candidate said {FINAL} with harmful residue present "
                                          "-- a verifier/migration defect"))
    again = subject.copy(base / "again")
    out = again.bootstrap(candidate, [])
    if not again.finished(candidate, out):
        found.append(Finding(BLOCKED, f"{shape}: a second bootstrap did not reach {FINAL}: {_last(out)}"))
    changed = [rel for rel, _, _ in _differences(again.snapshot(), snap)]
    if changed:
        found.append(Finding(RESIDUE, f"{shape}: a second bootstrap changed {changed} -- a re-run must be a no-op"))
    gone = subject.copy(base / "uninstalled")
    out = gone.uninstall(candidate)
    if _last(out) != UNINSTALLED:
        found.append(Finding(RESIDUE, f"{shape}: uninstall: {_last(out)}"))
    after = gone.snapshot()
    found += _project_survived(after, project, f"{shape} after uninstall")
    found += _only_the_project(after, project, f"{shape} after uninstall", before)
    return found


# --------------------------------------------------------------------------
# the matrix
# --------------------------------------------------------------------------

@dataclass
class Row:
    source: str                                     # "fresh", "release/0.1.6", or a path "release/0.1.2 -> 0.1.3 -> ..."
    findings: list = field(default_factory=list)
    lifecycle: dict = field(default_factory=dict)   # shape -> the verdicts the builds on the path reached, in order

    @property
    def verdict(self) -> str:
        return min((f.kind for f in self.findings), key=VERDICTS.index, default=CLEAN)

    @property
    def blocks(self) -> bool:
        return self.verdict in BLOCKING


def safe(rows: list[Row]) -> bool:
    """The matrix's one word: there are rows, and none blocks (CLEAN, or
    benign residue that SPEC-001 §15 already records)."""
    return bool(rows) and not any(r.blocks for r in rows)


def _start(build: Build, shape: str, base: Path, tools: Path) -> tuple[Sandbox, bool, str]:
    """The seeded project, bootstrapped by `build` with Matt's setup and
    `build`'s own Stop continuation: (sandbox, finished, what happened)."""
    sb = Sandbox(base, tools)
    _seed(sb, shape)
    ok, what = _install(build, sb, shape)
    return sb, ok, what


def _path(row: Row, builds: list[Build], candidate: Build, shape: str, base: Path, tools: Path,
          fresh: Sandbox | None) -> None:
    """One cell of the matrix: the project bootstrapped by `builds[0]`, then
    re-bootstrapped by each later build in order -- every hop its own real
    lifecycle, finishing final -- then by the candidate, then checked."""
    sb, ok, what = _start(builds[0], shape, base, tools)
    reached = [what]
    if not ok:
        row.lifecycle[shape] = what
        row.findings.append(Finding(BLOCKED, f"{shape}: {what}"))
        return
    for build in (*builds[1:], candidate):
        before = sb.settings()  # what the next build finds; after the loop, what the candidate found
        out = sb.bootstrap(build, [])
        if not sb.finished(build, out):
            reached.append(f"rice {build.version} did not reach {FINAL}: {_last(out)}")
            row.lifecycle[shape] = " -> ".join(reached)
            row.findings.append(Finding(BLOCKED, f"{shape}: {reached[-1]}"))
            return
        reached.append(build.version)
    row.lifecycle[shape] = " -> ".join(reached)
    if fresh is None:
        row.findings.append(Finding(BLOCKED, f"{shape}: no fresh candidate install to compare with"))
        return
    row.findings += _check(candidate, shape, sb, fresh, base / "check", before)


def run(candidate_root: Path, public_repo: str = PUBLIC_REPO, workdir: Path | None = None) -> tuple[list[Row], str | None]:
    """(`fresh`, one direct Row per published release, one historical Row per
    release but the newest; error). `error` means the matrix could not even
    be assembled -- treat as not safe."""
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
        sb, ok, what = _start(candidate, shape, workdir / f"fresh-{shape}", tools)
        fresh_row.lifecycle[shape] = what
        if not ok:
            fresh_row.findings.append(Finding(BLOCKED, f"{shape}: {what}"))
            continue
        fresh[shape] = sb
        fresh_row.findings += _check(candidate, shape, sb, None, workdir / f"fresh-{shape}-check",
                                     Project.of(shape).settings)

    rows, builds, unfetched = [fresh_row], {}, {}
    for version in versions:
        old_root = workdir / "releases" / version
        problem = fetch_release(public_repo, version, old_root)
        if problem:
            unfetched[version] = problem
        else:
            builds[version] = Build.at(old_root)

    # direct: release -> candidate; historical: release -> every later release -> candidate
    paths = [[v] for v in versions] + [versions[i:] for i in range(len(versions) - 1)]
    for path in paths:
        row = Row(" -> ".join([settings_template.release_ref(path[0]), *path[1:]]))
        rows.append(row)
        missing = [v for v in path if v in unfetched]
        if missing:
            row.findings += [Finding(BLOCKED, unfetched[v]) for v in missing]
            continue
        for shape in PROJECTS:
            _path(row, [builds[v] for v in path], candidate, shape,
                  workdir / f"{'-'.join(path)}-{shape}", tools, fresh.get(shape))
    return rows, None


def render(rows: list[Row], error: str | None = None) -> str:
    if error:
        return f"UPGRADE-MATRIX NOT SAFE -- {error}"
    width = max((len(r.source) for r in rows), default=6)
    lines = [f"{'source':<{width}} {'verdict':<22} lifecycle"]
    for r in rows:
        lines.append(f"{r.source:<{width}} {r.verdict:<22} " + "; ".join(f"{s}: {w}" for s, w in r.lifecycle.items()))
        lines.extend(f"    - [{f.kind}] {f.text}" for f in r.findings)
    benign = sum(r.verdict == BENIGN for r in rows)
    tail = f" ({len(rows)} rows" + (f"; {benign} with benign legacy residue" if benign else "") + ")"
    lines.append(f"UPGRADE-MATRIX SAFE{tail}" if safe(rows)
                 else f"UPGRADE-MATRIX NOT SAFE ({sum(r.blocks for r in rows)} of {len(rows)} rows block)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="every published rice release, directly and along its historical "
                                             "path through later releases, upgraded to a candidate build")
    ap.add_argument("--candidate", default=str(ROOT), help="the candidate build's root (default: this repo)")
    ap.add_argument("--public-repo", default=PUBLIC_REPO)
    ap.add_argument("--workdir", help="keep the sandboxes here (default: a temp dir)")
    args = ap.parse_args(argv)
    rows, err = run(Path(args.candidate), args.public_repo, Path(args.workdir) if args.workdir else None)
    print(render(rows, err))
    return 0 if not err and safe(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
