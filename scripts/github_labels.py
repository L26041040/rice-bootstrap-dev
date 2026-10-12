#!/usr/bin/env python3
"""RICE-BOOTSTRAP-UX-FIX-001: the triage labels Matt's setup names, on the
GitHub repository they are meant for.

Matt's official setup writes `docs/agents/triage-labels.md` -- the label
vocabulary the OWNER chose -- but creates nothing on GitHub. When the OWNER
picked GitHub Issues as the tracker, those labels must exist there before
rice says FINAL-VERIFIED. This module reads the vocabulary from Matt's file
(names and meanings, never guessed; no colors, since Matt defines none),
finds the repository from `origin`, and lists or creates labels through the
GitHub REST API with whatever credential the environment already has:
GH_TOKEN / GITHUB_TOKEN, git's own credential helper, or `gh auth token`
when gh happens to be installed -- gh is never required.

Nothing here is a verdict: `evidence` asks `check`, `repair` calls
`create_missing`, and the verifier alone decides.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence  # noqa: E402

TRACKER_DOC = evidence.MATT_REQUIRED_DOCS[0]
GITHUB_TRACKER_HEADING = "# Issue tracker: GitHub"  # the first line of Matt's GitHub template
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")      # a git proxy in front of github.com (Claude Code on the web)
TIMEOUT_SECONDS = 15

AUTHORIZE = ("give rice a GitHub credential that may manage issues on {repo} -- set GH_TOKEN (or "
             "GITHUB_TOKEN), sign git or `gh` in to GitHub, or on Claude Code on the web give the "
             "session's GitHub connection access to that repository -- then run the same command again")


@dataclass
class Check:
    applies: bool = False                          # a GitHub tracker with a triage vocabulary
    repo: str = ""                                 # owner/name
    wanted: list = field(default_factory=list)     # [(name, meaning)] from Matt's file
    missing: list = field(default_factory=list)    # names not on the repository
    error: str = ""                                # why the repository could not be read


def vocabulary(repo_root) -> list[tuple[str, str]] | None:
    """[(label, meaning)] from Matt's triage file when the tracker is GitHub,
    else None. The right-hand columns of Matt's table are the OWNER's
    answers (the defaults, or the names they chose instead)."""
    repo_root = Path(repo_root)
    tracker, triage = repo_root / TRACKER_DOC, repo_root / evidence.MATT_TRIAGE_DOC
    if not (tracker.is_file() and triage.is_file()):
        return None
    # Matt's template opens with `# Issue tracker: GitHub`; an agent adapting it
    # may vary the case or add a word, never the tracker's name
    first = next((l.strip() for l in tracker.read_text(encoding="utf-8").splitlines() if l.strip()), "").lower()
    if not (first.startswith("# issue tracker:") and "github" in first):
        return None
    rows, seen = [], set()
    for line in triage.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set(cells[1]) <= set("-: ") or "Label in our tracker" in cells[1]:
            continue
        name = cells[1].strip("`").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            rows.append((name, cells[2]))
    return rows


def repository(repo_root) -> tuple[str, str] | None:
    """(API base, owner/name) of `origin`, or None when it is not a GitHub
    URL: https / ssh / scp-style github.com (or a GitHub Enterprise host),
    and the local git proxy Claude Code on the web puts in front of
    github.com (`http://...@127.0.0.1:<port>/git/<owner>/<repo>`)."""
    r = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", "origin"], capture_output=True, text=True)
    url = r.stdout.strip() if r.returncode == 0 else ""
    m = re.match(r"^(?:[a-z+]+://)?(?:.*@)?([^/:@]+)(?::(\d+))?[/:](.+?)(?:\.git)?/?$", url)
    if not m or url.startswith("/"):
        return None
    host, _port, path = m.groups()
    parts = [p for p in path.split("/") if p]
    if host in LOCAL_HOSTS:
        if len(parts) >= 3 and parts[-3] == "git":
            return "https://api.github.com", f"{parts[-2]}/{parts[-1]}"
        return None
    if len(parts) != 2:
        return None
    api = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    return api, f"{parts[0]}/{parts[1]}"


@lru_cache(maxsize=None)
def _token(host: str = "github.com", cwd: str | None = None) -> str | None:
    """A credential the environment already holds, for `host` only: the
    GH_TOKEN / GITHUB_TOKEN environment for github.com, else git's own
    credential helper for that host (never interactive), else `gh auth
    token` when gh is installed -- once per run."""
    if host == "github.com":
        for var in ("GH_TOKEN", "GITHUB_TOKEN"):
            if os.environ.get(var):
                return os.environ[var]
    quiet = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
    try:
        r = subprocess.run(["git", "-c", "credential.interactive=false", "credential", "fill"],
                           input=f"protocol=https\nhost={host}\n\n", capture_output=True, text=True,
                           env=quiet, cwd=cwd, timeout=10)
        for line in r.stdout.splitlines() if r.returncode == 0 else []:
            if line.startswith("password=") and line[9:]:
                return line[9:]
        if shutil.which("gh"):
            r = subprocess.run(["gh", "auth", "token", "--hostname", host], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def default_probe(method: str, url: str, body: dict | None = None) -> tuple[int, object]:
    """One GitHub REST call: (HTTP status, parsed JSON or text); status 0 when
    GitHub could not be reached at all."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    host = urllib.parse.urlsplit(url).hostname or ""
    token = _token("github.com" if host == "api.github.com" else host)  # a credential goes only to its own host
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            text = r.read().decode("utf-8", errors="replace")
            status = r.status
    except urllib.error.HTTPError as e:
        text, status = e.read().decode("utf-8", errors="replace"), e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, text


def _refusal(status: int, payload, repo: str) -> str:
    if status == 0:
        return f"could not reach GitHub ({payload})"
    message = payload.get("message", "") if isinstance(payload, dict) else str(payload)[:200]
    if status in (401, 403, 404):
        return f"GitHub answered HTTP {status} ({message}) -- {AUTHORIZE.format(repo=repo)}"
    return f"GitHub answered HTTP {status} ({message})"


def check(repo_root, probe=None) -> Check:
    """Which of Matt's triage labels the repository lacks. Reads only."""
    probe = probe or default_probe
    wanted = vocabulary(repo_root)
    if wanted is None:
        return Check()
    if not wanted:
        return Check(applies=True, error=f"no label could be read from {evidence.MATT_TRIAGE_DOC}")
    where = repository(repo_root)
    if where is None:
        return Check(applies=True, wanted=wanted,
                     error="the issue tracker is GitHub Issues but `origin` is not a GitHub repository")
    api, repo = where
    names, page = set(), 1
    while True:
        status, payload = probe("GET", f"{api}/repos/{repo}/labels?per_page=100&page={page}")
        if status != 200 or not isinstance(payload, list):
            return Check(applies=True, repo=repo, wanted=wanted, error=_refusal(status, payload, repo))
        names |= {str(l.get("name", "")).lower() for l in payload if isinstance(l, dict)}
        if len(payload) < 100:
            break
        page += 1
    return Check(applies=True, repo=repo, wanted=wanted,
                 missing=[n for n, _ in wanted if n.lower() not in names])


def create_missing(repo_root, probe=None) -> tuple[list[str], str, str]:
    """Create the labels the repository lacks, each once (never a label it
    already has, compared as GitHub does, ignoring case). Returns (created,
    repo, why the rest could not be) -- an empty reason means nothing is
    left missing."""
    probe = probe or default_probe
    c = check(repo_root, probe)
    if not c.applies or c.error:
        return [], c.repo, c.error
    api, _ = repository(repo_root)
    meanings, created = dict(c.wanted), []
    for name in c.missing:
        status, payload = probe("POST", f"{api}/repos/{c.repo}/labels",
                                {"name": name, "description": meanings.get(name, "")[:100]})
        if status == 201:
            created.append(name)
        elif not (status == 422 and "already_exists" in json.dumps(payload)):
            return created, c.repo, _refusal(status, payload, c.repo)
    return created, c.repo, ""
