#!/usr/bin/env python3
"""rice release: deterministic mechanical gates, export, and publish.

Skill -> this script -> GitHub-native push. No git tag, no GitHub release,
no hook, no daemon, no state machine (RICE-BOOTSTRAP-030: release identity
lives entirely in .claude-plugin/plugin.json, CHANGELOG.md, and the exact
commit SHA -- private HEAD for a release cut, public main HEAD for what is
actually live). The agent never decides a release is OK; only this
script's `RELEASE-CHECKS PASS <version>` allows an export/publish to
proceed, and only its `RELEASED rice <version>` line means a release
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

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
README_REQUIRED = (
    "# FOR AGENTS",
    "## Installation",
    "## Bootstrap",
    "## Update",
    "## Uninstall",
    "## Release",
    "claude plugin marketplace add L26041040/rice-bootstrap",
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
    return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "")


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
    p = root / "README.md"
    if not p.exists():
        return Gate("readme:agent-contract", "FAIL", "README.md missing")
    text = p.read_text(encoding="utf-8")
    missing = [s for s in README_REQUIRED if s not in text]
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


def _replace_worktree_with_export(git_root: Path, export_dir: Path) -> None:
    """Make git_root's working tree an exact copy of export_dir, keeping
    .git. Anything tracked that export_dir no longer has (a file dropped
    from the allowlist) does not survive; git add -A then stages both the
    removals and the additions."""
    for item in git_root.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for src in export_dir.rglob("*"):
        if src.is_file():
            target = git_root / src.relative_to(export_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)


def publish(dest: Path, version: str, public_repo: str | None, source_sha: str = "") -> tuple[bool, str]:
    """Publish the exported surface to the public repository. Only called
    after a PASS verdict. No git tag or GitHub release is ever created,
    pushed, or checked (RICE-BOOTSTRAP-030) -- success is public `main`
    correctly holding this export, confirmed by re-reading the remote.
    Deterministic flow:

    - public repo has no `main` yet: fresh single-commit history.
    - public `main` exists and differs from this export: fetch it,
      reconcile the working tree to exactly the new export (dropping
      anything no longer allowlisted), commit a normal next release
      commit, fast-forward push -- never a rewrite, never an unrelated
      history.
    - public `main` already equals this export byte-for-byte (e.g. a
      re-run of the same version after an earlier run's push already
      landed): there is nothing new to commit. That is a safe
      already-published outcome, not a FAIL -- "nothing to commit" must
      never block a release whose content is already live.

    Every commit records provenance: rice <version> (source <private HEAD
    SHA>)."""
    if not public_repo:
        return False, "WAITING_FOR_PUBLIC_REPO -- no public repository configured; nothing was pushed"
    message = f"rice {version}" + (f" (source {source_sha})" if source_sha else "")

    rc, out = _run(["git", "ls-remote", "--heads", public_repo, "main"])
    if rc != 0:
        return False, f"could not reach public repository: {out.strip()[-300:]}"
    is_first_release = not out.strip()

    ws = Path(tempfile.mkdtemp(prefix="rice-publish-"))
    steps = [["git", "init", "-q", "-b", "main"], ["git", "remote", "add", "public", public_repo]]
    if not is_first_release:
        steps += [["git", "fetch", "-q", "public", "main"], ["git", "checkout", "-q", "-B", "main", "public/main"]]
    for cmd in steps:
        rc, out = _run(cmd, cwd=ws)
        if rc != 0:
            return False, f"publish step failed: {' '.join(cmd)} -> rc={rc} {out.strip()[-300:]}"

    _replace_worktree_with_export(ws, dest)

    rc, out = _run(["git", "add", "-A"], cwd=ws)
    if rc != 0:
        return False, f"publish step failed: git add -A -> rc={rc} {out.strip()[-300:]}"
    rc, out = _run(["git", "diff", "--cached", "--name-only"], cwd=ws)
    if rc != 0:
        return False, f"publish step failed: git diff --cached --name-only -> rc={rc} {out.strip()[-300:]}"

    if out.strip():
        commit_steps = [
            ["git", "-c", "user.name=rice-release", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m", message],
            ["git", "push", "public", "main"],
        ]
        for cmd in commit_steps:
            rc, out = _run(cmd, cwd=ws)
            if rc != 0:
                return False, f"publish step failed: {' '.join(cmd)} -> rc={rc} {out.strip()[-300:]}"
    # else: public main already equals this export -- already published,
    # nothing new to commit or push.

    rc_local, local_sha = _run(["git", "rev-parse", "HEAD"], cwd=ws)
    rc_remote, remote_out = _run(["git", "ls-remote", "--heads", public_repo, "main"])
    remote_sha = remote_out.split()[0] if remote_out.strip() else ""
    if rc_local != 0 or rc_remote != 0 or local_sha.strip() != remote_sha:
        return False, "could not verify published main matches the export"
    return True, "RELEASED rice " + version


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
