#!/usr/bin/env python3
"""RICE-BOOTSTRAP-044: rice's lifecycle engine -- the one place that knows
the ORDER of things.

Four verbs, and nothing outside this module needs to know more than these:

    bootstrap  configure this repo from scratch, up to the Matt hand-off
    update     converge this repo onto a newer rice build
    continue   finish a bootstrap after the OWNER's Matt setup (Stop hook)
    styles     the output-style values the OWNER may choose from

Every verb runs the same canonical flow, which lives here and only here:

    normal path -> verify -> PASS: done
                          -> FAIL: deterministic repair -> verify again
                                -> still FAIL: the model, last

This module decides nothing about correctness. It does not evaluate a gate,
parse evidence, rebuild a verdict, or repair anything itself:

    reconcile  owns what rice's desired state is, and writes it
    verify     is the sole authority on PASS/FAIL, and the sole writer of
               the verified-version file and remover of the pending marker
    repair     owns which failure identity maps to which lossless restore

What this module owns instead is sequence: which of those runs when, and
the one lifecycle decision that is genuinely about order -- whether this
repo still has to wait for the OWNER's Matt setup, or can go straight to
final verification because Matt's artifacts are already there. The skills
and the Stop hook are adapters over these verbs; they carry no steps of
their own (RICE-BOOTSTRAP-044).
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap_state  # noqa: E402
import output_styles  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

VERIFIER = Path(__file__).resolve().parent / "verify.py"
UNSET = reconcile.UNSET  # "the OWNER was not asked" (see reconcile.write_owner_choices)


def _memoized(probe):
    """One external command, one spawn per run. Readiness and each verifier
    run ask the same two questions (`claude plugin list --json`,
    `rtk --version`); re-spawning them per call is pure latency, and on the
    Stop hook that latency lands at the end of every turn."""
    cache: dict[tuple, tuple] = {}

    def run(cmd):
        key = tuple(cmd)
        if key not in cache:
            try:
                cache[key] = (probe(cmd), None)
            except Exception as exc:  # noqa: BLE001 - replayed verbatim below
                cache[key] = (None, exc)
        result, exc = cache[key]
        if exc is not None:
            raise exc
        return result

    return run


def default_probes() -> dict:
    """The probes every caller should pass unless it has its own: the real
    commands, asked at most once each."""
    return {"plugin_probe": _memoized(reconcile._default_plugin_probe),
            "rtk_probe": _memoized(reconcile._default_rtk_probe)}


@dataclass
class Outcome:
    """What a verb did: the verifier's last word, what repair restored on the
    way (None when the normal path passed and repair never ran), and whether
    the repo is now parked waiting for the OWNER's Matt setup."""
    report: verify.Report
    repaired: repair.RepairResult | None = None
    waiting_for_matt: bool = False

    @property
    def ok(self) -> bool:
        return self.report.ok

    @property
    def verdict(self) -> str:
        return self.report.verdict

    @property
    def lines(self) -> list[str]:
        head = self.repaired.lines if self.repaired is not None else ["repair: not needed (normal path passed)"]
        tail = ["waiting: the OWNER's Matt setup has not run yet"] if self.waiting_for_matt else []
        return head + self.report.lines + tail


def _pipeline(repo_root, *, final: bool, carried: repair.RepairResult | None = None, **probes) -> Outcome:
    """The canonical flow, in the only place it exists as code. `carried` is
    what an earlier half of the same lifecycle already restored -- a Gate A
    repair stays in the report even when Gate B then runs cleanly, so the
    output never claims repair was "not needed" after it rewrote files."""
    report = verify.run(repo_root, final=final, **probes)
    if report.ok:
        return Outcome(report=report, repaired=carried)
    repaired = repair.repair(repo_root, report, **probes)
    if carried is not None:
        repaired = repair.RepairResult(applied=carried.applied + repaired.applied,
                                       unresolved=carried.unresolved + repaired.unresolved)
    return Outcome(report=verify.run(repo_root, final=final, **probes), repaired=repaired)


def _matt_started(repo_root, **probes) -> bool:
    """Has the OWNER's Matt setup left anything behind? Asked of reconcile,
    which owns what Matt's artifacts are -- never inspected here."""
    plugin_probe = probes.get("plugin_probe") or reconcile._default_plugin_probe
    return any(reconcile.matt_presence(Path(repo_root), plugin_probe).values())


# --------------------------------------------------------------------------
# the four verbs
# --------------------------------------------------------------------------

def styles(repo_root, **probes) -> list[dict]:
    """Every output-style value that would resolve in this repo, for the
    OWNER's Step 1 choice."""
    plugin_probe = probes.get("plugin_probe") or reconcile._default_plugin_probe
    return output_styles.available(repo_root, plugin_probe,
                                   self_plugin=(settings_template.PLUGIN_NAME, reconcile.PLUGIN_ROOT))


def bootstrap(repo_root, *, ref=None, archive=None, output_style=UNSET, **probes) -> Outcome:
    """A fresh repo, up to (and when possible past) the Matt hand-off.

    The OWNER's answers are written once if absent, rice-owned state is
    reconciled, then Gate A goes through the canonical flow. On a Gate A
    pass the repo enters the pending state -- and if Matt's artifacts are
    already present (a re-bootstrap, or a repo that had them all along),
    final verification happens here and now rather than asking the OWNER
    for a setup they have already done. Either way the OWNER invokes
    bootstrap exactly once (RICE-BOOTSTRAP-028)."""
    repo_root = Path(repo_root)
    reconcile.write_owner_choices(repo_root, ref=ref, archive=archive, output_style=output_style)
    reconcile.apply(repo_root, **probes)
    gate_a = _pipeline(repo_root, final=False, **probes)
    if not gate_a.ok:
        return gate_a
    bootstrap_state.enter_waiting_for_matt(repo_root)
    matt = gate_a.report.item("matt:artifacts")
    if matt is not None and matt.state == "PASS":
        return _pipeline(repo_root, final=True, carried=gate_a.repaired, **probes)
    return Outcome(report=gate_a.report, repaired=gate_a.repaired, waiting_for_matt=True)


def update(repo_root, **probes) -> Outcome:
    """Converge onto the build running this code, then verify to the end.
    Same flow as bootstrap's second half; the OWNER's choices are never
    touched (`is_rice_update=True` converges rice-owned state only)."""
    repo_root = Path(repo_root)
    reconcile.apply(repo_root, is_rice_update=True, **probes)
    gate_a = _pipeline(repo_root, final=False, **probes)
    if not gate_a.ok:
        return gate_a
    return _pipeline(repo_root, final=True, carried=gate_a.repaired, **probes)


def continue_after_matt(repo_root, **probes) -> Outcome | None:
    """The Stop hook's verb. None means "nothing to continue" -- this repo
    is not mid-bootstrap, or the OWNER's Matt setup has not started, and the
    turn must not be disturbed. Anything else goes through the canonical
    flow to Gate B, so an incomplete Matt setup is reported rather than left
    silently pending (RICE-BOOTSTRAP-042)."""
    repo_root = Path(repo_root)
    if not bootstrap_state.is_waiting_for_matt(repo_root):
        return None
    if not _matt_started(repo_root, **probes):
        return None
    return _pipeline(repo_root, final=True, **probes)


def continuation_message(repo_root, outcome: Outcome) -> str:
    """What the Stop hook hands back to the model. The verifier's own verdict
    and reasons, verbatim, plus the one instruction that belongs to this
    lifecycle: finish the job in this same turn, and never write Matt's
    content or send the OWNER back to rice."""
    if outcome.ok:
        repaired = ""
        if outcome.repaired and outcome.repaired.applied:
            repaired = (" Deterministic repair restored:\n"
                        + "\n".join(f"  - {a}" for a in outcome.repaired.applied) + "\n")
        return (
            f"rice: {outcome.verdict}. The verifier has stamped the verified-version file "
            f"and cleared the pending marker.{repaired} Commit and push the bootstrap, "
            f"Matt and finalization changes in the working tree, then report `{outcome.verdict}` "
            "to the OWNER."
        )
    done = outcome.repaired
    repaired = "\n".join(f"  - {a}" for a in done.applied) if done and done.applied else "  - nothing was mechanically repairable"
    # repair passes non-repairable items through in the verifier's own words,
    # so only what it adds (Matt artifacts it refused to rebuild) is new here
    extra = [u for u in (done.unresolved if done else []) if u not in outcome.report.reasons]
    refused = "\n".join(f"  - {u}" for u in extra)
    return (
        "rice: final verification did not pass; the verified-version file is "
        "untouched.\nDeterministic repair ran first:\n" + repaired
        + "\nStill blocking after repair:\n" + "\n".join(f"  - {r}" for r in outcome.report.reasons)
        + (("\nRepair refused to guess at:\n" + refused) if refused else "")
        + "\nYou are the last fallback. Fix exactly what each line names; never "
        "write Matt setup content yourself -- a Matt artifact listed above must be "
        "reported to the OWNER as missing, by name, because only the official Matt "
        "setup can produce it. Then finish the job in this same turn by running "
        f"`python3 {VERIFIER} {repo_root} --final` yourself. This hook stays quiet "
        "for the rest of this turn."
    )


# --------------------------------------------------------------------------
# CLI -- what the skills invoke
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice lifecycle: bootstrap / update / continue / styles")
    sub = ap.add_subparsers(dest="verb", required=True)
    b = sub.add_parser("bootstrap", help="configure this repo, up to (or past) the Matt hand-off")
    b.add_argument("repo_root", nargs="?", default=".")
    b.add_argument("--ref", help="the delivery ref: upstream main's exact commit SHA (written only if absent)")
    b.add_argument("--archive", choices=("0", "1"), help="the OWNER's session-archive choice (written only if absent)")
    b.add_argument("--output-style", help="the OWNER's style choice, or 'none' for Claude's default (written only if absent)")
    for verb, help_text in (("update", "converge this repo onto this build"),
                            ("continue", "finish a bootstrap after the OWNER's Matt setup"),
                            ("styles", "list the output styles the OWNER may choose")):
        sp = sub.add_parser(verb, help=help_text)
        sp.add_argument("repo_root", nargs="?", default=".")
    args = ap.parse_args(argv)

    probes = default_probes()
    if args.verb == "styles":
        for s in styles(args.repo_root, **probes):
            print(f"{s['value']}\t{s['source']}")
        return 0
    if args.verb == "bootstrap":
        style = UNSET if args.output_style is None else (None if args.output_style == "none" else args.output_style)
        outcome = bootstrap(args.repo_root, ref=args.ref,
                            archive=None if args.archive is None else args.archive == "1",
                            output_style=style, **probes)
    elif args.verb == "update":
        outcome = update(args.repo_root, **probes)
    else:
        outcome = continue_after_matt(args.repo_root, **probes)
        if outcome is None:
            print("continue: nothing to continue (not mid-bootstrap, or Matt setup has not started)")
            return 0
    print("\n".join(outcome.lines))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
