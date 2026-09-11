#!/usr/bin/env python3
"""The Stop hook adapter (RICE-BOOTSTRAP-028/-044).

Claude Code runs this at the end of every assistant turn. It carries no
bootstrap steps of its own: it turns a Stop payload into one call of the
lifecycle engine's `continue` verb and turns the answer back into a hook
result. Everything about *what* continuing means -- when there is something
to continue, the verify/repair/verify flow, what the model is told -- lives
in `rice.py`.

Three guards belong here because they are facts about the turn, not about
the lifecycle: a payload without a cwd, a turn this hook already spoke in
(`stop_hook_active`, the re-entry guard RICE-BOOTSTRAP-027's runtime probe
confirmed), and the engine answering "nothing to continue". Each exits
silently -- status 0, no output -- so an ordinary turn never notices this
hook exists.

Authority is unchanged (RICE-BOOTSTRAP-021): nothing here writes a file,
clears the pending marker, repairs anything, or runs a git command.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rice  # noqa: E402

SILENT = 0  # nothing to do: no output, ordinary turn undisturbed
HANDOFF = 2  # stderr reaches the model (RICE-BOOTSTRAP-027 probe)


def _payload() -> dict:
    """The Stop payload, or {} if it is missing/unreadable -- a malformed
    payload is a silent no-op, never an error surfaced to the OWNER."""
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _run() -> int:
    payload = _payload()
    cwd = payload.get("cwd") or ""
    if not cwd:
        return SILENT
    if payload.get("stop_hook_active"):
        return SILENT  # this hook already spoke on the previous Stop

    repo_root = Path(cwd)
    outcome = rice.continue_after_matt(repo_root, **rice.default_probes())
    if outcome is None:
        return SILENT  # not mid-bootstrap, or Matt's setup has not started

    # Past this line the verifier may already have stamped the version and
    # cleared the marker -- a durable change this hook will never see again.
    # So a failure to *relay* the result must still extend the turn; falling
    # back to silence here would strand a finished bootstrap uncommitted.
    try:
        print(rice.continuation_message(repo_root, outcome), file=sys.stderr)
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
