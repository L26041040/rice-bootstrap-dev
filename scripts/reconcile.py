"""Ticket #23: the single deterministic apply/check primitive.

Both `apply` and `check` operate only against the *currently pinned* ref's
desired state -- the ref itself is read from the target's own
.claude/settings.json (whatever bootstrap or rice-update last set it to),
never supplied as a parameter and never written here. See #13's
"Reconcile, check, and rice-update": rice-update alone may change `ref`,
by writing it directly and then invoking this module's `apply` with
`is_rice_update=True` to converge everything else. Ordinary reconcile
(apply/check with is_rice_update=False, the default) never does either.

Four-state logic (missing / match / drift-conflict / drift-overwritten)
applies to rice-defined settings keys and to the .claude/rice.md
whole-file case. Owner-defined settings keys (`ref`, `RICE_SESSION_ARCHIVE`)
are presence-only from this module's point of view: it is never correct
for apply to create, fix, or reassert them, in any mode, ever -- that is
bootstrap's job at first-write. PROJECT_STATE.md is out of scope entirely:
this module never reads its content, never creates it, never restores it
(see #21) -- reconcile does not even import project_state.

Matt setup artifacts and RTK are presence-only, never content-validated,
and never written by apply -- Matt/environment own that content.

This module has no git responsibility of its own: it writes files, it
does not commit them. Committing rice-owned changes is the caller's job
(bootstrap #25 / rice-update #26).
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import settings_template

IMPORT_LINE = "@.claude/rice.md"
RICE_MD_RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "rice.md"

REF_PATH = settings_template.REF_PATH
# settings_template's classification sets hold real dotted paths into the
# official settings shape, usable verbatim for lookup. Sorted so report
# order is stable.
_RICE_LEAF_KEYS = sorted(settings_template.RICE_DEFINED_KEYS)
_OWNER_LEAF_KEYS = sorted(settings_template.OWNER_DEFINED_KEYS)

_MISSING = object()


@dataclass
class ItemResult:
    name: str
    state: str
    detail: str = ""


def _get_path(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _set_path(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _current_settings(repo_root: Path) -> dict:
    p = repo_root / ".claude" / "settings.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _current_ref(settings: dict) -> str | None:
    v = _get_path(settings, REF_PATH)
    return None if v is _MISSING else v


def _settings_items(current: dict, ref: str | None, *, write: bool, is_rice_update: bool):
    """Returns (items, current) -- `current` is mutated in place when
    write=True and an item needs creating/updating."""
    if ref is None:
        # Same item name the ref-present path below would use for this key
        # (settings:{REF_PATH}), so check()/apply() output is consistent
        # regardless of which branch produced it.
        return [ItemResult(
            f"settings:{REF_PATH}", "FAIL",
            f"{REF_PATH} is missing -- cannot determine the pinned version; run bootstrap",
        )], current

    desired = settings_template.desired_settings(ref, True)  # archive_default is inert here:
    # env.RICE_SESSION_ARCHIVE is owner-defined and never taken from `desired`.

    items = []
    for key in _RICE_LEAF_KEYS:
        cur = _get_path(current, key)
        des = _get_path(desired, key)
        if cur is _MISSING:
            if write:
                _set_path(current, key, des)
                items.append(ItemResult(f"settings:{key}", "created", f"set to {des!r}"))
            else:
                items.append(ItemResult(f"settings:{key}", "FAIL", f"missing; desired={des!r}"))
        elif cur == des:
            items.append(ItemResult(f"settings:{key}", "noop" if write else "PASS"))
        elif write and is_rice_update:
            _set_path(current, key, des)
            items.append(ItemResult(f"settings:{key}", "updated", f"{cur!r} -> {des!r}"))
        else:
            items.append(ItemResult(
                f"settings:{key}", "conflict" if write else "FAIL",
                f"drift: current={cur!r} desired={des!r} (not overwritten)",
            ))

    for key in _OWNER_LEAF_KEYS:
        cur = _get_path(current, key)
        if cur is _MISSING:
            items.append(ItemResult(
                f"settings:{key}", "FAIL",
                "owner-defined key missing; reconcile never creates it -- run bootstrap",
            ))
        else:
            items.append(ItemResult(
                f"settings:{key}", "noop" if write else "PASS",
                "owner-defined; present, value not compared or reasserted",
            ))
    return items, current


def _rice_md_item(repo_root: Path, *, write: bool, is_rice_update: bool) -> ItemResult:
    desired = RICE_MD_RESOURCE.read_text(encoding="utf-8")
    dest = repo_root / ".claude" / "rice.md"
    if not dest.exists():
        if write:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(desired, encoding="utf-8")
            return ItemResult("rice.md", "created")
        return ItemResult("rice.md", "FAIL", "missing; run apply")
    current = dest.read_text(encoding="utf-8")
    if current == desired:
        return ItemResult("rice.md", "noop" if write else "PASS")
    if write and is_rice_update:
        dest.write_text(desired, encoding="utf-8")
        return ItemResult("rice.md", "updated")
    return ItemResult(
        "rice.md", "conflict" if write else "FAIL",
        "hand-edited or drifted at the same version; not overwritten",
    )


def _claude_md_item(repo_root: Path, *, write: bool) -> ItemResult:
    dest = repo_root / "CLAUDE.md"
    if not dest.exists():
        if write:
            dest.write_text(f"{IMPORT_LINE}\n", encoding="utf-8")
            return ItemResult("CLAUDE.md:import-line", "created", "CLAUDE.md did not exist; created with only the import line")
        return ItemResult("CLAUDE.md:import-line", "FAIL", "CLAUDE.md missing")
    content = dest.read_text(encoding="utf-8")
    if any(line.strip() == IMPORT_LINE for line in content.splitlines()):
        return ItemResult("CLAUDE.md:import-line", "noop" if write else "PASS")
    if write:
        sep = "" if content.endswith("\n") else "\n"
        dest.write_text(f"{content}{sep}\n{IMPORT_LINE}\n", encoding="utf-8")
        return ItemResult("CLAUDE.md:import-line", "created", "import line appended; rest of file untouched")
    return ItemResult("CLAUDE.md:import-line", "FAIL", "import line missing")


def _matt_item(repo_root: Path) -> ItemResult:
    """Presence-only, never content-validated, never written here -- Matt's
    artifacts remain permanently Matt/project-owned (#13 Ownership model).
    Shape (`## Agent skills` heading + a populated docs/agents/) is the
    real shape observed from this repo's own Matt setup run."""
    claude_md = repo_root / "CLAUDE.md"
    has_heading = claude_md.exists() and "## Agent skills" in claude_md.read_text(encoding="utf-8")
    docs_dir = repo_root / "docs" / "agents"
    has_docs = docs_dir.is_dir() and any(docs_dir.glob("*.md"))
    if has_heading and has_docs:
        return ItemResult("matt:artifacts", "PASS")
    return ItemResult("matt:artifacts", "FAIL", "Matt setup artifacts not found (## Agent skills / docs/agents/*.md)")


def _default_rtk_probe(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _rtk_item(rtk_probe) -> ItemResult:
    try:
        r = rtk_probe(["rtk", "--version"])
    except FileNotFoundError:
        return ItemResult("rtk", "FAIL", "rtk not found on PATH")
    except OSError as exc:
        return ItemResult("rtk", "NOT_VERIFIED", f"could not run rtk --version: {exc}")
    if r.returncode != 0:
        return ItemResult("rtk", "FAIL", f"rtk --version exited {r.returncode}")
    return ItemResult("rtk", "PASS", r.stdout.strip())


def check(repo_root, *, rtk_probe=_default_rtk_probe) -> tuple[list[ItemResult], str]:
    repo_root = Path(repo_root)
    current = _current_settings(repo_root)
    ref = _current_ref(current)
    items, _ = _settings_items(current, ref, write=False, is_rice_update=False)
    items.append(_rice_md_item(repo_root, write=False, is_rice_update=False))
    items.append(_claude_md_item(repo_root, write=False))
    items.append(_matt_item(repo_root))
    items.append(_rtk_item(rtk_probe))

    passed = sum(1 for i in items if i.state == "PASS")
    failed = sum(1 for i in items if i.state == "FAIL")
    not_verified = sum(1 for i in items if i.state == "NOT_VERIFIED")
    summary = f"RICE-CHECK: {passed} PASS, {failed} FAIL, {not_verified} NOT_VERIFIED ({len(items)} total)"
    return items, summary


def apply(repo_root, *, is_rice_update: bool = False, rtk_probe=_default_rtk_probe) -> list[ItemResult]:
    repo_root = Path(repo_root)
    settings_path = repo_root / ".claude" / "settings.json"
    current = _current_settings(repo_root)
    ref = _current_ref(current)
    items, mutated = _settings_items(current, ref, write=True, is_rice_update=is_rice_update)
    if any(i.state in ("created", "updated") for i in items):
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(mutated, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    items.append(_rice_md_item(repo_root, write=True, is_rice_update=is_rice_update))
    items.append(_claude_md_item(repo_root, write=True))
    items.append(_matt_item(repo_root))
    items.append(_rtk_item(rtk_probe))
    return items
