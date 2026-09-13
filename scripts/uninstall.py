#!/usr/bin/env python3
"""rice uninstall: remove rice-owned / rice-created state from a target repo,
then mechanically check it is gone (RICE-BOOTSTRAP-025/-045).

This script decides nothing about what rice owns. `reconcile` is the single
declaration of that (RICE-BOOTSTRAP-045), and the same declaration drives
install, update, repair and this removal -- so what rice adds and what rice
takes away can never drift apart. What lives here is the part that is
uninstall's alone: a snapshot of the project-owned view taken BEFORE the
removal, and a check afterwards that every byte of it survived.

Deterministic subtraction, never inference. Anything that is not exactly a
rice entry is project-owned and is left byte-for-byte alone -- other
plugins, other marketplaces, permissions.allow/ask, deny rules rice did not
add, project env vars, hooks, custom output styles, session-history/, Matt
artifacts, RTK, repo files, git history.

This script never touches the user-level plugin / marketplace install:
other repos in the same environment may still use rice. That is a separate
OWNER question the skill asks afterwards.

Only this script's `check` may print RICE-UNINSTALLED, and only when every
item is exactly PASS. It is a separate lifecycle from verify.py's gates.
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402
from verify import VERSION_FILE  # noqa: E402  -- owned by verify.py; here it is only unlinked


@dataclass
class Item:
    name: str
    state: str  # PASS / FAIL / ERROR
    detail: str = ""


def expected_after(repo_root) -> dict:
    """Snapshot, taken BEFORE uninstall, of what must be on disk AFTER it:
    the project-owned view of every file rice touches, plus every other file
    byte-for-byte. `check(expected=...)` compares against this -- project
    content that changed during uninstall is a FAIL, whatever the reason."""
    repo_root = Path(repo_root)
    exp = {"files": {}, "settings": None, "claude_md": None, "agents_md": None}
    s_path = repo_root / ".claude" / "settings.json"
    if s_path.exists():
        current = reconcile.current_settings(repo_root)
        exp["settings"] = reconcile.project_settings(repo_root, current) if current is not None else None
    for key, rel, project_view in (("claude_md", "CLAUDE.md", reconcile.project_claude_md),
                                   ("agents_md", "AGENTS.md", reconcile.project_agents_md)):
        path = repo_root / rel
        if path.exists():
            exp[key] = project_view(path.read_text(encoding="utf-8"))
    # everything rice may rewrite or remove is compared through its
    # project-owned view above, not byte-for-byte here
    skip = {".claude/settings.json", "CLAUDE.md", "AGENTS.md", VERSION_FILE, *reconcile.OWNED_FILES}
    for p in repo_root.rglob("*"):
        if p.is_file() and ".git" not in p.relative_to(repo_root).parts:
            rel = str(p.relative_to(repo_root))
            if rel not in skip:
                exp["files"][rel] = p.read_bytes()
    return exp


def uninstall(repo_root) -> list[str]:
    """Remove rice state. Returns the actions taken. Idempotent: on a repo
    with no rice state it does nothing and returns []."""
    repo_root = Path(repo_root)
    version = repo_root / VERSION_FILE
    actions = []
    if version.exists():
        version.unlink(); actions.append(f"removed {VERSION_FILE}")
    actions += reconcile.remove(repo_root)
    claude_dir = repo_root / ".claude"
    if claude_dir.is_dir() and not any(claude_dir.iterdir()):
        claude_dir.rmdir(); actions.append("removed empty .claude/")
    return actions


def _collect_items(repo_root: Path, expected: dict | None) -> list[Item]:
    items = [Item("verified-version:absent",
                  "FAIL" if (repo_root / VERSION_FILE).exists() else "PASS")]
    for label, detail in reconcile.remnants(repo_root).items():
        items.append(Item(label, "FAIL" if detail else "PASS", detail))

    if expected is not None:
        s_path = repo_root / ".claude" / "settings.json"
        exp_s = expected["settings"]
        actual_s = reconcile.current_settings(repo_root) if s_path.exists() else None
        if not exp_s:
            items.append(Item("sentinel:settings.json", "PASS" if not s_path.exists() else "FAIL",
                              "expected removal (held only rice entries)" if exp_s == {} else "expected no settings.json"))
        else:
            items.append(Item("sentinel:settings.json", "PASS" if actual_s == exp_s else "FAIL",
                              "project-owned settings differ from the pre-uninstall projection"))
        for key, rel in (("claude_md", "CLAUDE.md"), ("agents_md", "AGENTS.md")):
            path = repo_root / rel
            actual = path.read_text(encoding="utf-8") if path.exists() else None
            items.append(Item(f"sentinel:{rel}", "PASS" if actual == expected[key] else "FAIL",
                              f"project-owned {rel} content differs"))
        for rel, data in sorted(expected["files"].items()):
            p = repo_root / rel
            ok = p.is_file() and p.read_bytes() == data
            items.append(Item(f"sentinel:{rel}", "PASS" if ok else "FAIL", "" if ok else "changed or missing"))
    return items


def verdict(items: list[Item]) -> str:
    bad = [i for i in items if i.state != "PASS"]
    if not items or bad:
        return f"RICE-UNINSTALL FAIL ({len(bad) if items else 'no items'} not PASS)"
    return "RICE-UNINSTALLED"


def check(repo_root, *, expected: dict | None = None) -> tuple[list[Item], str]:
    repo_root = Path(repo_root)
    try:
        items = _collect_items(repo_root, expected)
    except Exception as exc:  # noqa: BLE001 - fail closed
        items = [Item("uninstall-check", "ERROR", f"{type(exc).__name__}: {exc}")]
    return items, verdict(items)


def render(items, actions, v) -> str:
    lines = [f"  {a}" for a in actions]
    lines += [f"{i.state:<5} {i.name}" + (f" -- {i.detail}" if i.detail else "") for i in items]
    lines.append(v)
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice uninstall: remove rice-owned state from a repo and check it is gone")
    ap.add_argument("repo_root", nargs="?", default=".")
    ap.add_argument("--check-only", action="store_true", help="only run the check; remove nothing")
    args = ap.parse_args(argv)
    root = Path(args.repo_root)
    if args.check_only:
        items, v = check(root)
        print(render(items, [], v))
        return 0 if v == "RICE-UNINSTALLED" else 1
    expected = expected_after(root)
    actions = uninstall(root)
    items, v = check(root, expected=expected)
    print(render(items, actions, v))
    return 0 if v == "RICE-UNINSTALLED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
