#!/usr/bin/env python3
"""rice release: deterministic mechanical gates, export, and publish.

Skill -> this script -> GitHub-native push. No git tag, no GitHub release,
no hook, no daemon, no state machine (RICE-BOOTSTRAP-030: release identity
lives entirely in .claude-plugin/plugin.json, CHANGELOG.md, and the exact
commit SHA -- private HEAD for a release cut, public main HEAD for what is
actually live). The agent never decides a release is OK; only this
script's `RELEASE-CHECKS PASS <version>` allows an export/publish to
proceed, and only its `RELEASE-PUBLISHED <version>` line means a release
happened.

Verdict strings are assembled at runtime so that tests/test_verify.py's
"only verify.py emits verifier verdicts" grep stays honest -- those are the
verifier's words; this script has its own.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_public  # noqa: E402
import settings_template  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
README_REQUIRED = (
    "# FOR AGENTS",
    "## Installation",
    "## Bootstrap",
    "## Uninstall",
    "## Release",
    "claude plugin install rice@rice-bootstrap",
    "RICE-" + "VERIFIED",
    "FINAL-" + "VERIFIED",
    ".claude/rice-" + "version",
)


@dataclass
class Gate:
    name: str
    state: str  # PASS / FAIL only
    detail: str = ""


def _run(cmd, cwd=None, timeout=900):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)[-4000:]
    except FileNotFoundError as exc:
        return 127, f"{exc}"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def plugin_version(root: Path = ROOT) -> str:
    """The build's version, or "" when it cannot be read -- unreadable is
    never a version, and `gate_version` fails closed on it."""
    try:
        return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def changelog_top_version(root: Path = ROOT) -> str | None:
    p = root / "CHANGELOG.md"
    if not p.exists():
        return None
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", p.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def changelog_top_date(root: Path = ROOT) -> str | None:
    p = root / "CHANGELOG.md"
    if not p.exists():
        return None
    m = re.search(r"^## \[\d+\.\d+\.\d+\] - (\d{4}-\d{2}-\d{2})", p.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def _git_head_sha(root: Path) -> str:
    rc, out = _run(["git", "rev-parse", "HEAD"], cwd=root)
    return out.strip() if rc == 0 else ""


def source_identity(root: Path = ROOT) -> dict:
    """The private release identity a release is cut from: plugin.json
    version, CHANGELOG top date, and the exact private HEAD SHA. Recorded
    mechanically at the start of every check/publish run -- no private git
    tag is required or consulted."""
    return {
        "version": plugin_version(root),
        "changelog_date": changelog_top_date(root),
        "head_sha": _git_head_sha(root),
    }


def gate_tests(root: Path, run_tests: bool) -> list[Gate]:
    if not run_tests:
        return [Gate("tests:all", "FAIL", "test run skipped -- a release check never skips tests"),
                Gate("tests:verifier", "FAIL", "test run skipped")]
    rc, out = _run([sys.executable, "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "tests"], cwd=root)
    all_g = Gate("tests:all", "PASS" if rc == 0 else "FAIL", out.strip().splitlines()[-1] if out.strip() else f"rc={rc}")
    rc2, out2 = _run([sys.executable, "-m", "unittest", "tests.test_verify"], cwd=root)
    ver_g = Gate("tests:verifier", "PASS" if rc2 == 0 else "FAIL", out2.strip().splitlines()[-1] if out2.strip() else f"rc={rc2}")
    return [all_g, ver_g]


def gate_manifests(root: Path) -> Gate:
    try:
        mp = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        pj = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Gate("manifests:valid", "FAIL", f"manifest unreadable: {exc}")
    if pj.get("name") != "rice" or mp.get("name") != "rice-bootstrap":
        return Gate("manifests:valid", "FAIL", "plugin/marketplace names are not rice / rice-bootstrap")
    rc, out = _run(["claude", "plugin", "validate", str(root)])
    if rc != 0:
        return Gate("manifests:valid", "FAIL", f"claude plugin validate failed (rc={rc}): {out.strip()[-300:]}")
    if "warning" in out.lower():
        return Gate("manifests:valid", "FAIL", f"claude plugin validate reported warnings: {out.strip()[-300:]}")
    return Gate("manifests:valid", "PASS", "claude plugin validate clean")


def gate_version(root: Path, requested: str | None) -> Gate:
    pv, cv = plugin_version(root), changelog_top_version(root)
    if not SEMVER.match(pv or ""):
        return Gate("version:consistent", "FAIL", f"plugin.json version {pv!r} is not SemVer")
    if cv != pv:
        return Gate("version:consistent", "FAIL", f"CHANGELOG top entry {cv!r} != plugin.json {pv!r}")
    if requested and requested != pv:
        return Gate("version:consistent", "FAIL", f"requested {requested!r} != plugin.json {pv!r}")
    return Gate("version:consistent", "PASS", pv)


def gate_readme(root: Path) -> Gate:
    """The README is part of the release surface, so it must name the exact
    release branch it ships as -- a reader who copies its install command
    gets this build, not whatever a moving ref points at."""
    p = root / "README.md"
    if not p.exists():
        return Gate("readme:agent-contract", "FAIL", "README.md missing")
    text = p.read_text(encoding="utf-8")
    version = plugin_version(root)
    branch = release_branch(version)
    required = list(README_REQUIRED) + [f"add {export_public.MARKETPLACE_REPO}@{branch}"]
    missing = [s for s in required if s not in text]
    stale = sorted(set(re.findall(r"release/\d+\.\d+\.\d+", text)) - {branch})
    if stale:
        return Gate("readme:agent-contract", "FAIL",
                    f"names other release branch(es) {stale} -- a reader copying an install "
                    f"command must get {branch}")
    if missing:
        return Gate("readme:agent-contract", "FAIL", "missing required content: " + ", ".join(repr(m) for m in missing))
    human_intro = text.split("# FOR AGENTS", 1)[0]
    body_lines = [l for l in human_intro.splitlines() if l.strip() and not l.startswith("#")]
    if len(body_lines) > 12:
        return Gate("readme:agent-contract", "FAIL", f"human introduction too long ({len(body_lines)} lines before FOR AGENTS)")
    return Gate("readme:agent-contract", "PASS")


def gate_export(root: Path, dest: Path) -> list[Gate]:
    gates = []
    try:
        copied = export_public.export(dest, source_root=root)
        gates.append(Gate("export:allowlist-complete", "PASS", f"{len(copied)} files"))
    except (FileNotFoundError, ValueError) as exc:
        return [Gate("export:allowlist-complete", "FAIL", str(exc)),
                Gate("export:forbidden-absent", "FAIL", "export did not run"),
                Gate("export:secret-scan", "FAIL", "export did not run"),
                Gate("export:standalone", "FAIL", "export did not run")]
    # Standalone runs first: it executes things inside the export, so the
    # audit must see the tree as it will be published, not as it was copied.
    standalone = gate_standalone(dest)
    problems = export_public.audit(dest)
    forbidden = [p for p in problems if p.startswith(("forbidden", "not allowlisted", "allowlisted but absent"))]
    secrets = [p for p in problems if p.startswith(("credential-shaped", "owner-specific"))]
    gates.append(Gate("export:forbidden-absent", "PASS" if not forbidden else "FAIL", "; ".join(forbidden)[:600]))
    gates.append(Gate("export:secret-scan", "PASS" if not secrets else "FAIL", "; ".join(secrets)[:600]))
    gates.append(standalone)
    return gates


def gate_standalone(dest: Path) -> Gate:
    """The export must work as an independent plugin/marketplace, proven
    entirely from the private side against the staging tree: manifests
    validate from the export dir, every hook/skill path resolves inside it,
    and every shipped runtime script imports cleanly standalone. The public
    distribution never carries tests/ to prove this -- see RICE-BOOTSTRAP-027."""
    problems = []
    try:
        hooks = json.loads((dest / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for event, entries in hooks.get("hooks", {}).items():
            for entry in entries:
                for h in entry.get("hooks", []):
                    m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"' ]+)", h.get("command", ""))
                    if not m or not (dest / m.group(1)).is_file():
                        problems.append(f"hook command does not resolve inside export: {h.get('command')}")
        pj = json.loads((dest / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for s in pj.get("skills", []):
            if not (dest / s / "SKILL.md").is_file():
                problems.append(f"plugin.json skill path missing in export: {s}")
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"manifest/hook unreadable in export: {exc}")
    rc, out = _run(["claude", "plugin", "validate", str(dest)])
    if rc != 0 or "warning" in out.lower():
        problems.append(f"claude plugin validate on export: rc={rc} {out.strip()[-200:]}")
    scripts_dir = dest / "scripts"
    modules = sorted(p.stem for p in scripts_dir.glob("*.py")) if scripts_dir.is_dir() else []
    if not modules:
        problems.append("no shipped runtime scripts to smoke-test")
    else:
        smoke = "; ".join(f"import {m}" for m in modules)
        rc, out = _run([sys.executable, "-B", "-c", smoke], cwd=scripts_dir)  # -B: no bytecode into the export
        if rc != 0:
            problems.append(f"runtime import smoke test failed: {out.strip()[-300:] if out.strip() else rc}")
    return Gate("export:standalone", "PASS" if not problems else "FAIL", "; ".join(problems)[:800])


def check(root: Path = ROOT, *, requested_version: str | None = None, dest: Path | None = None,
          run_tests: bool = True) -> tuple[list[Gate], str, Path]:
    dest = Path(dest) if dest else Path(tempfile.mkdtemp(prefix="rice-export-"))
    gates = []
    gates.extend(gate_tests(root, run_tests))
    gates.append(gate_manifests(root))
    gates.append(gate_version(root, requested_version))
    gates.append(gate_readme(root))
    gates.extend(gate_export(root, dest))
    failed = [g for g in gates if g.state != "PASS"]
    version = plugin_version(root)
    if failed:
        verdict = "RELEASE BLOCKED (%d gate(s) not PASS)" % len(failed)
    else:
        verdict = "RELEASE-CHECKS PASS " + version
    return gates, verdict, dest


def render(gates, verdict) -> str:
    lines = [f"{g.state:<5} {g.name}" + (f" -- {g.detail}" if g.detail else "") for g in gates]
    lines.append(verdict)
    return "\n".join(lines)


def release_branch(version: str) -> str:
    return settings_template.release_ref(version)


def publish(dest: Path, version: str, public_repo: str | None, source_sha: str = "") -> tuple[bool, str]:
    """Publish this export as the public branch `release/<version>`.

    A release is a branch, and a branch is immutable once pushed
    (RICE-FINAL-001). That makes publishing the simplest operation rice has:
    a fresh single-commit history, pushed to a branch name that must not
    exist yet. If it does exist, this FAILS CLOSED -- the same version never
    names two different builds, and rice's tooling never moves or overwrites
    a branch someone may already be installing from. The next release is a
    new branch, not a new commit on an old one.

    No git tag or GitHub release is created (RICE-BOOTSTRAP-030), and public
    `main` is not touched: it is a landing page at most, never an
    installation source. Every commit records provenance: rice <version>
    (source <private HEAD SHA>)."""
    if not public_repo:
        return False, "WAITING_FOR_PUBLIC_REPO -- no public repository configured; nothing was pushed"
    branch = release_branch(version)

    rc, out = _run(["git", "ls-remote", "--heads", public_repo, branch])
    if rc != 0:
        return False, f"could not reach public repository: {out.strip()[-300:]}"
    if out.strip():
        return False, (f"RELEASE BLOCKED -- {branch} already exists on {public_repo}. A released "
                       "version is immutable: rice never moves or overwrites a release branch. "
                       "Bump the version and publish the next one.")

    with tempfile.TemporaryDirectory(prefix="rice-publish-") as tmp:
        return _publish_branch(Path(tmp), dest, branch, version, public_repo, source_sha)


def _publish_branch(ws: Path, dest: Path, branch: str, version: str, public_repo: str,
                    source_sha: str) -> tuple[bool, str]:
    message = f"rice {version}" + (f" (source {source_sha})" if source_sha else "")
    steps = [["git", "init", "-q", "-b", branch],
             ["git", "remote", "add", "public", public_repo]]
    for cmd in steps:
        rc, out = _run(cmd, cwd=ws)
        if rc != 0:
            return False, f"publish step failed: {' '.join(cmd)} -> rc={rc} {out.strip()[-300:]}"

    for rel in sorted(str(p.relative_to(dest)) for p in Path(dest).rglob("*") if p.is_file()):
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(dest) / rel, target)

    for cmd in (["git", "add", "-A"],
                ["git", "-c", "user.email=rice@local", "-c", "user.name=rice release",
                 "commit", "-q", "-m", message]):
        rc, out = _run(cmd, cwd=ws)
        if rc != 0:
            return False, f"publish step failed: {' '.join(cmd)} -> rc={rc} {out.strip()[-300:]}"

    # never a force, never a rewrite: the branch does not exist yet
    rc, out = _run(["git", "push", "public", f"{branch}:{branch}"], cwd=ws)
    if rc != 0:
        return False, f"push failed: rc={rc} {out.strip()[-300:]}"

    rc, local = _run(["git", "rev-parse", "HEAD"], cwd=ws)
    rc2, remote = _run(["git", "ls-remote", "--heads", public_repo, branch])
    if rc != 0 or rc2 != 0 or not remote.strip():
        return False, f"published, but could not confirm {branch} on the remote"
    if remote.split()[0].strip() != local.strip():
        return False, f"{branch} on the remote is not what was pushed"
    return True, f"RELEASE-PUBLISHED {version} -> {public_repo} {branch} @ {local.strip()[:12]}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice release gates / export / publish")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--version"); c.add_argument("--dest")
    d = sub.add_parser("dry-run"); d.add_argument("--version"); d.add_argument("--dest")
    p = sub.add_parser("publish"); p.add_argument("--version"); p.add_argument("--dest"); p.add_argument("--public-repo")
    args = ap.parse_args(argv)
    source = source_identity(ROOT)
    print(f"SOURCE rice={source['version']} changelog={source['changelog_date']} head={source['head_sha']}")
    gates, verdict, dest = check(requested_version=args.version, dest=args.dest)
    print(render(gates, verdict))
    print(f"export staged at: {dest}")
    if not verdict.startswith("RELEASE-CHECKS PASS"):
        return 1
    if args.cmd == "publish":
        ok, msg = publish(dest, plugin_version(), args.public_repo, source_sha=source["head_sha"])
        print(msg)
        return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
