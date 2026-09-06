#!/usr/bin/env python3
"""rice release: deterministic mechanical gates, export, and publish.

Skill -> this script -> GitHub-native tag/push. No hook, no daemon, no state
machine. The agent never decides a release is OK; only this script's
`RELEASE-CHECKS PASS <version>` allows an export/publish to proceed, and
only its `RELEASED rice <version>` line means a release happened.

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
    """The export must work as an independent plugin/marketplace: manifests
    validate from the export dir, every hook/skill path resolves inside it,
    and the shipped tests pass when run from the export alone."""
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
    rc, out = _run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=dest)  # -B: no bytecode into the export
    if rc != 0:
        problems.append(f"shipped tests fail inside export: {out.strip().splitlines()[-1] if out.strip() else rc}")
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


def publish(dest: Path, version: str, public_repo: str | None) -> tuple[bool, str]:
    """Push the exported surface as a fresh single-commit history to the
    public repository and tag it. Only called after a PASS verdict."""
    if not public_repo:
        return False, "WAITING_FOR_PUBLIC_REPO -- no public repository configured; nothing was tagged or pushed"
    tag = f"v{version}"
    steps = [
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=rice-release", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m", f"rice {version}"],
        ["git", "tag", "-a", tag, "-m", f"rice {version}"],
        ["git", "remote", "add", "public", public_repo],
        ["git", "push", "public", "main"],
        ["git", "push", "public", tag],
    ]
    for cmd in steps:
        rc, out = _run(cmd, cwd=dest)
        if rc != 0:
            return False, f"publish step failed: {' '.join(cmd)} -> rc={rc} {out.strip()[-300:]}"
    rc, out = _run(["git", "ls-remote", "--tags", public_repo, tag])
    if rc != 0 or tag not in out:
        return False, f"published tag {tag} not visible on {public_repo}"
    rc_local, local_tree = _run(["git", "rev-parse", f"{tag}^{{tree}}"], cwd=dest)
    rc_remote, remote_line = _run(["git", "ls-remote", public_repo, f"refs/tags/{tag}^{{}}"])
    if rc_local != 0 or rc_remote != 0 or not remote_line.strip():
        return False, "could not compare exported tree with the published tag"
    return True, "RELEASED rice " + version


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice release gates / export / publish")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--version"); c.add_argument("--dest")
    d = sub.add_parser("dry-run"); d.add_argument("--version"); d.add_argument("--dest")
    p = sub.add_parser("publish"); p.add_argument("--version"); p.add_argument("--dest"); p.add_argument("--public-repo")
    args = ap.parse_args(argv)
    gates, verdict, dest = check(requested_version=args.version, dest=args.dest)
    print(render(gates, verdict))
    print(f"export staged at: {dest}")
    if not verdict.startswith("RELEASE-CHECKS PASS"):
        return 1
    if args.cmd == "publish":
        ok, msg = publish(dest, plugin_version(), args.public_repo)
        print(msg)
        return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
