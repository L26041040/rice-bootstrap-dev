#!/usr/bin/env python3
"""SessionEnd(reason=clear) hook: archive this conversation as readable Markdown.

Wired in .claude/settings.json as a standard Claude Code SessionEnd hook with
matcher "clear", so Claude Code itself decides when this runs -- a plain
/clear is the only thing that reaches it. This script does NOT parse the
event stream, hunt for the "current" session, or implement any clear
behaviour of its own.

Reads the official hook payload on stdin:

    {"session_id": "...", "transcript_path": "...", "cwd": "...",
     "permission_mode": "...", "hook_event_name": "SessionEnd",
     "reason": "clear"}

transcript_path is used directly -- Claude Code has already resolved it for
the session that is ending. Verified against the installed CLI
(@anthropic-ai/claude-code 2.1.42):

  * clearConversation() awaits executeSessionEndHooks("clear") BEFORE it
    clears messages and BEFORE it assigns a new conversation id, so the
    payload's session_id / transcript_path still name the conversation the
    OWNER is ending.
  * the payload is built as {...wj(undefined), hook_event_name:"SessionEnd",
    reason} where wj() = {session_id, transcript_path, cwd, permission_mode}.
  * hook matchers for SessionEnd are compared against the `reason` field, by
    exact string equality for a plain alphanumeric matcher like "clear".

Failure policy (SESSION-HISTORY-007 §5): report loudly on stderr and exit
non-zero. A non-zero SessionEnd hook only surfaces stderr to the user; it
does not block the clear. That is deliberate -- clearing does not delete
Claude Code's own JSONL transcript, so the source data survives a failed
archive and the segment can be recovered later.

---------------------------------------------------------------------------
Vendored code notice (MIT)

The transcript parsing below (SessionMeta, FlatMessage, load_transcript,
flatten) is copied VERBATIM from claude-code-export:

    https://github.com/PeriChu/claude-code-export
    File: claude_code_export.py
    License: MIT

    MIT License
    Copyright (c) 2026 claude-code-export contributors

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to
    the following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
    IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
    TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
    SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Everything OUTSIDE the block marked "BEGIN/END vendored" is original to this
repo: the dialogue projection (which flat messages count as real OWNER<->
Claude dialogue), /clear segmentation, rendering, and the git pipeline.
---------------------------------------------------------------------------
"""
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# Bumped when this file changes, so `install.py check` can tell an installed
# copy from the distribution copy. Not used by the archiving logic itself.
SESSION_ARCHIVE_VERSION = "1.1.0"

# SessionEnd hooks share a 1.5s budget by default; setting an explicit
# per-hook `timeout` raises that, capped at 60s -- that cap is the real
# ceiling regardless of what a hook declares (docs.claude.com/en/docs/
# claude-code/hooks, verbatim: "SessionEnd hooks share a 1.5-second budget;
# if your settings set a longer per-hook timeout, Claude Code raises the
# budget to match, up to 60 seconds"). install.py sets the hook's own
# timeout to 60 to match. The push below uses a smaller timeout so it fails
# loudly with time to spare instead of racing that outer 60s cutoff.
PUSH_TIMEOUT_SECONDS = 30

TZ_NAME = os.environ.get("SESSION_HISTORY_TZ", "Asia/Taipei")
CLEAR_MARKER = "<command-name>/clear</command-name>"
COMPACTION_PREFIX = "This session is being continued from a previous conversation"
INTERRUPT_MARKER = "[Request interrupted by user for tool use]"
INTERRUPT_MARKER2 = "[Request interrupted by user]"


# === BEGIN vendored from claude-code-export (MIT, verbatim) ================
@dataclass
class SessionMeta:
    task_id: str = ""
    title: str = ""
    cwd: str = ""
    decoded_cwd: str = ""
    git_branch: str = ""
    version: str = ""
    entrypoint: str = ""
    permission_mode: str = ""
    started_at: str = ""
    ended_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__}

@dataclass
class FlatMessage:
    index: int
    kind: str
    role: str
    timestamp: str
    uuid: str = ""
    parent_uuid: str = ""
    text: str = ""
    tool_name: str = ""
    tool_id: str = ""
    tool_input: Any = None
    is_error: bool = False
    attachment_type: str = ""
    attachment_payload: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__}

def load_transcript(jsonl_path: Path) -> tuple[SessionMeta, list[dict[str, Any]]]:
    """Read a Claude Code transcript jsonl. The schema is uniform across
    platforms: each line is a JSON object with one of the types we handle
    in flatten()."""
    meta = SessionMeta()
    raw: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw.append(obj)
            if not meta.task_id and obj.get("sessionId"):
                meta.task_id = obj["sessionId"]
            # First non-empty cwd wins. A resumed session can appear with
            # multiple cwds in the same transcript (started in repo A, later
            # continued from repo B). We want the original project so the
            # CLAUDE.md / asset paths line up with where the work actually
            # happened.
            if obj.get("cwd") and not meta.cwd:
                meta.cwd = obj["cwd"]
            if obj.get("gitBranch") and not meta.git_branch:
                meta.git_branch = obj["gitBranch"]
            if obj.get("version") and not meta.version:
                meta.version = obj["version"]
            if obj.get("entrypoint") and not meta.entrypoint:
                meta.entrypoint = obj["entrypoint"]
            if obj.get("permissionMode") and not meta.permission_mode:
                meta.permission_mode = obj["permissionMode"]
            ts = obj.get("timestamp")
            if ts:
                if not meta.started_at:
                    meta.started_at = ts
                meta.ended_at = ts
            if obj.get("type") == "ai-title" and obj.get("aiTitle"):
                meta.title = obj["aiTitle"]
    return meta, raw

def flatten(raw: list[dict[str, Any]]) -> list[FlatMessage]:
    flat: list[FlatMessage] = []
    seen_uuid_kind: set[tuple[str, str]] = set()
    last_text_signature: tuple[str, str, str] | None = None

    def push(**kwargs):
        u = kwargs.get("uuid") or ""
        k = kwargs.get("kind") or ""
        if u and (u, k) in seen_uuid_kind:
            return
        nonlocal last_text_signature
        if k == "text":
            sig = (kwargs.get("role", ""), k, kwargs.get("text", "") or "")
            if sig[2] and sig == last_text_signature:
                return
            last_text_signature = sig
        else:
            last_text_signature = None
        if u:
            seen_uuid_kind.add((u, k))
        flat.append(FlatMessage(index=len(flat), **kwargs))

    for obj in raw:
        t = obj.get("type")
        if t in ("queue-operation", "ai-title", "last-prompt"):
            continue
        ts = obj.get("timestamp", "") or ""
        uuid = obj.get("uuid", "") or ""
        parent = obj.get("parentUuid", "") or ""
        if t == "attachment":
            att = obj.get("attachment", {}) or {}
            push(kind="attachment", role="system", timestamp=ts, uuid=uuid, parent_uuid=parent,
                 attachment_type=att.get("type", ""), attachment_payload=att)
            continue
        msg = obj.get("message") or {}
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            push(kind="text", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent, text=content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                push(kind="text", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent, text=block.get("text", ""))
            elif bt == "thinking":
                push(kind="thinking", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent, text=block.get("thinking", ""))
            elif bt == "tool_use":
                push(kind="tool_use", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     tool_name=block.get("name", ""), tool_id=block.get("id", ""),
                     tool_input=block.get("input"))
            elif bt == "tool_result":
                inner = block.get("content")
                text = ""
                if isinstance(inner, str):
                    text = inner
                elif isinstance(inner, list):
                    parts: list[str] = []
                    for x in inner:
                        if isinstance(x, dict):
                            if x.get("type") == "text":
                                parts.append(x.get("text", ""))
                            elif x.get("type") == "image":
                                parts.append("[image]")
                    text = "\n".join(parts)
                tur = obj.get("toolUseResult") or {}
                tur_meta = (
                    {k: v for k, v in tur.items() if k not in ("stdout", "stderr")}
                    if isinstance(tur, dict)
                    else {}
                )
                stderr = tur.get("stderr") if isinstance(tur, dict) else None
                extra = {}
                if stderr:
                    extra["stderr"] = stderr
                if tur_meta:
                    extra["result_meta"] = tur_meta
                push(kind="tool_result", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     text=text, tool_id=block.get("tool_use_id", ""),
                     is_error=bool(block.get("is_error")), extra=extra)
            elif bt == "image":
                push(kind="image", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     extra={"source": block.get("source")})
            else:
                push(kind=bt or "unknown", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     extra={"raw": block})
    return flat
# === END vendored from claude-code-export ================================


# === Original code for this repo (not from claude-code-export) =============

def _get_tz():
    try:
        return ZoneInfo(TZ_NAME) if ZoneInfo else timezone.utc
    except Exception:
        print(f"warning: unknown SESSION_HISTORY_TZ={TZ_NAME!r}; using UTC", file=sys.stderr)
        return timezone.utc

def _local(ts: str, tz, fmt: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime(fmt)

def _between(s: str, open_tag: str, close_tag: str) -> str | None:
    i = s.find(open_tag)
    if i < 0:
        return None
    j = s.find(close_tag, i + len(open_tag))
    if j < 0:
        return None
    return s[i + len(open_tag):j]

def reconstruct_command(s: str) -> str | None:
    """Rebuild the slash-command line the OWNER actually typed from the
    <command-name>/<command-args> wrapper. Verbatim args, no paraphrasing."""
    name = _between(s, "<command-name>", "</command-name>")
    if name is None:
        return None
    args = (_between(s, "<command-args>", "</command-args>") or "").strip("\n")
    name = name.strip()
    if not args.strip():
        return name
    if "\n" in args:
        return f"{name}\n{args}"
    return f"{name} {args}"

def owner_text(s: str) -> str | None:
    """The OWNER's real words in a user-message string, or None to drop it.
    These rules were established against this repo's real transcripts
    (SESSION-HISTORY-002/003) -- every excluded shape was observed in real
    data, not assumed."""
    st = s.strip()
    if not st:
        return None
    if st in (INTERRUPT_MARKER, INTERRUPT_MARKER2):
        return None
    for prefix in ("<local-command-caveat>", "<local-command-stdout>",
                   "<task-notification>", "<system-reminder>"):
        if st.startswith(prefix):
            return None
    if st.startswith(COMPACTION_PREFIX):
        return None
    if "<command-name>" in st:
        return reconstruct_command(s)
    return s

def project_dialogue(raw_segment: list[dict]) -> list[tuple[str, str, str]]:
    """(role, timestamp, text) for the real OWNER<->Claude dialogue only.

    Traversal/flattening is claude-code-export's (verbatim above). This
    projection then keeps only kind=="text" messages, drops sidechain
    records, and applies the user-side whitelist from owner_text()."""
    raw_main = [o for o in raw_segment if not o.get("isSidechain")]
    by_uuid = {o["uuid"]: o for o in raw_main if o.get("uuid")}
    turns: list[tuple[str, str, str]] = []
    for m in flatten(raw_main):
        if m.kind != "text":
            continue
        rec = by_uuid.get(m.uuid, {})
        if m.role == "assistant":
            if m.text.strip():
                turns.append(("Claude", m.timestamp, m.text))
        elif m.role == "user":
            if rec.get("isMeta"):
                continue
            kept = owner_text(m.text)
            if kept is not None:
                turns.append(("OWNER", m.timestamp, kept))
    return turns

def render(turns, session_id: str, tz) -> tuple[str, str]:
    """Returns (markdown, end_timestamp_iso). Chat-style: bold role labels,
    consecutive same-role messages merged under one label."""
    end_ts = turns[-1][1] if turns else datetime.now(timezone.utc).isoformat()
    h1_time = _local(end_ts, tz, "%Y-%m-%d %H:%M")
    out = [f"# {h1_time} — Claude Session", ""]
    out.append(f"<!-- session: {session_id} | tz: {TZ_NAME} | last-event: {turns[-1][1] if turns else ''} -->")
    out.append("")
    prev_role = None
    for role, ts, text in turns:
        if role != prev_role:
            stamp = _local(ts, tz, "%H:%M")
            out.append(f"**{role} — {stamp}**")
            out.append("")
        out.append(text.rstrip())
        out.append("")
        prev_role = role
    return "\n".join(out), end_ts

def _text(x) -> str:
    """Normalize a TimeoutExpired.stdout/stderr value to str. Despite
    text=True, Python can still hand back bytes here -- the partial output
    is captured from the pipe before the timeout fires, and decoding isn't
    guaranteed to have happened yet on that path. None (nothing captured
    yet) becomes ""."""
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return x

def run(cmd, **kw):
    """subprocess.run, except a timeout is reported the same way any other
    git failure is -- a non-zero CompletedProcess -- instead of raising
    TimeoutExpired past every call site's `.returncode` check."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except subprocess.TimeoutExpired as exc:
        timeout = kw.get("timeout")
        stdout, stderr = _text(exc.stdout), _text(exc.stderr)
        return subprocess.CompletedProcess(
            cmd, 1, stdout=stdout,
            stderr=stderr + f"\ntimed out after {timeout}s")

def _is_committed(repo_root: Path, rel: Path) -> bool:
    """True if `rel` is tracked and the working tree matches HEAD for it --
    i.e. a prior /xlear attempt already committed this exact content, so the
    add/commit step can be skipped on retry."""
    tracked = run(["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", str(rel)]).returncode == 0
    if not tracked:
        return False
    return run(["git", "-C", str(repo_root), "diff", "--quiet", "HEAD", "--", str(rel)]).returncode == 0

def _current_branch(repo_root: Path) -> str:
    """The branch this project is on.

    The only behavioural generalization from the canonical K_yt_proj version,
    which hard-coded `master` because that is that repo's branch policy. An
    installer that targets arbitrary projects has to push where the project
    actually lives. Returns "" on a detached HEAD, which the caller treats as
    "commit locally, cannot verify remotely".
    """
    r = run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode != 0:
        return ""
    branch = r.stdout.strip()
    return "" if branch in ("", "HEAD") else branch


def clear_boundary_ends(raw: list[dict]) -> list[int]:
    """Index of the last record of each /clear boundary block.

    Boundary shape, verified against this repo's real transcripts (six /clear
    firings, all identical): a user record carrying the /clear command,
    optionally followed by its system/local_command acknowledgement whose
    parentUuid points back at it. The parent pointer is checked explicitly --
    never position alone.
    """
    ends: list[int] = []
    for i, o in enumerate(raw):
        if o.get("type") != "user":
            continue
        c = (o.get("message") or {}).get("content")
        if not (isinstance(c, str) and CLEAR_MARKER in c):
            continue
        end = i
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        if (
            isinstance(nxt, dict)
            and nxt.get("type") == "system"
            and nxt.get("subtype") == "local_command"
            and nxt.get("parentUuid") == o.get("uuid")
        ):
            end = i + 1
        ends.append(end)
    return ends


def segment_for_this_clear(raw: list[dict]) -> list[dict]:
    """The records belonging to the segment this /clear is ending.

    The subtlety: at SessionEnd(reason=clear) the /clear the OWNER just typed
    may itself already be in the transcript. If it is, it must NOT be treated
    as the segment's starting boundary -- it is the segment's END, and taking
    everything after it would archive nothing.

    Rather than guessing whether that record has been flushed yet (it depends
    on write timing we do not control), walk the boundaries from newest to
    oldest and take the first one that still leaves real dialogue behind it.
    That is self-correcting for both orderings: if the newest boundary is the
    current /clear it yields no dialogue and we fall back to the previous one,
    which correctly leaves the current /clear inside the segment as the
    OWNER's own last input.
    """
    for end in reversed(clear_boundary_ends(raw)):
        candidate = raw[end + 1:]
        if project_dialogue(candidate):
            return candidate
    return raw


def read_payload() -> dict:
    """The official SessionEnd hook payload, from stdin."""
    data = sys.stdin.read()
    if not data.strip():
        raise SystemExit("FAIL: empty hook payload on stdin")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FAIL: hook payload is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise SystemExit("FAIL: hook payload is not a JSON object")
    return payload


def main() -> int:
    payload = read_payload()
    session_id = payload.get("session_id") or ""
    transcript_path = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or ""
    reason = payload.get("reason") or ""

    # settings.json already scopes this hook with matcher "clear"; this is
    # defence in depth so the script is still correct if invoked directly.
    if reason != "clear":
        print(f"reason={reason!r} is not 'clear'; nothing to archive", file=sys.stderr)
        return 0

    if not transcript_path:
        print("FAIL: hook payload has no transcript_path", file=sys.stderr)
        return 1
    transcript = Path(transcript_path)
    if not transcript.is_file():
        print(f"FAIL: transcript_path does not exist: {transcript}", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[2]
    archive_dir = repo_root / "session-history"
    tz = _get_tz()
    print(f"archiving session {session_id} (cwd={cwd}) from {transcript}", file=sys.stderr)

    meta, raw = load_transcript(transcript)
    segment = segment_for_this_clear(raw)
    turns = project_dialogue(segment)
    if not turns:
        print("FAIL: no OWNER/Claude dialogue found in this segment; nothing to archive",
              file=sys.stderr)
        return 1

    md, end_ts = render(turns, meta.task_id or session_id, tz)

    # Deterministic identity (segment end timestamp + session id) so a repeat
    # invocation resumes instead of duplicating.
    short_id = (meta.task_id or session_id or "unknown")[:8]
    stamp = _local(end_ts, tz, "%Y-%m-%d_%H%M%S%z")
    base = f"{stamp}_{short_id}"
    archive_dir.mkdir(exist_ok=True)
    dest = archive_dir / f"{base}.md"
    rel = dest.relative_to(repo_root)

    if dest.exists():
        if dest.read_text(encoding="utf-8") != md:
            print(f"FAIL: {dest} already exists with DIFFERENT content -- refusing to "
                  "overwrite", file=sys.stderr)
            return 1
        print(f"already written: {rel} (identical content; resuming)", file=sys.stderr)
    else:
        dest.write_text(md, encoding="utf-8")
        print(f"wrote: {rel} ({dest.stat().st_size} bytes, {len(turns)} turns)", file=sys.stderr)

    if not _is_committed(repo_root, rel):
        if run(["git", "-C", str(repo_root), "add", "--", str(rel)]).returncode != 0:
            print("FAIL: git add failed", file=sys.stderr)
            return 1
        r = run(["git", "-C", str(repo_root), "commit", "-m", f"session history: {base}",
                 "--", str(rel)])
        if r.returncode != 0:
            print(f"FAIL: git commit failed:\n{r.stdout}{r.stderr}", file=sys.stderr)
            return 1
        committed = run(["git", "-C", str(repo_root), "show", "--name-only", "--format=",
                         "HEAD"]).stdout.split()
        if committed != [str(rel)]:
            print(f"FAIL: commit contains unexpected files: {committed}", file=sys.stderr)
            return 1
        print(f"committed: {rel}", file=sys.stderr)
    else:
        print(f"already committed: {rel} (resuming)", file=sys.stderr)

    branch = _current_branch(repo_root)
    if not branch:
        print(f"ARCHIVED {rel} (committed locally; detached HEAD, so no push)",
              file=sys.stderr)
        return 0

    # A plain (non-force) push is its own correctness check: git rejects it
    # unless our HEAD is a fast-forward of origin/<branch>, and "everything
    # up-to-date" (exit 0) on a retry after an already-successful push is
    # exactly the idempotent-resume behaviour a separate pre/post fetch was
    # standing in for. One network call instead of three.
    r = run(["git", "-C", str(repo_root), "push", "origin", f"HEAD:{branch}"],
            timeout=PUSH_TIMEOUT_SECONDS)
    if r.returncode != 0:
        print(f"FAIL: git push failed:\n{r.stdout}{r.stderr}", file=sys.stderr)
        return 1

    print(f"ARCHIVED {rel} (pushed to origin/{branch})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
