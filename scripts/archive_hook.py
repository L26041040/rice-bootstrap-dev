#!/usr/bin/env python3
"""Ticket #22 (RICE-BOOTSTRAP-032 split): archive an ended session as
readable, redacted Markdown, with a mechanical INDEX row -- and separately,
safely sync any pending archive commits to origin.

Two entrypoints, two hook events, wired in the plugin's hooks.json so
Claude Code itself decides when each runs:

- `main()` -- SessionEnd(clear): parse/redact/archive, write INDEX, local
  git commit. Never touches the network.
- `sync_main()` (invoked with `--sync`) -- SessionStart(clear): push any
  commits this repo is ahead of its upstream by, but ONLY when every one
  of them is mechanically provable to be a Rice archive commit (see
  `RICE_ARCHIVE_TRAILER`) touching nothing but `session-history/**`. Rice
  must never use archive sync as a reason to publish unrelated
  OWNER/project work that happens to be sitting ahead of the same
  upstream -- see `_range_is_safe_to_push`. Git itself is the only pending
  -state store: no queue, database, or state machine.

Failure policy (#13 "Session archive + INDEX", #22 scope, extended by
RICE-BOOTSTRAP-032 to the sync phase): report clearly to stderr and return
without blocking. Claude Code offers no mechanism to make a SessionEnd or
SessionStart hook block the session -- a non-zero exit only surfaces
stderr. There is no retry-as-protocol, no escape hatch, no pending/recovery
state beyond git's own ahead/behind: on a commit failure, any files this
run wrote are rolled back so the working tree is left exactly as it was
found (a plain re-run of the hook is not a "retry protocol", it's just
running the hook again); when sync finds the ahead range unsafe or a push
fails, the local commit(s) simply stand as-is until the next SessionStart.

---------------------------------------------------------------------------
Vendored code notice (MIT)

The transcript parsing below (SessionMeta, FlatMessage, load_transcript,
flatten) and the /clear segmentation helpers are adapted from this repo's
own provisional .claude/hooks/archive_session.py, which itself vendors
parsing code verbatim from claude-code-export:

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

Everything outside the block marked "BEGIN/END vendored" is original to
this ticket: redaction, truncation, the INDEX, and the non-blocking,
rollback-on-commit-failure git pipeline.
---------------------------------------------------------------------------
"""
import json
import os
import re
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

# SessionEnd/SessionStart hooks share a 1.5s default budget; the plugin's
# hooks.json sets each hook's own `timeout` to 60 (the documented cap) to
# match. sync_main()'s push uses a smaller timeout so it fails loudly with
# time to spare instead of racing that outer cutoff.
PUSH_TIMEOUT_SECONDS = 30

# Mechanical identity for a Rice-authored archive commit (RICE-BOOTSTRAP-032):
# a trailer line, not a heuristic on the commit message subject, so sync can
# tell a Rice archive commit apart from any OWNER/project commit with
# certainty before ever considering a push.
RICE_ARCHIVE_TRAILER = "Rice-Archive: true"

TZ_NAME = os.environ.get("SESSION_HISTORY_TZ", "Asia/Taipei")
CLEAR_MARKER = "<command-name>/clear</command-name>"
COMPACTION_PREFIX = "This session is being continued from a previous conversation"
INTERRUPT_MARKER = "[Request interrupted by user for tool use]"
INTERRUPT_MARKER2 = "[Request interrupted by user]"

MAX_MESSAGE_CHARS = 4000
EXCERPT_MAX_CHARS = 80
INDEX_FILENAME = "INDEX.md"


# === BEGIN vendored (adapted from claude-code-export, MIT) =================
@dataclass
class SessionMeta:
    task_id: str = ""
    title: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    started_at: str = ""
    ended_at: str = ""


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
    extra: dict = field(default_factory=dict)


def load_transcript(jsonl_path: Path) -> tuple[SessionMeta, list[dict[str, Any]]]:
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
            if obj.get("cwd") and not meta.cwd:
                meta.cwd = obj["cwd"]
            if obj.get("gitBranch") and not meta.git_branch:
                meta.git_branch = obj["gitBranch"]
            if obj.get("version") and not meta.version:
                meta.version = obj["version"]
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

    def push(**kwargs):
        u = kwargs.get("uuid") or ""
        k = kwargs.get("kind") or ""
        if u and (u, k) in seen_uuid_kind:
            return
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
                push(kind="tool_result", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent,
                     tool_id=block.get("tool_use_id", ""), is_error=bool(block.get("is_error")))
            else:
                push(kind=bt or "unknown", role=role, timestamp=ts, uuid=uuid, parent_uuid=parent)
    return flat
# === END vendored ===========================================================


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
    """(role, timestamp, text) for real OWNER<->Claude dialogue only --
    tool calls, tool results, and thinking are never kind=="text", so they
    are excluded by construction, not by a special case here."""
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


def clear_boundary_ends(raw: list[dict]) -> list[int]:
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
    for end in reversed(clear_boundary_ends(raw)):
        candidate = raw[end + 1:]
        if project_dialogue(candidate):
            return candidate
    return raw


# --- redaction / truncation (new for #22) -----------------------------------

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9\-_.=]{8,}")
_GITHUB_TOKEN = re.compile(r"gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255}")
_OPENAI_KEY = re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")
_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
_SLACK_TOKEN = re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

# Order matters: Bearer runs first so "Bearer <anything>" is redacted as one
# unit rather than leaving a nested [REDACTED:...] behind Bearer once the
# more specific token patterns below have already run.
_PATTERNS = [
    ("private-key", _PRIVATE_KEY_BLOCK),
    ("bearer-token", _BEARER),
    ("github-token", _GITHUB_TOKEN),
    ("openai-key", _OPENAI_KEY),
    ("aws-key", _AWS_KEY),
    ("slack-token", _SLACK_TOKEN),
    ("jwt", _JWT),
]


def redact(text: str) -> str:
    for name, pattern in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


def truncate_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[TRUNCATED: {len(text) - limit} more characters]"


def first_line_excerpt(text: str) -> str:
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if len(first) > EXCERPT_MAX_CHARS:
        return first[: EXCERPT_MAX_CHARS - 1] + "…"
    return first


def render(turns, session_id: str, tz) -> tuple[str, str]:
    """Redaction and truncation are applied here, on the full original text,
    before anything is cut for length -- truncating first could slice a
    credential in half and leak the visible remainder."""
    end_ts = turns[-1][1] if turns else datetime.now(timezone.utc).isoformat()
    h1_time = _local(end_ts, tz, "%Y-%m-%d %H:%M")
    out = [f"# {h1_time} — Claude Session", ""]
    out.append(f"<!-- session: {session_id} | tz: {TZ_NAME} | last-event: {turns[-1][1] if turns else ''} -->")
    out.append("")
    prev_role = None
    for role, ts, text in turns:
        clean = truncate_message(redact(text))
        if role != prev_role:
            stamp = _local(ts, tz, "%H:%M")
            out.append(f"**{role} — {stamp}**")
            out.append("")
        out.append(clean.rstrip())
        out.append("")
        prev_role = role
    return "\n".join(out), end_ts


# --- INDEX ------------------------------------------------------------------

_INDEX_HEADER = (
    "# Session Archive Index\n"
    "\n"
    "| Date | Title | Excerpt | Turns | File |\n"
    "|---|---|---|---|---|\n"
)


def render_index_row(date: str, title: str, excerpt: str, turn_count: int, filename: str) -> str:
    def esc(s: str) -> str:
        return s.replace("|", "\\|")
    return f"| {date} | {esc(title)} | {esc(excerpt)} | {turn_count} | {filename} |\n"


def index_has_row_for(index_path: Path, filename: str) -> bool:
    if not index_path.exists():
        return False
    return f"| {filename} |" in index_path.read_text(encoding="utf-8")


def append_index_row(index_path: Path, row: str, filename: str) -> bool:
    """True if appended; False if a row for this filename was already there."""
    if index_has_row_for(index_path, filename):
        return False
    if not index_path.exists():
        index_path.write_text(_INDEX_HEADER, encoding="utf-8")
    with index_path.open("a", encoding="utf-8") as f:
        f.write(row)
    return True


# --- git plumbing -------------------------------------------------------------

def _text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return x


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except subprocess.TimeoutExpired as exc:
        timeout = kw.get("timeout")
        stdout, stderr = _text(exc.stdout), _text(exc.stderr)
        return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr=stderr + f"\ntimed out after {timeout}s")


def _is_committed(repo_root: Path, rel: Path) -> bool:
    tracked = run(["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", str(rel)]).returncode == 0
    if not tracked:
        return False
    return run(["git", "-C", str(repo_root), "diff", "--quiet", "HEAD", "--", str(rel)]).returncode == 0


def _current_branch(repo_root: Path) -> str:
    r = run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode != 0:
        return ""
    branch = r.stdout.strip()
    return "" if branch in ("", "HEAD") else branch


def _upstream_ref(repo_root: Path, branch: str) -> str | None:
    r = run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
    if r.returncode != 0:
        return None
    ref = r.stdout.strip()
    return ref or None


def _is_rice_archive_commit(repo_root: Path, sha: str) -> bool:
    r = run(["git", "-C", str(repo_root), "log", "-1", "--format=%B", sha])
    if r.returncode != 0:
        return False
    return any(line.strip() == RICE_ARCHIVE_TRAILER for line in r.stdout.splitlines())


def _commit_touches_only_session_history(repo_root: Path, sha: str) -> bool:
    r = run(["git", "-C", str(repo_root), "diff-tree", "--no-commit-id", "--name-only", "-r", sha])
    if r.returncode != 0:
        return False
    paths = [p for p in r.stdout.splitlines() if p.strip()]
    if not paths:
        return False  # an empty diff proves nothing; fail closed
    return all(p.startswith("session-history/") for p in paths)


def _range_is_safe_to_push(repo_root: Path, upstream: str) -> tuple[bool, str]:
    """Fail-closed range check (RICE-BOOTSTRAP-032). Rice must never use
    archive sync as a reason to publish unrelated OWNER/project work that
    happens to be sitting ahead of the same upstream: every commit in
    `upstream..HEAD` must be mechanically provable to be a Rice archive
    commit touching only session-history/**, or nothing gets pushed."""
    if run(["git", "-C", str(repo_root), "merge-base", "--is-ancestor", upstream, "HEAD"]).returncode != 0:
        return False, f"upstream {upstream} is not an ancestor of HEAD"
    r = run(["git", "-C", str(repo_root), "rev-list", f"{upstream}..HEAD"])
    shas = [s for s in r.stdout.split() if s]
    if not shas:
        return True, "nothing to sync"
    for sha in shas:
        if not _is_rice_archive_commit(repo_root, sha):
            return False, f"commit {sha[:8]} ahead of {upstream} is not a Rice archive commit"
        if not _commit_touches_only_session_history(repo_root, sha):
            return False, f"commit {sha[:8]} touches a path outside session-history/"
    return True, f"{len(shas)} Rice archive commit(s) safe to push"


def sync_main() -> int:
    """SessionStart(clear) hook: push pending Rice archive commits when (and
    only when) the entire ahead range is safe -- see `_range_is_safe_to_push`.
    Never blocks session start: always returns 0."""
    payload = read_payload()
    cwd = payload.get("cwd") or ""
    if not cwd:
        print("rice: hook payload has no cwd; nothing to sync", file=sys.stderr)
        return 0
    repo_root = Path(cwd)
    branch = _current_branch(repo_root)
    if not branch:
        print("rice: detached HEAD; nothing to sync", file=sys.stderr)
        return 0
    upstream = _upstream_ref(repo_root, branch)
    if not upstream:
        print(f"rice: {branch} has no upstream configured; nothing to sync", file=sys.stderr)
        return 0
    safe, detail = _range_is_safe_to_push(repo_root, upstream)
    if not safe:
        print(f"rice: archive sync pending (not auto-pushed): {detail}", file=sys.stderr)
        return 0
    if detail == "nothing to sync":
        print("rice: archive already in sync with upstream", file=sys.stderr)
        return 0
    r = run(["git", "-C", str(repo_root), "push", "origin", f"HEAD:{branch}"], timeout=PUSH_TIMEOUT_SECONDS)
    if r.returncode != 0:
        print(f"rice: archive sync push failed, local commit(s) remain ({detail}): {r.stdout}{r.stderr}",
              file=sys.stderr)
        return 0
    print(f"rice: archive synced to origin/{branch} ({detail})", file=sys.stderr)
    return 0


def read_payload() -> dict:
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
    reason = payload.get("reason") or ""
    if reason != "clear":
        print(f"reason={reason!r} is not 'clear'; nothing to archive", file=sys.stderr)
        return 0

    if os.environ.get("RICE_SESSION_ARCHIVE", "0") != "1":
        print("rice: session archive disabled (RICE_SESSION_ARCHIVE != '1'); skipping", file=sys.stderr)
        return 0

    session_id = payload.get("session_id") or ""
    transcript_path = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or ""
    if not transcript_path:
        print("FAIL: hook payload has no transcript_path", file=sys.stderr)
        return 1
    transcript = Path(transcript_path)
    if not transcript.is_file():
        print(f"FAIL: transcript_path does not exist: {transcript}", file=sys.stderr)
        return 1
    if not cwd:
        print("FAIL: hook payload has no cwd", file=sys.stderr)
        return 1
    # NOT the plugin's own __file__ location -- this script runs from the
    # installed plugin dir, which is never the target repo. `cwd` from the
    # hook payload is Claude Code's own resolution of the repo being worked
    # in, and is the only correct source for repo_root here.
    repo_root = Path(cwd)

    tz = _get_tz()
    meta, raw = load_transcript(transcript)
    segment = segment_for_this_clear(raw)
    turns = project_dialogue(segment)
    if not turns:
        print("FAIL: no OWNER/Claude dialogue found in this segment; nothing to archive", file=sys.stderr)
        return 1

    md, end_ts = render(turns, meta.task_id or session_id, tz)

    short_id = (meta.task_id or session_id or "unknown")[:8]
    stamp = _local(end_ts, tz, "%Y-%m-%d_%H%M%S%z")
    base = f"{stamp}_{short_id}"
    filename = f"{base}.md"
    archive_dir = repo_root / "session-history"
    dest = archive_dir / filename
    index_path = archive_dir / INDEX_FILENAME
    rel_dest = dest.relative_to(repo_root)
    rel_index = index_path.relative_to(repo_root)

    title = meta.title or short_id
    excerpt = first_line_excerpt(redact(turns[0][2]))
    date_str = _local(end_ts, tz, "%Y-%m-%d")
    row = render_index_row(date_str, title, excerpt, len(turns), filename)

    archive_dir_existed = archive_dir.exists()
    archive_dir.mkdir(exist_ok=True)

    dest_existed = dest.exists()
    if dest_existed and dest.read_text(encoding="utf-8") != md:
        print(f"FAIL: {rel_dest} already exists with DIFFERENT content -- refusing to overwrite",
              file=sys.stderr)
        return 1
    wrote_dest = False
    if not dest_existed:
        dest.write_text(md, encoding="utf-8")
        wrote_dest = True

    index_existed = index_path.exists()
    index_prior = index_path.read_text(encoding="utf-8") if index_existed else None
    appended = append_index_row(index_path, row, filename)

    def _rollback(staged: list[str]):
        """Put the repo back exactly as found: unstage anything this run
        staged, restore/remove the files it wrote, drop the directory it
        created. No retry, no queue -- just a clean pre-run state."""
        if staged:
            run(["git", "-C", str(repo_root), "reset", "-q", "--", *staged])
        if wrote_dest:
            dest.unlink(missing_ok=True)
        if appended:
            if index_existed:
                index_path.write_text(index_prior, encoding="utf-8")
            else:
                index_path.unlink(missing_ok=True)
        if not archive_dir_existed and archive_dir.exists() and not any(archive_dir.iterdir()):
            archive_dir.rmdir()

    dest_committed = _is_committed(repo_root, rel_dest)
    index_committed = _is_committed(repo_root, rel_index)
    to_add = [str(p) for p, committed in
              ((rel_dest, dest_committed), (rel_index, index_committed)) if not committed]

    if to_add:
        if run(["git", "-C", str(repo_root), "add", "--", *to_add]).returncode != 0:
            print("FAIL: git add failed", file=sys.stderr)
            _rollback(staged=to_add)  # a partial add may have staged some
            return 1
        commit_message = f"session history: {base}\n\n{RICE_ARCHIVE_TRAILER}"
        r = run(["git", "-C", str(repo_root), "commit", "-m", commit_message, "--", *to_add])
        if r.returncode != 0:
            print(f"FAIL: git commit failed:\n{r.stdout}{r.stderr}", file=sys.stderr)
            _rollback(staged=to_add)
            return 1
        print(f"committed: {', '.join(to_add)}", file=sys.stderr)
    else:
        print(f"already written and committed: {rel_dest} (resuming)", file=sys.stderr)

    print(f"ARCHIVED {rel_dest} (committed locally; sync happens at the next SessionStart)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if "--sync" in sys.argv[1:]:
        raise SystemExit(sync_main())
    raise SystemExit(main())
