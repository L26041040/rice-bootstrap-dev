#!/usr/bin/env python3
"""rice's PreToolUse guard (RICE-SPEC-001 §3).

One plugin hook, two rules, no state:

- CLAUDE.md is routing only. Once a repo's bootstrap has fully completed --
  the verifier's version file (`verify.VERSION_FILE`, stamped only on a
  Gate B pass) exists -- any agent write to the
  repo's CLAUDE.md is denied with a reason that sends the content to an
  ordinary project file Claude Code does not load automatically. Before that
  (bootstrap, Matt's setup) the rule is inert, so it needs no exception.

The guard only ever denies, and only ever to the model: it never asks the
OWNER, never allows, never rewrites a tool's input, and never writes a file.
Outside a rice repo, on a payload it cannot read, or on any error of its own
it says nothing -- a rice bug must never stall a session.
"""
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402
from verify import VERSION_FILE  # noqa: E402  -- written only by the verifier, on Gate B

CLAUDE_MD = "CLAUDE.md"

CLAUDE_MD_REASON = (
    "rice: CLAUDE.md is routing only. Do not write status, progress, reports, decisions, "
    "hand-off or working memory here. Put the content in an ordinary project file that "
    "Claude Code does not load automatically -- choose the file from the content. Never: "
    "CLAUDE.md, any @import of it, .claude/rules files without `paths:`, auto-memory "
    "MEMORY.md, AGENTS.md. If the OWNER wants CLAUDE.md itself changed, the OWNER edits it "
    "directly."
)

_WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
_SHELL_SEPARATORS = {"|", "||", "&&", ";", "&"}


def repo_root(cwd: Path) -> Path | None:
    """The rice repo `cwd` is in: `cwd` itself or the nearest parent where rice
    is installed, never climbing past a git work-tree root."""
    for p in (cwd, *cwd.parents):
        if reconcile.rice_installed_here(p):
            return p
        if (p / ".git").exists():
            return None
    return None


def _resolve(path: str, cwd: Path) -> Path:
    p = Path(path).expanduser()
    return (p if p.is_absolute() else cwd / p).resolve()


def _bash_write_targets(command: str) -> list[str]:
    """Operands Bash would write to: after `>` / `>>`, and after `tee` (with
    or without an `rtk` prefix or `-a`)."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    targets, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if re.fullmatch(r"\d*>>?|&>>?", tok) and i + 1 < len(tokens):
            targets.append(tokens[i + 1]); i += 2; continue
        if tok == "tee":
            j = i + 1
            while j < len(tokens) and tokens[j] not in _SHELL_SEPARATORS:
                if not tokens[j].startswith("-"):
                    targets.append(tokens[j])
                j += 1
            i = j; continue
        i += 1
    return targets


def _claude_md_write(tool: str, tool_input: dict, cwd: Path, root: Path) -> bool:
    target = (root / CLAUDE_MD).resolve()
    if tool in _WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return bool(path) and _resolve(str(path), cwd) == target
    if tool == "Bash":
        return any(_resolve(t, cwd) == target for t in _bash_write_targets(str(tool_input.get("command") or "")))
    return False


def decide(payload: dict) -> str | None:
    """The deny reason for this tool call, or None to stay silent."""
    cwd_raw = payload.get("cwd")
    if not cwd_raw:
        return None
    cwd = Path(cwd_raw).resolve()
    root = repo_root(cwd)
    if root is None:
        return None
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    bootstrap_complete = (root / VERSION_FILE).exists()
    if bootstrap_complete and _claude_md_write(tool, tool_input, cwd, root):
        return CLAUDE_MD_REASON
    return None


def _payload() -> dict:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    try:
        reason = decide(_payload())
        if reason:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }}))
    except Exception as exc:  # noqa: BLE001 - never stall a session
        print(f"rice guard: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
