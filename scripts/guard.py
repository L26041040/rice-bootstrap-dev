#!/usr/bin/env python3
"""rice's PreToolUse guard (RICE-SPEC-001 §3).

One plugin hook, two rules, no state:

- CLAUDE.md is routing only. Once a repo's bootstrap has fully completed --
  the verifier's version file (`verify.VERSION_FILE`, stamped only on a
  Gate B pass) exists -- any agent write to the repo's CLAUDE.md is denied
  with a reason that sends the content to an ordinary project file Claude
  Code does not load automatically. Before that (bootstrap, Matt's setup) the
  rule is inert, so it needs no exception.
- session-history/ is cold. In every rice repo, a Read/Grep/Glob aimed at it,
  or a Bash command naming it, is denied -- unless the OWNER typed the History
  Skill's command (/rice:history) in the current /clear segment of this
  session's transcript. The next /clear closes it again. Searches that do not
  name the archive are not matched, by design.

The guard only ever denies, and only ever to the model: it never asks the
OWNER, never allows, never rewrites a tool's input, and never writes a file.
Outside a rice repo, on a payload it cannot read, or on any error of its own
it says nothing -- a rice bug must never stall a session. The two rules are
evaluated independently, so one failing never switches the other off.

It runs on every matching tool call in every repo the user opens, so it is
cheap first: a call that cannot concern either rule returns before any module
import or filesystem walk.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

CLAUDE_MD = "CLAUDE.md"
HISTORY_DIR = "session-history"

CLAUDE_MD_REASON = (
    "rice: CLAUDE.md is routing only. Do not write status, progress, reports, decisions, "
    "hand-off or working memory here. Put the content in an ordinary project file that "
    "Claude Code does not load automatically -- choose the file from the content. Never: "
    "CLAUDE.md, any @import of it, .claude/rules files without `paths:`, auto-memory "
    "MEMORY.md, AGENTS.md. If the OWNER wants CLAUDE.md itself changed, the OWNER edits it "
    "directly."
)

HISTORY_REASON = (
    "rice: session-history/ is cold historical evidence and is closed by default. If this "
    "task genuinely needs it, ask the OWNER to invoke /rice:history; do not work around this."
)

# The key is the OWNER typing the History Skill's command, recorded as the
# start of a user message. The skill sets `disable-model-invocation`, so any
# Skill call naming it can only be a (refused) model attempt -- never a key.
KEY_TAG = "<command-name>/rice:history</command-name>"
_COMMAND_STARTS = ("<command-message>", "<command-name>")

_WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
_SEARCH_TOOLS = ("Read", "Grep", "Glob")
_HISTORY_SEGMENT = re.compile(r"(?<![\w.-])session-history(?![\w-])")
_REDIRECT = re.compile(r"\d*(>>?|>\||>&)|&>>?")
_SEPARATORS = {"|", "||", "&&", ";", "&", "(", ")"}
_PREFIXES = {"rtk", "sudo", "command", "env", "nice"}


# --------------------------------------------------------------------------
# where things are
# --------------------------------------------------------------------------

def _resolve(path, cwd: Path) -> Path:
    p = Path(str(path)).expanduser()
    return (p if p.is_absolute() else cwd / p).resolve()


def _installed(p: Path) -> bool:
    if not ((p / ".claude" / "rice.md").exists() or (p / CLAUDE_MD).exists() or (p / "AGENTS.md").exists()):
        return False  # none of rice's markers: no need to load reconcile at all
    sys.path.insert(0, str(SCRIPTS))
    import reconcile
    try:
        return reconcile.rice_installed_here(p)
    except (OSError, UnicodeDecodeError):
        return False  # unreadable here; keep looking upward


def repo_root(start: Path) -> Path | None:
    """The rice repo containing `start`: itself or the nearest ancestor where
    rice is installed, never climbing past a git work-tree root."""
    for p in (start, *start.parents):
        if _installed(p):
            return p
        if (p / ".git").exists():
            return None
    return None


def _bootstrap_complete(root: Path) -> bool:
    sys.path.insert(0, str(SCRIPTS))
    from verify import VERSION_FILE  # the verifier's own constant; existence only
    return (root / VERSION_FILE).exists()


# --------------------------------------------------------------------------
# rule 1: CLAUDE.md is routing only (after bootstrap completion)
# --------------------------------------------------------------------------

def _simple_commands(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    out, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _bash_write_targets(command: str) -> list[str]:
    """Operands a Bash command would write: redirection targets (`>`, `>>`,
    `>|`, `>&`, `&>`), `tee` operands, in-place `sed -i` / `perl -i` operands,
    and the destination of `cp` / `mv` / `install`. `rtk`-prefixed forms
    count the same."""
    targets = []
    for words in _simple_commands(command):
        rest, i = [], 0
        while i < len(words):
            if _REDIRECT.fullmatch(words[i]) and i + 1 < len(words):
                targets.append(words[i + 1]); i += 2; continue
            rest.append(words[i]); i += 1
        while rest and (rest[0] in _PREFIXES or re.fullmatch(r"\w+=.*", rest[0])):
            rest = rest[1:]
        if not rest:
            continue
        cmd, args = os.path.basename(rest[0]), rest[1:]
        operands = [a for a in args if not a.startswith("-")]
        if cmd == "tee":
            targets += operands
        elif cmd == "sed" and any(a.startswith("-i") or a.startswith("--in-place") for a in args):
            targets += operands
        elif cmd == "perl" and any(re.fullmatch(r"-\w*i.*", a) for a in args):
            targets += operands
        elif cmd in ("cp", "mv", "install") and operands:
            targets.append(operands[-1])
    return targets


def _is_claude_md(path: Path, root: Path) -> bool:
    target = root / CLAUDE_MD
    if path == target.resolve():
        return True
    try:  # a hard link, or a case alias on a case-insensitive file system
        return path.exists() and target.exists() and os.path.samefile(path, target)
    except OSError:
        return False


def _claude_md_write(tool: str, tool_input: dict, cwd: Path) -> bool:
    if tool in _WRITE_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        paths = [_resolve(raw, cwd)] if raw else []
    elif tool == "Bash":
        command = str(tool_input.get("command") or "")
        if "claude.md" not in command.lower():
            return False
        paths = [_resolve(t, cwd) for t in _bash_write_targets(command)]
    else:
        return False
    for path in paths:
        root = repo_root(path.parent)
        if root is not None and _bootstrap_complete(root) and _is_claude_md(path, root):
            return True
    return False


# --------------------------------------------------------------------------
# rule 2: session-history is cold until the OWNER hands over the key
# --------------------------------------------------------------------------

def _archive_hit(path: Path, pattern_text: str = "") -> bool:
    """Does `path` (or a glob naming the archive, evaluated from it) fall
    inside a rice repo's session-history/?"""
    root = repo_root(path if path.is_dir() else path.parent)
    if root is None:
        return False
    archive = (root / HISTORY_DIR).resolve()
    return path == archive or archive in path.parents or bool(_HISTORY_SEGMENT.search(pattern_text))


def _history_target(tool: str, tool_input: dict, cwd: Path) -> bool:
    if tool == "Read":
        raw = tool_input.get("file_path")
        if not raw:
            return False
        path = _resolve(raw, cwd)
        return bool(_HISTORY_SEGMENT.search(str(path))) and _archive_hit(path)
    if tool in ("Grep", "Glob"):
        base = _resolve(tool_input.get("path") or cwd, cwd)
        keys = ("glob", "pattern") if tool == "Glob" else ("glob",)
        pattern = " ".join(str(tool_input.get(k) or "") for k in keys)
        if not (_HISTORY_SEGMENT.search(str(base)) or _HISTORY_SEGMENT.search(pattern)):
            return False
        return _archive_hit(base, pattern)
    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        if not _HISTORY_SEGMENT.search(command):
            return False
        if repo_root(cwd) is not None:
            return True  # the command names the archive, from inside a rice repo
        words = [w for c in _simple_commands(command) for w in c if _HISTORY_SEGMENT.search(w)]
        return any(_archive_hit(_resolve(w, cwd)) for w in words)
    return False


def _is_key(record: dict) -> bool:
    if record.get("type") != "user" or record.get("isCompactSummary") or record.get("isMeta"):
        return False
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    texts = [content] if isinstance(content, str) else [
        b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    return any(str(t).lstrip().startswith(_COMMAND_STARTS) and KEY_TAG in str(t) for t in texts)


def history_key_present(transcript_path) -> bool:
    """Did the OWNER type /rice:history since the last /clear? Segmenting is
    the archive hook's own (a genuine /clear command message, never a tool
    result quoting one). Unreadable means no key."""
    if not transcript_path:
        return False
    sys.path.insert(0, str(SCRIPTS))
    import archive_hook
    try:
        _, raw = archive_hook.load_transcript(Path(transcript_path))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    ends = archive_hook.clear_boundary_ends(raw)
    segment = raw[ends[-1] + 1:] if ends else raw
    return any(_is_key(r) for r in segment if isinstance(r, dict))


# --------------------------------------------------------------------------
# the hook
# --------------------------------------------------------------------------

def _rule(check, *args) -> bool:
    try:
        return bool(check(*args))
    except Exception as exc:  # noqa: BLE001 - one rule failing must not silence the other
        name = getattr(check, "__name__", "rule")
        print(f"rice guard: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def decide(payload: dict) -> str | None:
    """The deny reason for this tool call, or None to stay silent."""
    tool = str(payload.get("tool_name") or "")
    if tool not in _WRITE_TOOLS + _SEARCH_TOOLS + ("Bash",) or not payload.get("cwd"):
        return None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = Path(str(payload["cwd"])).resolve()
    if _rule(_claude_md_write, tool, tool_input, cwd):
        return CLAUDE_MD_REASON
    if _rule(_history_target, tool, tool_input, cwd) \
            and not _rule(history_key_present, payload.get("transcript_path")):
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
