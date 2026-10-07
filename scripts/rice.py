#!/usr/bin/env python3
"""RICE-BOOTSTRAP-044: rice's lifecycle engine -- the one place that knows
the ORDER of things.

Three verbs, and nothing outside this module needs to know more than these:

    bootstrap  configure this repo from scratch, up to the Matt hand-off
    continue   finish a bootstrap after the OWNER's Matt setup (Stop hook)
    styles     the output-style values the OWNER may choose from

There is no update verb (RICE-FINAL-001). Rice has exactly two repo
lifecycles -- bootstrap and uninstall -- and neither ever asks the OWNER to
restart a session, migrate a ref, or reason about which build is "latest".

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

What this module owns instead is sequence -- which of those runs when --
and every decision that follows from it (RICE-BOOTSTRAP-044/-044B):

- whether rice is even loaded here, and whether the install is the
  supported one;
- whether this repo still has to wait for the OWNER's Matt setup, or can
  go straight to final verification because Matt's artifacts are there.

Each verb returns, besides the verifier's word, a `directive`: the exact
text the OWNER must see. The skills and the Stop hook are adapters -- they
collect answers, run one verb, and present the directive verbatim. They
carry no steps, no branching on gates, and no lifecycle knowledge of their
own.
"""
import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import output_styles  # noqa: E402
import evidence  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

VERIFIER = Path(__file__).resolve().parent / "verify.py"

# The immutable public branch this very build was released from
# (RICE-FINAL-001). Read from the build, never asked of upstream.
RELEASE_REF = settings_template.release_ref()
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
    return {"plugin_probe": _memoized(evidence._default_plugin_probe),
            "rtk_probe": _memoized(evidence._default_rtk_probe)}


@dataclass
class Outcome:
    """What a verb did: the verifier's last word (None when the stage never
    got far enough to verify), what repair restored on the way, whether the
    repo is now parked waiting for the OWNER's Matt setup, which lifecycle
    stage produced this, and the text the adapter must present verbatim."""
    report: verify.Report | None = None
    repaired: repair.RepairResult | None = None
    waiting_for_matt: bool = False
    stage: str = ""
    directive: list = field(default_factory=list)
    blocked: bool = False

    @property
    def ok(self) -> bool:
        """Did this stage complete everything that was not the OWNER's or the
        environment's to do? The verifier decides whenever it ran; otherwise
        the stage does -- handing off to the OWNER is a success, a probe that
        could not answer is not."""
        return self.report.ok if self.report is not None else not self.blocked

    @property
    def verdict(self) -> str:
        return self.report.verdict if self.report is not None else ""

    @property
    def lines(self) -> list[str]:
        out = []
        if self.repaired is not None:
            out += self.repaired.lines
        elif self.report is not None and self.report.ok:
            out.append("repair: not needed (normal path passed)")
        if self.report is not None:
            out += self.report.lines
        return out


def _precondition_stop(report: verify.Report) -> Outcome | None:
    """A blocked outcome when the verifier says this session must not change
    the repo at all (RICE-SPEC-001 I16): it is not running the installed
    build (an update not yet loaded), or the repo was finalized by a newer
    build. None when nothing blocks."""
    if not report.blocking:
        return None
    return Outcome(report=report, stage="blocked", blocked=True,
                   directive=_precondition_directive(report.blocking))


def _pipeline(repo_root, *, final: bool, carried: repair.RepairResult | None = None, **probes) -> Outcome:
    """The canonical flow, in the only place it exists as code. `carried` is
    what an earlier half of the same lifecycle already restored -- a Gate A
    repair stays in the report even when Gate B then runs cleanly, so the
    output never claims repair was "not needed" after it rewrote files.
    Repair never runs while a precondition fails: the wrong build must not
    rewrite the repo in the name of fixing it."""
    report = verify.run(repo_root, final=final, **probes)
    if report.ok:
        return Outcome(report=report, repaired=carried)
    stop = _precondition_stop(report)
    if stop is not None:
        return stop
    repaired = repair.repair(repo_root, report, **probes)
    if carried is not None:
        repaired = repair.RepairResult(applied=carried.applied + repaired.applied,
                                       unresolved=carried.unresolved + repaired.unresolved)
    return Outcome(report=verify.run(repo_root, final=final, **probes), repaired=repaired)


def _matt_started(repo_root, **probes) -> bool:
    """Has the OWNER's Matt setup left anything behind? Asked of reconcile,
    which owns what Matt's artifacts are -- never inspected here."""
    plugin_probe = probes.get("plugin_probe") or evidence._default_plugin_probe
    return any(evidence.matt_presence(Path(repo_root), plugin_probe).values())


# --------------------------------------------------------------------------
# the official install, as this environment actually has it
# --------------------------------------------------------------------------

def _canonical_install(**probes) -> tuple[list[str] | None, list[str]]:
    """(versions installed at the canonical scope, directive).

    The decision is `evidence.classify_install`'s -- the same one the
    verifier's `plugin:scope` item reports, so rice never holds a second
    truth about what a supported install is. What rice adds is the shape the
    OWNER needs: a definite stop with every scope named and the exact
    cleanup commands."""
    versions, state, detail, scopes = evidence.classify_install(probes.get("plugin_probe"))
    if state == "PASS":
        return versions, []
    if state != "FAIL":
        # could not classify: never a cleanup instruction, because there is no
        # runnable one to give. `scopes is None` = the probe could not answer;
        # `{}` = it answered, and rice is simply not installed.
        return None, (_install_directive() if scopes == {} else _install_unreadable_directive(detail))
    return None, _unsupported_install_directive(detail, scopes)


# --------------------------------------------------------------------------
# the four verbs
# --------------------------------------------------------------------------

def styles(repo_root, **probes) -> list[dict]:
    """Every output-style value that would resolve in this repo, for the
    OWNER's Step 1 choice."""
    plugin_probe = probes.get("plugin_probe") or evidence._default_plugin_probe
    return output_styles.available(repo_root, plugin_probe,
                                   self_plugin=(settings_template.PLUGIN_NAME, evidence.PLUGIN_ROOT))


def bootstrap(repo_root, *, archive=None, output_style=UNSET, **probes) -> Outcome:
    """A fresh repo, up to (and when possible past) the Matt hand-off.

    Rice must be loaded before any of this means anything, so that is asked
    first and nothing is written when it is not. Then the OWNER's answers are
    written once if absent, rice-owned state is reconciled, and Gate A goes
    through the canonical flow. On a Gate A pass the repo enters the pending
    state -- and if Matt's artifacts are already present (a re-bootstrap, or
    a repo that had them all along), final verification happens here and now
    rather than asking the OWNER for a setup they have already done. Either
    way the OWNER invokes bootstrap exactly once (RICE-BOOTSTRAP-028), in one
    session, and never has to restart or update anything (RICE-FINAL-001)."""
    repo_root = Path(repo_root)
    loaded = verify.run(repo_root, **probes)
    installed = loaded.item("plugin:installed")
    state = installed.state if installed is not None else "MISSING"
    if state == "FAIL":
        return Outcome(report=loaded, stage="install-required", blocked=True,
                       directive=_install_directive())
    if state != "PASS":
        # the probe could not answer -- not the same as "rice is not installed"
        return Outcome(report=loaded, stage="blocked", blocked=True,
                       directive=_unknown_install_directive(installed))
    # ... and the install must be the supported one before anything is written
    _, install_directive = _canonical_install(**probes)
    if install_directive:
        return Outcome(report=loaded, stage="blocked", blocked=True, directive=install_directive)
    # ... and this session must be the build that may change this repo (I16)
    stop = _precondition_stop(loaded)
    if stop is not None:
        return stop

    reconcile.write_owner_choices(repo_root, archive=archive, output_style=output_style)
    reconcile.apply(repo_root)

    gate_a = _pipeline(repo_root, final=False, **probes)
    if not gate_a.ok:
        return Outcome(report=gate_a.report, repaired=gate_a.repaired, stage="blocked",
                       directive=_blockers_directive(gate_a.report))
    reconcile.enter_waiting_for_matt(repo_root)
    matt = gate_a.report.item("matt:artifacts")
    if matt is not None and matt.state == "PASS":
        final = _pipeline(repo_root, final=True, carried=gate_a.repaired, **probes)
        final.stage = "finalized" if final.ok else "blocked"
        final.directive = (_finalized_directive(final.report.verdict) if final.ok
                           else _blockers_directive(final.report))
        return final
    return Outcome(report=gate_a.report, repaired=gate_a.repaired, waiting_for_matt=True,
                   stage="waiting-for-matt", directive=_matt_handoff_directive(gate_a.report.verdict))


def continue_after_matt(repo_root, **probes) -> Outcome | None:
    """The Stop hook's verb. None means "nothing to continue" -- this repo
    is not mid-bootstrap, or the OWNER's Matt setup has not started, and the
    turn must not be disturbed. Anything else goes through the canonical
    flow to Gate B, so an incomplete Matt setup is reported rather than left
    silently pending (RICE-BOOTSTRAP-042)."""
    repo_root = Path(repo_root)
    if not reconcile.is_waiting_for_matt(repo_root):
        return None
    if not _matt_started(repo_root, **probes):
        return None
    outcome = _pipeline(repo_root, final=True, **probes)
    outcome.stage = "finalized" if outcome.ok else "blocked"
    return outcome


# --------------------------------------------------------------------------
# directives: the exact text the OWNER must see, decided here, presented by
# the adapter verbatim
# --------------------------------------------------------------------------

def _install_directive() -> list[str]:
    return [
        "rice isn't loaded in this environment yet, so nothing has been written to this repo.",
        "The official install is:",
        "",
        f"    claude plugin marketplace add {settings_template.MARKETPLACE_REPO}",
        f"    claude plugin install {settings_template.PLUGIN_KEY}",
        "",
        f"(That installs whatever is newest. To pin this exact version instead, add @{RELEASE_REF} to the first line.)",
        "",
        "- Local machine: those two commands can be run now, then start a new session",
        "  and ask for the bootstrap there.",
        "- Claude Code on the web: the fix belongs at the Environment level, not this",
        "  session -- add those two commands to a Cloud Environment's setup script (a",
        "  dedicated rice Environment, or an existing one alongside mattpocock-skills /",
        "  RTK), then start a new session from it. Every session from that Environment",
        "  onward already has rice loaded. Running them only inside this session is a",
        "  one-off recovery: it does not survive to the next container.",
    ]


def _matt_handoff_directive(verdict: str) -> list[str]:
    """The terse hand-off, and nothing after it (RICE-BOOTSTRAP-028)."""
    return [
        f"`{verdict}`",
        "",
        "還差 Matt repo setup；完成後 Rice 會自動續接 final verification。",
        "",
        "/mattpocock-skills:setup-matt-pocock-skills",
    ]


def _finalized_directive(verdict: str) -> list[str]:
    return [
        f"`{verdict}`",
        "",
        "The verifier has stamped the verified-version file. Commit and push the"
        " changes in the working tree and quote that line verbatim. This repo needs"
        " no Matt hand-off: the setup is already there.",
    ]


def _blockers_directive(report) -> list[str]:
    return [
        "Deterministic repair has already had its turn; what is left is yours, and"
        " only what is listed:",
        *[f"  - {r}" for r in report.reasons],
        "",
        "Do exactly what each line's detail says, then run this same command again."
        " Never write Matt setup content yourself -- a missing Matt artifact is the"
        " OWNER's to produce, with /mattpocock-skills:setup-matt-pocock-skills.",
    ]


def _precondition_directive(reasons) -> list[str]:
    return [
        "This session's rice must not change this repo, so nothing was written:",
        *[f"  - {r}" for r in reasons],
        "",
        "Report this to the OWNER and stop. Do not install, update or reload rice"
        " yourself; once the OWNER has fixed it, this same command runs normally.",
    ]


def _unknown_install_directive(item) -> list[str]:
    return [
        "Whether rice is loaded here could not be determined, so nothing was written:",
        f"  - {item.detail if item is not None else 'the verifier produced no install result'}",
        "",
        "Report this and stop. Do not install, and do not assume either answer.",
    ]


def _unsupported_install_directive(detail: str, scopes: dict) -> list[str]:
    """The verifier's own sentence about this install, plus the cleanup as
    runnable lines. Rice removes nothing and picks no winner."""
    return [
        f"{detail}.",
        "",
        "Installed right now:",
        *[f"  - {scope} scope: {sorted(set(v))}" for scope, v in sorted(scopes.items())],
        "",
        "Minimal cleanup -- keep the canonical"
        f" {evidence.CANONICAL_SCOPE}-scope install, remove every other rice install:",
        *[f"    claude plugin uninstall {settings_template.PLUGIN_KEY} --scope {scope}"
          for scope in sorted(scopes) if scope != evidence.CANONICAL_SCOPE],
        *([f"    claude plugin install {settings_template.PLUGIN_KEY}"]
          if evidence.CANONICAL_SCOPE not in scopes else []),
        "",
        "Nothing was written. Rice will not remove either install for you, and will not"
        " guess which one should win. Do the cleanup, then bootstrap again.",
    ]


def _install_unreadable_directive(err: str) -> list[str]:
    return [
        f"The install state could not be read ({err}), so there is no way to tell"
        " which build this session would load. Nothing was written.",
        "",
        "Report this and stop -- rice never bootstraps against an unknown install.",
    ]


def continuation_message(repo_root, outcome: Outcome) -> str:
    """What the Stop hook hands back to the model. The verifier's own verdict
    and reasons, verbatim, plus the one instruction that belongs to this
    lifecycle: finish the job in this same turn, and never write Matt's
    content or send the OWNER back to rice."""
    if outcome.blocked and outcome.directive:
        return "rice: " + "\n".join(outcome.directive) + "\nThis hook stays quiet for the rest of this turn."
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
    ap = argparse.ArgumentParser(description="rice lifecycle: bootstrap / continue / styles")
    sub = ap.add_subparsers(dest="verb", required=True)
    b = sub.add_parser("bootstrap", help="configure this repo, up to (or past) the Matt hand-off")
    b.add_argument("repo_root", nargs="?", default=".")
    b.add_argument("--archive", choices=("0", "1"), help="the OWNER's session-archive choice (written only if absent)")
    b.add_argument("--output-style", help="the OWNER's style choice, or 'none' for Claude's default (written only if absent)")
    for verb, help_text in (("continue", "finish a bootstrap after the OWNER's Matt setup"),
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
        outcome = bootstrap(args.repo_root,
                            archive=None if args.archive is None else args.archive == "1",
                            output_style=style, **probes)
    else:
        outcome = continue_after_matt(args.repo_root, **probes)
        if outcome is None:
            print("continue: nothing to continue (not mid-bootstrap, or Matt setup has not started)")
            return 0
    print("\n".join(outcome.lines))
    if outcome.directive:
        print("\n--- present this to the OWNER verbatim ---")
        print("\n".join(outcome.directive))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
