#!/usr/bin/env python3
"""RICE-BOOTSTRAP-028: the Stop hook that continues bootstrap after the
OWNER's Matt setup, so a fresh bootstrap costs exactly one `/rice:bootstrap`.

Wired in the plugin's hooks.json as a no-matcher Stop hook, so Claude Code
itself decides when this runs: at the end of every assistant turn. Almost
none of those turns are a bootstrap continuation, so every guard below exits
silently (status 0, no output) and an ordinary turn never notices this hook
exists.

The one turn that matters is the one where the OWNER's Matt setup finished:
`WAITING_FOR_MATT` is still pending, Matt's artifacts have appeared, and RTK
is present. Then, and only then, this asks the verifier for Gate B and hands
the result back to the model with exit 2 + stderr -- the mechanism
RICE-BOOTSTRAP-027's runtime probe confirmed reaches the model as actionable
instruction, letting the same session finish the job.

Authority is unchanged (RICE-BOOTSTRAP-021): this hook decides nothing. It
never writes the verified-version file, never removes the `WAITING_FOR_MATT`
marker, and never runs a git command. `verify.py` stamps the version and
clears the marker itself on a Gate B pass, and its own verdict line is
relayed verbatim rather than reconstructed here; the model does the commit
and push. This hook only decides *when to ask the verifier*.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap_state  # noqa: E402
import reconcile  # noqa: E402
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
    the verifier ask the same two questions (`claude plugin list --json`,
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


def _ready(repo_root: Path, probes: dict) -> bool:
    """Presence-only readiness: has the OWNER's Matt setup landed, and is RTK
    there? Both are Gate B items; asking the verifier before they exist would
    only produce noise on every turn."""
    matt = reconcile._matt_item(repo_root, probes["plugin_probe"])
    if matt.state != "PASS":
        return False
    return reconcile._rtk_item(probes["rtk_probe"]).state == "PASS"


def _message(repo_root: Path, report) -> str:
    if report.gate_b:
        verdict = next((l for l in report.lines if l.startswith("FINAL-")), "")
        return (
            f"rice: {verdict}. The verifier has stamped the verified-version file "
            "and cleared the pending marker. Commit and push the bootstrap, Matt "
            f"and finalization changes in the working tree, then report `{verdict}` "
            "to the OWNER."
        )
    _, reasons = verify.gate_passes(report.items, verify.GATE_B)
    return (
        "rice: final verification did not pass; the verified-version file is "
        "untouched. Blockers:\n"
        + "\n".join(f"  - {r}" for r in reasons)
        + "\nFix exactly what each line names, then finish the job in this same "
        f"turn by running `python3 {VERIFIER} {repo_root} --final` yourself. This "
        "hook stays quiet for the rest of this turn, and the OWNER is owed no "
        "action here -- ask them for nothing."
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
    if not _ready(repo_root, probes):
        return SILENT  # Matt setup and/or RTK not there yet

    report = verify.run(repo_root, final=True, **probes)
    # Past this line the verifier may already have stamped the version and
    # cleared the marker -- a durable change this hook will never see again.
    # So a failure to *relay* the result must still extend the turn; falling
    # back to silence here would strand a finished bootstrap uncommitted.
    try:
        print(_message(repo_root, report), file=sys.stderr)
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
