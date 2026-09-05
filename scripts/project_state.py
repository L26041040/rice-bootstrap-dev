"""Ticket #21: the PROJECT_STATE.md lifecycle module.

Exactly two callers exist for this module: bootstrap (create_if_missing)
and rice-update (preserve_or_restore). Ordinary reconcile (apply/check,
ticket #23) must NEVER call anything here -- see #13's Ownership model:
PROJECT_STATE.md's existence is guaranteed at these two lifecycle
boundaries, its content is never inspected or rewritten by anything else
rice does.
"""
from pathlib import Path

FILENAME = "PROJECT_STATE.md"

TEMPLATE = """# PROJECT_STATE

## Now
(phase / objective)

## Next
(what's next)

## Blockers
(open questions / blockers, or "none")

## Last updated
(date + one-line source)
"""


def _path(repo_root) -> Path:
    return Path(repo_root) / FILENAME


def create_if_missing(repo_root) -> None:
    """Bootstrap's call: write the empty template if the file doesn't
    exist yet. Never touches existing content."""
    p = _path(repo_root)
    if not p.exists():
        p.write_text(TEMPLATE)


def preserve_or_restore(repo_root) -> None:
    """rice-update's call: if the file exists, leave it exactly as is --
    never read for content, never rewritten. If it's missing, restore the
    same empty template bootstrap would have written."""
    p = _path(repo_root)
    if not p.exists():
        p.write_text(TEMPLATE)
    # else: do nothing at all -- existence alone is enough.
