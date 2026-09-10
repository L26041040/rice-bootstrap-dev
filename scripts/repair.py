#!/usr/bin/env python3
"""RICE-BOOTSTRAP-042: the single deterministic repair boundary, and the
one pipeline every rice lifecycle runs through:

    normal path -> verify -> (FAIL) -> deterministic repair -> verify again
                                    -> (still FAIL) -> the model, last

`verify.py` alone decides what is broken: it collects real state and names
every failing invariant by its item name. This module never re-derives that
judgement. It maps each failure *identity* the verifier reported to a known,
lossless restore, applies it, and hands the repo straight back to the same
verifier. It declares nothing: the verdict after a repair is whatever the
second verify run says, and only that run may stamp the verified version.

What is mechanically repairable here, and from where:

- rice-owned settings leaves, `.claude/rice.md`, the `@.claude/rice.md`
  line in CLAUDE.md, the rice pointer block in AGENTS.md -- from this
  build's canonical desired state, via `reconcile.apply(is_rice_update=True)`
  (the verifier has already declared the drift a FAIL, so converging on the
  canonical value is the one repair that exists).
- Matt's setup artifacts -- only from this repo's own history: a doc that
  is tracked at HEAD but missing from the working tree is checked out
  again; a `## Agent skills` section present in HEAD's CLAUDE.md but gone
  from the working copy is appended back, byte for byte. Nothing else:
  every Matt file encodes answers the OWNER gave the official setup, so a
  file the repo never committed cannot be rebuilt without guessing, and
  guessing is exactly what this module refuses to do. Such an artifact is
  reported as unresolved, for the model to explain to the OWNER.

Everything else the verifier can fail (`plugin:*`, `output-style:*`, the
hook registrations of the build, `git-deny:behaves`, owner-defined settings
keys, `rtk`) needs the environment, the OWNER, or a real execution; it is
passed through as unresolved with the verifier's own detail.
"""
import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402
import verify  # noqa: E402

# Failure identities reconcile.apply repairs from canonical state.
RECONCILE_OWNED = frozenset(
    {f"settings:{k}" for k in reconcile.settings_template.RICE_DEFINED_KEYS}
    | {"rice.md", "CLAUDE.md:import-line", "AGENTS.md:rice-pointer"}
)
MATT = "matt:artifacts"


@dataclass
class RepairResult:
    applied: list[str] = field(default_factory=list)     # what was restored, and from where
    unresolved: list[str] = field(default_factory=list)  # what this module refuses to guess at


@dataclass
class Outcome:
    before: verify.Report
    repair: RepairResult | None   # None: the normal path passed, repair never ran
    after: verify.Report | None   # the verifier's word after repair

    @property
    def report(self) -> verify.Report:
        return self.after or self.before


def gate_ok(report: verify.Report, *, final: bool) -> bool:
    return bool(report.gate_b) if final else bool(report.gate_a)


# --------------------------------------------------------------------------
# Matt artifacts: restore from this repo's history, never from a template
# --------------------------------------------------------------------------

def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)


def _head_text(repo_root: Path, rel: str) -> str | None:
    r = _git(repo_root, "show", f"HEAD:{rel}")
    return r.stdout if r.returncode == 0 else None


def _section_from(text: str, heading: str) -> str | None:
    """The `heading` section of a markdown text: from its heading line up to
    the next heading of the same or higher level, or the end."""
    lines = text.splitlines(keepends=True)
    level = len(heading) - len(heading.lstrip("#"))
    start = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        s = lines[i].lstrip()
        if s.startswith("#") and 0 < len(s) - len(s.lstrip("#")) <= level:
            end = i
            break
    return "".join(lines[start:end])


def _restore_matt(repo_root: Path, plugin_probe) -> tuple[list[str], list[str]]:
    applied, unresolved = [], []
    for label, present in reconcile.matt_presence(repo_root, plugin_probe).items():
        if present:
            continue
        if label == reconcile.MATT_HEADING_LABEL:
            head = _head_text(repo_root, "CLAUDE.md")
            section = _section_from(head, reconcile.MATT_HEADING) if head else None
            if section:
                dest = repo_root / "CLAUDE.md"
                current = dest.read_text(encoding="utf-8") if dest.exists() else ""
                sep = "" if (not current or current.endswith("\n")) else "\n"
                dest.write_text(f"{current}{sep}\n{section}" if current else section, encoding="utf-8")
                applied.append(f"{MATT}: restored the `{reconcile.MATT_HEADING}` section of CLAUDE.md from HEAD")
            else:
                unresolved.append(f"{MATT}: {label} is not in this repo's HEAD; only the official Matt setup can write it")
            continue
        if _head_text(repo_root, label) is not None and _git(repo_root, "checkout", "HEAD", "--", label).returncode == 0:
            applied.append(f"{MATT}: restored {label} from HEAD")
        else:
            unresolved.append(f"{MATT}: {label} is not in this repo's HEAD; only the official Matt setup can write it")
    return applied, unresolved


# --------------------------------------------------------------------------
# the repair boundary
# --------------------------------------------------------------------------

def repair(repo_root, items, *, final: bool = False, rtk_probe=None, plugin_probe=None) -> RepairResult:
    """Apply the known repair for every failing item in `items` (a verifier
    report's items) that the gate in question requires -- Gate A, or Gate B
    with `final`. Returns what was done and what could not be; says nothing
    about whether the repo now passes -- run the verifier."""
    repo_root = Path(repo_root)
    plugin_probe = plugin_probe or reconcile._default_plugin_probe
    required = verify.GATE_B if final else verify.GATE_A
    failing = [i for i in items if i.state != "PASS" and i.name in required]
    result = RepairResult()
    if any(i.name in RECONCILE_OWNED for i in failing):
        for a in reconcile.apply(repo_root, is_rice_update=True, rtk_probe=rtk_probe, plugin_probe=plugin_probe):
            if a.state in ("created", "updated"):
                result.applied.append(f"{a.name}: {a.state} from canonical state" + (f" ({a.detail})" if a.detail else ""))
    for i in failing:
        if i.name in RECONCILE_OWNED:
            continue
        if i.name == MATT:
            applied, unresolved = _restore_matt(repo_root, plugin_probe)
            result.applied += applied
            result.unresolved += unresolved
        else:
            result.unresolved.append(f"{i.name}: {i.state}" + (f" -- {i.detail}" if i.detail else ""))
    return result


def pipeline(repo_root, *, final: bool = False, **probes) -> Outcome:
    """Normal path first: verify. Repair runs only on a FAIL, and the repo
    then goes back to the same verifier for the word that counts."""
    before = verify.run(repo_root, final=final, **probes)
    if gate_ok(before, final=final):
        return Outcome(before=before, repair=None, after=None)
    done = repair(repo_root, before.items, final=final, **probes)
    after = verify.run(repo_root, final=final, **probes)
    return Outcome(before=before, repair=done, after=after)


def render(outcome: Outcome) -> str:
    lines = []
    if outcome.repair is None:
        lines.append("repair: not needed (normal path passed)")
    else:
        lines += [f"repair: {a}" for a in outcome.repair.applied] or ["repair: nothing mechanically repairable"]
        lines += [f"unresolved: {u}" for u in outcome.repair.unresolved]
    lines += outcome.report.lines
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice: verify, repair what the verifier failed, verify again")
    ap.add_argument("repo_root", nargs="?", default=".")
    ap.add_argument("--final", action="store_true", help="Gate B (the verifier stamps on a pass, as always)")
    args = ap.parse_args(argv)
    outcome = pipeline(args.repo_root, final=args.final)
    print(render(outcome))
    return 0 if gate_ok(outcome.report, final=args.final) else 1


if __name__ == "__main__":
    raise SystemExit(main())
