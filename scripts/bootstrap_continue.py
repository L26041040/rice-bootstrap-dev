#!/usr/bin/env python3
"""RICE-BOOTSTRAP-028: the Stop hook that continues bootstrap after the
OWNER's Matt setup, so a fresh bootstrap costs exactly one `/rice:bootstrap`.

Wired in the plugin's hooks.json as a no-matcher Stop hook, so Claude Code
itself decides when this runs: at the end of every assistant turn. Almost
none of those turns are a bootstrap continuation, so every guard below exits
silently (status 0, no output) and an ordinary turn never notices this hook
exists.

The one turn that matters is the one where the OWNER's Matt setup has run:
`WAITING_FOR_MATT` is still pending and Matt's setup has left anything at
all behind. Then this hands the repo to the one finalization pipeline
(`repair.pipeline`, RICE-BOOTSTRAP-042): verify, deterministic repair on a
FAIL, verify again -- and relays the result to the model with exit 2 +
stderr, the mechanism RICE-BOOTSTRAP-027's runtime probe confirmed reaches
the model as actionable instruction, letting the same session finish the
job. Until Matt's setup has left a trace there is nothing to continue and
the hook stays silent; from the first trace on it never goes silent again
on an incomplete setup (RICE-BOOTSTRAP-042): what repair cannot restore
without guessing is reported, turn after turn, until it is resolved.

Authority is unchanged (RICE-BOOTSTRAP-021): this hook decides nothing. It
never writes the verified-version file, never removes the `WAITING_FOR_MATT`
marker, never repairs anything itself and never runs a git command.
`verify.py` stamps the version and clears the marker itself on a Gate B
pass, and its own verdict line is relayed verbatim rather than
reconstructed here; the model does the commit and push. This hook only
decides *when to run the pipeline*.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap_state  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import verify  # noqa: E402

SILENT = 0  # nothing to do: no output, ordinary turn undisturbed
HANDOFF = 2  # stderr reaches the model (RICE-BOOTSTRAP-027 probe)

VERIFIER = Path(__file__).resolve().parent / "verify.py"


def _payload() -> dict:
    """The Stop payload, or {} if it is missing/unreadable -- a malformed
    payload is a silent no-op, never an error surfaced to the OWNER."""
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _memoized(probe):
    """One external command, one spawn per hook run. The readiness check and
    the pipeline's two verifier runs ask the same two questions (`claude plugin list --json`,
    `rtk --version`); on a Stop hook that runs at the end of every turn, the
    duplicate spawns are pure latency."""
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


def _matt_attempted(repo_root: Path, probes: dict) -> bool:
    """Has the OWNER's Matt setup left anything behind? Before that there is
    nothing to continue; running the pipeline on every turn in between would
    only push "Matt is missing" at the model while the OWNER is still being
    asked to run it."""
    return any(reconcile.matt_presence(repo_root, probes["plugin_probe"]).values())


def _message(repo_root: Path, outcome: repair.Outcome) -> str:
    report = outcome.report
    if report.gate_b:
        verdict = next((l for l in report.lines if l.startswith("FINAL-")), "")
        repaired = ""
        if outcome.repair and outcome.repair.applied:
            repaired = (" Deterministic repair restored:\n"
                        + "\n".join(f"  - {a}" for a in outcome.repair.applied) + "\n")
        return (
            f"rice: {verdict}. The verifier has stamped the verified-version file "
            f"and cleared the pending marker.{repaired} Commit and push the bootstrap, "
            f"Matt and finalization changes in the working tree, then report `{verdict}` "
            "to the OWNER."
        )
    _, reasons = verify.gate_passes(report.items, verify.GATE_B)
    done = outcome.repair
    repaired = "\n".join(f"  - {a}" for a in done.applied) if done and done.applied else "  - nothing was mechanically repairable"
    # repair passes non-repairable items through in the verifier's own words,
    # so only what it adds (Matt artifacts it refused to rebuild) is new here
    extra = [u for u in (done.unresolved if done else []) if u not in reasons]
    refused = "\n".join(f"  - {u}" for u in extra)
    return (
        "rice: final verification did not pass; the verified-version file is "
        "untouched.\nDeterministic repair ran first:\n" + repaired
        + "\nStill blocking after repair:\n" + "\n".join(f"  - {r}" for r in reasons)
        + (("\nRepair refused to guess at:\n" + refused) if refused else "")
        + "\nYou are the last fallback. Fix exactly what each line names; never "
        "write Matt setup content yourself -- a Matt artifact listed above must be "
        "reported to the OWNER as missing, by name, because only the official Matt "
        "setup can produce it. Then finish the job in this same turn by running "
        f"`python3 {VERIFIER} {repo_root} --final` yourself. This hook stays quiet "
        "for the rest of this turn."
    )


def _run() -> int:
    payload = _payload()
    cwd = payload.get("cwd") or ""
    if not cwd:
        return SILENT
    repo_root = Path(cwd)

    if not bootstrap_state.is_waiting_for_matt(repo_root):
        return SILENT  # not mid-bootstrap: the overwhelmingly common case
    if payload.get("stop_hook_active"):
        return SILENT  # this hook already spoke on the previous Stop
    probes = {"plugin_probe": _memoized(reconcile._default_plugin_probe),
              "rtk_probe": _memoized(reconcile._default_rtk_probe)}
    if not _matt_attempted(repo_root, probes):
        return SILENT  # the OWNER has not run Matt's setup yet

    outcome = repair.pipeline(repo_root, final=True, **probes)
    # Past this line the verifier may already have stamped the version and
    # cleared the marker -- a durable change this hook will never see again.
    # So a failure to *relay* the result must still extend the turn; falling
    # back to silence here would strand a finished bootstrap uncommitted.
    try:
        print(_message(repo_root, outcome), file=sys.stderr)
    except Exception:  # noqa: BLE001 - the exit status is the last signal left
        pass
    return HANDOFF


def main() -> int:
    try:
        return _run()
    except Exception:  # noqa: BLE001 - a hook fault must never break a turn
        return SILENT


if __name__ == "__main__":
    raise SystemExit(main())
