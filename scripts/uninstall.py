#!/usr/bin/env python3
"""rice uninstall: remove rice-owned / rice-created state from a target repo,
then mechanically check it is gone (RICE-BOOTSTRAP-025).

Deterministic subtraction, never inference: every removal is an exact
match against this build's canonical values (settings_template). Anything
that is not an exact rice entry is project-owned and is left byte-for-byte
alone -- other plugins, other marketplaces, permissions.allow/ask, deny
rules rice did not add, project env vars, hooks, custom output styles,
session-history/, Matt artifacts, RTK, repo files, git history.

This script never touches the user-level plugin / marketplace install:
other repos in the same environment may still use rice. That is a
separate OWNER question the skill asks afterwards.

Only this script's `check` may print RICE-UNINSTALLED, and only when every
item is exactly PASS. It is a separate lifecycle from verify.py's gates.
"""
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402  -- for the AGENTS.md pointer-block shape only
import settings_template  # noqa: E402
from bootstrap_state import MARKER_NAME  # noqa: E402
from verify import VERSION_FILE  # noqa: E402  -- owned by verify.py; here it is only unlinked

IMPORT_LINE = "@.claude/rice.md"
_VERSION_NAME = Path(VERSION_FILE).name
RICE_FILES = ("rice.md", _VERSION_NAME, MARKER_NAME)  # under .claude/
_MP = settings_template.MARKETPLACE_NAME
_PLUGIN = settings_template.PLUGIN_KEY
_SONNET = settings_template.desired_settings("x", True)["env"]["CLAUDE_CODE_SUBAGENT_MODEL"]
RICE_DENY = tuple(settings_template.desired_settings("x", True)["permissions"]["deny"])


@dataclass
class Item:
    name: str
    state: str  # PASS / FAIL / ERROR
    detail: str = ""


# --------------------------------------------------------------------------
# pure projection: what a settings dict / CLAUDE.md look like without rice
# --------------------------------------------------------------------------

def _read_json(p: Path):
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def project_settings(s: dict) -> dict:
    """The settings dict minus exact rice entries. Empty containers rice
    emptied are dropped; containers with project content stay."""
    out = json.loads(json.dumps(s))  # deep copy, JSON-shaped
    mps = out.get("extraKnownMarketplaces")
    if isinstance(mps, dict):
        mps.pop(_MP, None)
        if not mps:
            out.pop("extraKnownMarketplaces")
    eps = out.get("enabledPlugins")
    if isinstance(eps, dict):
        eps.pop(_PLUGIN, None)
        if not eps:
            out.pop("enabledPlugins")
    env = out.get("env")
    if isinstance(env, dict):
        if env.get("CLAUDE_CODE_SUBAGENT_MODEL") == _SONNET:
            env.pop("CLAUDE_CODE_SUBAGENT_MODEL")
        env.pop("RICE_SESSION_ARCHIVE", None)
        if not env:
            out.pop("env")
    perms = out.get("permissions")
    if isinstance(perms, dict):
        deny = perms.get("deny")
        if isinstance(deny, list):
            kept = [d for d in deny if d not in RICE_DENY]
            if kept:
                perms["deny"] = kept
            else:
                perms.pop("deny")
        if not perms:
            out.pop("permissions")
    if out.get("outputStyle") == settings_template.RICE_STYLE:
        out.pop("outputStyle")
    return out


def project_claude_md(text: str) -> str | None:
    """CLAUDE.md without the rice import line (and the blank line reconcile
    put in front of it when appending). None = nothing project-owned was
    ever in it (rice created it with only the import line) -> remove."""
    lines = text.splitlines(keepends=True)
    kept = [l for l in lines if l.strip() != IMPORT_LINE]
    if not "".join(kept).strip():
        return None
    if len(kept) < len(lines):
        while len(kept) >= 2 and kept[-1].strip() == "" and kept[-2].endswith("\n"):
            kept.pop()
    return "".join(kept)


def project_agents_md(text: str) -> str | None:
    """AGENTS.md without the rice pointer block (and the blank line reconcile
    put in front of it when appending). None = nothing project-owned was
    ever in it (rice created it with only the block) -> remove."""
    span = reconcile._agents_block_span(text)
    if span is None:
        return text
    kept = text[:span[0]] + text[span[1]:]
    if not kept.strip():
        return None
    if kept.endswith("\n\n"):
        kept = kept[:-1]
    return kept


def expected_after(repo_root) -> dict:
    """Snapshot, taken BEFORE uninstall, of what must be on disk AFTER it:
    the projected settings/CLAUDE.md plus every other file byte-for-byte.
    `check(expected=...)` compares against this -- project content that
    changed during uninstall is a FAIL, whatever the reason."""
    repo_root = Path(repo_root)
    exp = {"files": {}, "settings": None, "claude_md": None, "agents_md": None}
    s_path = repo_root / ".claude" / "settings.json"
    if s_path.exists():
        s = _read_json(s_path)
        exp["settings"] = project_settings(s) if s is not None else None
    c_path = repo_root / "CLAUDE.md"
    if c_path.exists():
        exp["claude_md"] = project_claude_md(c_path.read_text(encoding="utf-8"))
    a_path = repo_root / "AGENTS.md"
    if a_path.exists():
        exp["agents_md"] = project_agents_md(a_path.read_text(encoding="utf-8"))
    skip = {".claude/settings.json", "CLAUDE.md", "AGENTS.md"} | {f".claude/{n}" for n in RICE_FILES}
    for p in repo_root.rglob("*"):
        if p.is_file() and ".git" not in p.relative_to(repo_root).parts:
            rel = str(p.relative_to(repo_root))
            if rel not in skip:
                exp["files"][rel] = p.read_bytes()
    return exp


# --------------------------------------------------------------------------
# the removal
# --------------------------------------------------------------------------

def uninstall(repo_root) -> list[str]:
    """Remove rice state. Returns the actions taken. Idempotent: on a repo
    with no rice state it does nothing and returns []."""
    repo_root = Path(repo_root)
    actions = []
    claude_dir = repo_root / ".claude"
    for name in RICE_FILES:
        p = claude_dir / name
        if p.exists():
            p.unlink(); actions.append(f"removed .claude/{name}")
    s_path = claude_dir / "settings.json"
    if s_path.exists():
        s = _read_json(s_path)
        if s is not None:
            projected = project_settings(s)
            if projected != s:
                if projected:
                    s_path.write_text(json.dumps(projected, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
                    actions.append("removed rice entries from .claude/settings.json")
                else:
                    s_path.unlink(); actions.append("removed .claude/settings.json (held only rice entries)")
    c_path = repo_root / "CLAUDE.md"
    if c_path.exists():
        text = c_path.read_text(encoding="utf-8")
        projected = project_claude_md(text)
        if projected is None:
            c_path.unlink(); actions.append("removed CLAUDE.md (rice-created, held only the import line)")
        elif projected != text:
            c_path.write_text(projected, encoding="utf-8"); actions.append(f"removed `{IMPORT_LINE}` line from CLAUDE.md")
    a_path = repo_root / "AGENTS.md"
    if a_path.exists():
        text = a_path.read_text(encoding="utf-8")
        projected = project_agents_md(text)
        if projected is None:
            a_path.unlink(); actions.append("removed AGENTS.md (rice-created, held only the rice pointer block)")
        elif projected != text:
            a_path.write_text(projected, encoding="utf-8"); actions.append("removed the rice pointer block from AGENTS.md")
    if claude_dir.is_dir() and not any(claude_dir.iterdir()):
        claude_dir.rmdir(); actions.append("removed empty .claude/")
    return actions


# --------------------------------------------------------------------------
# the check -- the only thing allowed to say RICE-UNINSTALLED
# --------------------------------------------------------------------------

def _collect_items(repo_root: Path, expected: dict | None) -> list[Item]:
    items = []
    claude_dir = repo_root / ".claude"
    for name, label in (("rice.md", "rice-md:absent"), (_VERSION_NAME, "verified-version:absent"), (MARKER_NAME, "waiting-marker:absent")):
        items.append(Item(label, "FAIL" if (claude_dir / name).exists() else "PASS"))
    c_path = repo_root / "CLAUDE.md"
    has_import = c_path.exists() and any(l.strip() == IMPORT_LINE for l in c_path.read_text(encoding="utf-8").splitlines())
    items.append(Item("claude-md:import-absent", "FAIL" if has_import else "PASS"))
    a_path = repo_root / "AGENTS.md"
    has_block = a_path.exists() and reconcile._agents_block_span(a_path.read_text(encoding="utf-8")) is not None
    items.append(Item("agents-md:pointer-absent", "FAIL" if has_block else "PASS"))

    s_path = claude_dir / "settings.json"
    s = _read_json(s_path) if s_path.exists() else {}
    if s is None:
        items.append(Item("settings:readable", "FAIL", ".claude/settings.json is not a JSON object"))
        s = {}
    mp_present = isinstance(s.get("extraKnownMarketplaces"), dict) and _MP in s["extraKnownMarketplaces"]
    items.append(Item("settings:marketplace-absent", "FAIL" if mp_present else "PASS"))
    ep_present = isinstance(s.get("enabledPlugins"), dict) and _PLUGIN in s["enabledPlugins"]
    items.append(Item("settings:enabled-plugin-absent", "FAIL" if ep_present else "PASS"))
    env = s.get("env") if isinstance(s.get("env"), dict) else {}
    items.append(Item("settings:subagent-default-absent", "FAIL" if env.get("CLAUDE_CODE_SUBAGENT_MODEL") == _SONNET else "PASS",
                      "" if env.get("CLAUDE_CODE_SUBAGENT_MODEL") in (None, _SONNET) else f"project value {env['CLAUDE_CODE_SUBAGENT_MODEL']!r} kept"))
    items.append(Item("settings:archive-switch-absent", "FAIL" if "RICE_SESSION_ARCHIVE" in env else "PASS"))
    deny = s.get("permissions", {}).get("deny", []) if isinstance(s.get("permissions"), dict) else []
    left = [d for d in deny if d in RICE_DENY] if isinstance(deny, list) else []
    items.append(Item("settings:rice-deny-absent", "FAIL" if left else "PASS", "; ".join(left)[:300]))
    items.append(Item("settings:rice-output-style-absent", "FAIL" if s.get("outputStyle") == settings_template.RICE_STYLE else "PASS"))

    if expected is not None:
        exp_s = expected["settings"]
        if exp_s is None:
            items.append(Item("sentinel:settings.json", "PASS" if not s_path.exists() else "FAIL", "expected no settings.json"))
        elif not exp_s:
            items.append(Item("sentinel:settings.json", "PASS" if not s_path.exists() else "FAIL", "expected removal (held only rice entries)"))
        else:
            items.append(Item("sentinel:settings.json", "PASS" if s == exp_s else "FAIL", "project-owned settings differ from the pre-uninstall projection"))
        exp_c = expected["claude_md"]
        actual_c = c_path.read_text(encoding="utf-8") if c_path.exists() else None
        items.append(Item("sentinel:CLAUDE.md", "PASS" if actual_c == exp_c else "FAIL", "project-owned CLAUDE.md content differs"))
        exp_a = expected["agents_md"]
        actual_a = a_path.read_text(encoding="utf-8") if a_path.exists() else None
        items.append(Item("sentinel:AGENTS.md", "PASS" if actual_a == exp_a else "FAIL", "project-owned AGENTS.md content differs"))
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
