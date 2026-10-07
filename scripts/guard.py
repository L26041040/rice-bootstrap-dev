#!/usr/bin/env python3
"""rice's PreToolUse guard (RICE-SPEC-001 §3).

One plugin hook, two rules, no state:

- CLAUDE.md is routing only. Once a repo's bootstrap has fully completed --
  the verifier's version file (`verify.VERSION_FILE`, stamped only on a
  Gate B pass) exists -- any agent write to the
  repo's CLAUDE.md is denied with a reason that sends the content to an
  ordinary project file Claude Code does not load automatically. Before that
  (bootstrap, Matt's setup) the rule is inert, so it needs no exception.
- session-history/ is cold. In every rice repo, a Read/Grep/Glob aimed at it,
  or a Bash command naming it, is denied -- unless the OWNER invoked the
  History Skill (/rice:history) in the current /clear segment of this
  session's transcript. The next /clear closes it again. Searches that do not
  name the archive are not matched, by design.

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

HISTORY_DIR = "session-history"

HISTORY_REASON = (
    "rice: session-history/ is cold historical evidence and is closed by default. If this "
    "task genuinely needs it, ask the OWNER to invoke /rice:history; do not work around this."
)

# The key: the OWNER typing the History Skill's command, or -- should the
# loader ever record it that way -- a Skill call naming it. The skill sets
# `disable-model-invocation`, so the model itself cannot turn this key.
_KEY_COMMANDS = ("/rice:history", "/history")
_KEY_SKILLS = ("rice:history", "history")
_HISTORY_SEGMENT = re.compile(r"(?<![\w.-])session-history(?![\w-])")

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


def _history_target(tool: str, tool_input: dict, cwd: Path, root: Path) -> bool:
    archive = (root / HISTORY_DIR).resolve()

    def under(path) -> bool:
        resolved = _resolve(str(path), cwd)
        return resolved == archive or archive in resolved.parents

    if tool == "Read":
        return bool(tool_input.get("file_path")) and under(tool_input["file_path"])
    if tool in ("Grep", "Glob"):
        if tool_input.get("path") and under(tool_input["path"]):
            return True
        globs = (tool_input.get("glob"), tool_input.get("pattern") if tool == "Glob" else None)
        return any(isinstance(g, str) and _HISTORY_SEGMENT.search(g) for g in globs)
    if tool == "Bash":
        return bool(_HISTORY_SEGMENT.search(str(tool_input.get("command") or "")))
    return False


def _is_key(record: dict) -> bool:
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    if record.get("type") == "user":
        texts = [content] if isinstance(content, str) else [
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        return any(f"<command-name>{c}</command-name>" in str(x) for x in texts for c in _KEY_COMMANDS)
    if record.get("type") == "assistant" and isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill"
                   and (b.get("input") or {}).get("skill") in _KEY_SKILLS for b in content)
    return False


def history_key_present(transcript_path) -> bool:
    """Did the OWNER hand over the History Skill key since the last /clear?
    Segmenting is the archive hook's own (a genuine /clear command message,
    never a tool result quoting one). Unreadable means no key."""
    if not transcript_path:
        return False
    import archive_hook  # only calls aimed at the archive pay for it
    try:
        _, raw = archive_hook.load_transcript(Path(transcript_path))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    ends = archive_hook.clear_boundary_ends(raw)
    segment = raw[ends[-1] + 1:] if ends else raw
    return any(_is_key(r) for r in segment if isinstance(r, dict))


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
    if _history_target(tool, tool_input, cwd, root) and not history_key_present(payload.get("transcript_path")):
        return HISTORY_REASON
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
