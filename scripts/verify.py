#!/usr/bin/env python3
"""The deterministic mechanical verifier -- the ONLY authority that may say
RICE-VERIFIED / FINAL-VERIFIED, and the ONLY writer of .claude/rice-version.

RICE-BOOTSTRAP-021. Skills, README, the spec and the agent describe how to
operate rice; none of them decide correctness. This module does, with one
invariant that every test in tests/test_verify.py pins down:

    a gate passes only if EVERY required item of that gate has state
    exactly "PASS". FAIL, NOT_VERIFIED, ERROR, a missing item, or any
    unknown state => the gate FAILS. There is no allowlist, no "informational"
    escape, and no parameter through which a caller can waive an item.

Gate A (RICE-VERIFIED <build>): everything rice controls or can verify
mechanically without the OWNER at the keyboard. Passing it is the
precondition for entering WAITING_FOR_MATT.

Gate B (FINAL-VERIFIED <build>): Gate A again, plus the items that need the
OWNER's own actions or the environment (Matt artifacts, RTK). Matt and RTK
are REQUIRED, not optional integrations: v1 is the OWNER's actual workflow,
and no FINAL-VERIFIED exists without both. A real `/clear` is deliberately
NOT required (RICE-BOOTSTRAP-032): archive registration is a Gate A fact
(`archive-hook:exists`, `archive-sync-hook:exists` -- wired, not exercised),
and whether a produced archive has actually reached its upstream
(`archive:behaves`) is informational only, checked from git state, never a
gate requirement. Only a Gate B pass writes `.claude/rice-version`; on any
failure the file is left byte-for-byte untouched. The same pass removes the
WAITING_FOR_MATT marker -- the deterministic re-check #28 asked for.

Pyramid check for this module: Setting/Permission cannot verify
themselves; this is a Script; no Hook is needed (verification runs when a
skill or the E2E invokes it); nothing here is policy.
"""
import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402
from bootstrap_state import MARKER_NAME  # noqa: E402

VERSION_FILE = ".claude/rice-version"

GATE_A = (
    "plugin:installed",
    "plugin:build",
    "output-style:bundled",
    "output-style:choice",
    *sorted(f"settings:{k}" for k in reconcile.settings_template.RICE_DEFINED_KEYS),
    *sorted(f"settings:{k}" for k in reconcile.settings_template.OWNER_DEFINED_KEYS),
    "rice.md",
    "CLAUDE.md:import-line",
    "AGENTS.md:rice-pointer",
    "archive-hook:exists",
    "archive-sync-hook:exists",
    "continuation-hook:exists",
    "git-deny:behaves",
)

GATE_B_EXTRA = (
    "matt:artifacts",
    "rtk",
)

GATE_B = GATE_A + GATE_B_EXTRA


@dataclass
class Report:
    build: str
    items: list
    gate_a: bool
    gate_b: bool | None            # None when --final was not requested
    version_written: str | None    # the SemVer stamped, only on a Gate B pass
    lines: list = field(default_factory=list)


def gate_passes(items, required) -> tuple[bool, list[str]]:
    """The invariant. Returns (passed, reasons). A reason is recorded for
    every required item that is not exactly PASS, including absent ones.
    If a name occurs more than once, every occurrence must be PASS -- a
    duplicate can never mask a failure."""
    by_name: dict[str, list] = {}
    for i in items:
        by_name.setdefault(i.name, []).append(i)
    reasons = []
    for name in required:
        occurrences = by_name.get(name)
        if not occurrences:
            reasons.append(f"{name}: MISSING (verifier produced no result for a required item)")
            continue
        for i in occurrences:
            if i.state != "PASS":
                reasons.append(f"{name}: {i.state}" + (f" -- {i.detail}" if i.detail else ""))
    return (not reasons), reasons


def _collect(repo_root, **probes):
    """Every exception inside item collection becomes an ERROR item rather
    than a crash -- and ERROR is not PASS, so the gate fails closed."""
    try:
        return reconcile.check(repo_root, **probes)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything
        return [reconcile.ItemResult("verifier", "ERROR", f"{type(exc).__name__}: {exc}", kind="ERROR")]


def read_version(repo_root) -> str | None:
    p = Path(repo_root) / VERSION_FILE
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def run(repo_root, *, final: bool = False, **probes) -> Report:
    repo_root = Path(repo_root)
    build = reconcile.build_version()
    items = _collect(repo_root, **probes)
    a_ok, a_reasons = gate_passes(items, GATE_A)
    lines = [f"[{'B' if i.name in GATE_B_EXTRA else 'A'}] {i.kind:<7} {i.state:<12} {i.name}"
             + (f" -- {i.detail}" if i.detail else "") for i in items]
    lines.append(f"rice-version on disk: {read_version(repo_root) or '(none)'}")
    if a_ok:
        lines.append(f"RICE-VERIFIED {build}")
    else:
        lines.append(f"RICE-GATE-A FAIL ({len(a_reasons)} required item(s) not PASS)")
        lines.extend(f"  - {r}" for r in a_reasons)

    b_ok, written = None, None
    if final:
        b_ok, b_reasons = gate_passes(items, GATE_B)
        if b_ok:
            written = _stamp(repo_root, build)
            lines.append(f"FINAL-VERIFIED {build}")
        else:
            lines.append(f"FINAL FAIL ({len(b_reasons)} required item(s) not PASS); rice-version untouched")
            lines.extend(f"  - {r}" for r in b_reasons)
    return Report(build=build, items=items, gate_a=a_ok, gate_b=b_ok, version_written=written, lines=lines)


def _stamp(repo_root: Path, build: str) -> str:
    """The one and only write of the verified version. Called exclusively
    from a Gate B pass. Also clears the WAITING_FOR_MATT marker, since a
    Gate B pass by definition includes matt:artifacts PASS."""
    p = repo_root / VERSION_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build + "\n", encoding="utf-8")
    marker = repo_root / ".claude" / MARKER_NAME
    if marker.exists():
        marker.unlink()
    return build


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice mechanical verifier")
    ap.add_argument("repo_root", nargs="?", default=".")
    ap.add_argument("--final", action="store_true", help="also evaluate Gate B and, on PASS, stamp .claude/rice-version")
    args = ap.parse_args(argv)
    rep = run(args.repo_root, final=args.final)
    print("\n".join(rep.lines))
    ok = rep.gate_b if args.final else rep.gate_a
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
