"""Ticket #25: the tiny WAITING_FOR_MATT pending marker bootstrap creates.

Per #13 directive H: a deterministic marker, not Claude remembering across
turns, that a fresh session can check before redoing already-finished
bootstrap work. Clearing it automatically once Matt setup is verified
complete is #28's job (blocked on #27's runtime probe for the completion-
detection mechanism) -- this module only provides what #25 itself needs:
entering the pending state, and checking it.
"""
from pathlib import Path

MARKER_NAME = "WAITING_FOR_MATT"

_MARKER_CONTENT = (
    "rice bootstrap is waiting for you to personally run the official\n"
    "Matt Pocock setup skill (setup-matt-pocock-skills). Claude cannot\n"
    "invoke it for you -- see .claude/rice.md.\n"
    "\n"
    "This file is removed automatically once that setup is verified\n"
    "complete.\n"
)


def _path(repo_root) -> Path:
    return Path(repo_root) / ".claude" / MARKER_NAME


def enter_waiting_for_matt(repo_root) -> None:
    p = _path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(_MARKER_CONTENT, encoding="utf-8")


def is_waiting_for_matt(repo_root) -> bool:
    return _path(repo_root).exists()
