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

import output_styles
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


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PROJECT_STATE_IMPORT_LINE = "@../PROJECT_STATE.md"

# Rubric kinds (RICE-BOOTSTRAP-020). EXISTS: the artifact/capability is
# there. MATCHES: its content/setting equals this build's canonical state.
# BEHAVES: only a real execution can confirm it -- the script can report
# evidence or say how to exercise it in-session, never fake a PASS.
EXISTS, MATCHES, BEHAVES = "EXISTS", "MATCHES", "BEHAVES"


@dataclass
class ItemResult:
    name: str
    state: str
    detail: str = ""
    kind: str = MATCHES


def build_version() -> str:
    """Version of the plugin build this rubric ships in -- the only version
    a PASS can ever be claimed for."""
    try:
        return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "?")
    except (OSError, json.JSONDecodeError):
        return "?"


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
            kind=EXISTS,
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
                kind=EXISTS,
            ))
        else:
            items.append(ItemResult(
                f"settings:{key}", "noop" if write else "PASS",
                "owner-defined; present, value not compared or reasserted",
                kind=EXISTS,
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


MATT_HEADING = "## Agent skills"
MATT_REQUIRED_DOCS = ("docs/agents/issue-tracker.md", "docs/agents/domain.md")
MATT_TRIAGE_DOC = "docs/agents/triage-labels.md"
MATT_TRIAGE_HEADING = "### Triage labels"


def _triage_capability_present(repo_root: Path, plugin_probe) -> bool:
    """Matt's setup writes the triage section only when its `triage` skill
    is installed. Mechanical signals, either suffices: the installed
    mattpocock plugin build ships a `triage` skill (from `claude plugin
    list --json` installPath), or the repo's CLAUDE.md carries the
    `### Triage labels` heading Matt writes in that case."""
    claude_md = repo_root / "CLAUDE.md"
    if claude_md.exists() and any(l.strip() == MATT_TRIAGE_HEADING for l in claude_md.read_text(encoding="utf-8").splitlines()):
        return True
    try:
        r = plugin_probe(["claude", "plugin", "list", "--json"])
        entries = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except (OSError, json.JSONDecodeError, AttributeError):
        entries = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and str(e.get("id", "")).startswith("mattpocock-skills@") and e.get("installPath"):
            root = Path(e["installPath"])
            if root.is_dir() and any(root.glob("skills/**/triage/SKILL.md")):
                return True
    return False


def _matt_item(repo_root: Path, plugin_probe) -> ItemResult:
    """Presence-only, never content-validated, never written here -- Matt's
    artifacts remain Matt/project-owned. Required shape is the real output
    observed from the official setup run in this repo (RICE-BOOTSTRAP-022):
    a `## Agent skills` heading line in CLAUDE.md, docs/agents/issue-tracker.md,
    docs/agents/domain.md, and docs/agents/triage-labels.md whenever the
    triage capability is present."""
    missing = []
    claude_md = repo_root / "CLAUDE.md"
    if not (claude_md.exists() and any(l.strip() == MATT_HEADING for l in claude_md.read_text(encoding="utf-8").splitlines())):
        missing.append(f"CLAUDE.md `{MATT_HEADING}` heading")
    required = list(MATT_REQUIRED_DOCS)
    if _triage_capability_present(repo_root, plugin_probe):
        required.append(MATT_TRIAGE_DOC)
    for rel in required:
        if not (repo_root / rel).is_file():
            missing.append(rel)
    if missing:
        return ItemResult("matt:artifacts", "FAIL", "official Matt setup artifacts missing: " + ", ".join(missing), kind=EXISTS)
    return ItemResult("matt:artifacts", "PASS", "present: " + ", ".join([f"`{MATT_HEADING}`", *required]), kind=EXISTS)


def _default_rtk_probe(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


_default_plugin_probe = _default_rtk_probe


def _installed_entries(plugin_probe):
    """Parsed `claude plugin list --json` (a top-level array), or an
    ItemResult explaining why it couldn't be read."""
    try:
        r = plugin_probe(["claude", "plugin", "list", "--json"])
    except FileNotFoundError:
        return None, "claude CLI not found on PATH"
    except OSError as exc:
        return None, f"could not run claude plugin list: {exc}"
    if r.returncode != 0:
        return None, f"claude plugin list exited {r.returncode}"
    try:
        entries = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return None, "claude plugin list --json was not valid JSON"
    if not isinstance(entries, list):
        return None, "unexpected claude plugin list --json shape"
    return [e for e in entries if isinstance(e, dict) and e.get("id") == settings_template.PLUGIN_KEY], None


def _plugin_items(plugin_probe) -> list[ItemResult]:
    """plugin:installed (EXISTS) and plugin:build (MATCHES).

    #18 runtime facts: a project settings.json declaring the marketplace/
    plugin does not by itself install it; only an actual install counts.
    apply() never installs. plugin:build compares the installed version with
    the build this rubric ships in -- if they differ, whatever ran this
    script is not the build the repo will actually load next session, so no
    PASS can be claimed for it."""
    mine, err = _installed_entries(plugin_probe)
    if err:
        return [ItemResult("plugin:installed", "NOT_VERIFIED", err, kind=EXISTS),
                ItemResult("plugin:build", "NOT_VERIFIED", err, kind=MATCHES)]
    if not mine:
        return [ItemResult(
            "plugin:installed", "FAIL",
            f"{settings_template.PLUGIN_KEY} is not installed on this machine; install it with "
            "`claude plugin marketplace add L26041040/rice-bootstrap && claude plugin install "
            f"{settings_template.PLUGIN_KEY}`, then start a new session",
            kind=EXISTS,
        ), ItemResult("plugin:build", "NOT_VERIFIED", "nothing installed to compare against", kind=MATCHES)]
    e = mine[0]
    installed = ItemResult(
        "plugin:installed", "PASS",
        "; ".join(f"{settings_template.PLUGIN_KEY} {x.get('version', '?')} (scope {x.get('scope', '?')}, "
                  f"enabled={x.get('enabled')})" for x in mine),
        kind=EXISTS,
    )
    bv = build_version()
    versions = {str(x.get("version")) for x in mine}
    if bv in versions:
        build = ItemResult("plugin:build", "PASS", f"installed {sorted(versions)} includes this rubric's build {bv}", kind=MATCHES)
    else:
        build = ItemResult(
            "plugin:build", "FAIL",
            f"installed {sorted(versions)} but this rubric is build {bv} -- update the install "
            "(`claude plugin update`) and/or start a new session so the loaded build matches",
            kind=MATCHES,
        )
    return [installed, build]


def _output_style_bundled_item() -> ItemResult:
    """EXISTS: this build ships the rice style (`name: 老弟`), addressable as
    rice:老弟 -- rice's own artifact, regardless of what the OWNER chose."""
    styles = PLUGIN_ROOT / "output-styles"
    for f in sorted(styles.glob("*.md")) if styles.is_dir() else []:
        if output_styles.frontmatter_name(f) == "老弟":
            return ItemResult("output-style:bundled", "PASS", f"{f.name} in build {build_version()} (addressed as {settings_template.RICE_STYLE})", kind=EXISTS)
    return ItemResult("output-style:bundled", "FAIL", "no output-styles/*.md with `name: 老弟` in this build", kind=EXISTS)


def _output_style_choice_item(repo_root: Path, current_settings: dict, plugin_probe) -> ItemResult:
    """MATCHES: whatever `outputStyle` the OWNER chose resolves to a real
    style. Absent key = Claude's native default (PASS). A value that points
    at nothing is FAIL -- rice never writes or 'fixes' this key."""
    value = _get_path(current_settings, "outputStyle")
    ok, detail = output_styles.resolve(None if value is _MISSING else value, repo_root, plugin_probe,
                                       self_plugin=(settings_template.PLUGIN_NAME, PLUGIN_ROOT))
    return ItemResult("output-style:choice", "PASS" if ok else "FAIL", detail, kind=MATCHES)


def _rice_md_import_item(repo_root: Path) -> ItemResult:
    dest = repo_root / ".claude" / "rice.md"
    if not dest.exists():
        return ItemResult("rice.md:imports-project-state", "FAIL", ".claude/rice.md missing", kind=MATCHES)
    if any(line.strip() == PROJECT_STATE_IMPORT_LINE for line in dest.read_text(encoding="utf-8").splitlines()):
        return ItemResult("rice.md:imports-project-state", "PASS", kind=MATCHES)
    return ItemResult("rice.md:imports-project-state", "FAIL", f"`{PROJECT_STATE_IMPORT_LINE}` line missing", kind=MATCHES)


def _project_state_item(repo_root: Path) -> ItemResult:
    """Read-only presence. Content is never inspected (#21)."""
    if (repo_root / "PROJECT_STATE.md").exists():
        return ItemResult("project-state:exists", "PASS", kind=EXISTS)
    return ItemResult("project-state:exists", "FAIL", "PROJECT_STATE.md missing (bootstrap creates it; rice-update restores it)", kind=EXISTS)


def _archive_hook_item() -> ItemResult:
    hooks = PLUGIN_ROOT / "hooks" / "hooks.json"
    script = PLUGIN_ROOT / "scripts" / "archive_hook.py"
    try:
        cfg = json.loads(hooks.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ItemResult("archive-hook:exists", "FAIL", "hooks/hooks.json missing or invalid in this build", kind=EXISTS)
    for entry in cfg.get("hooks", {}).get("SessionEnd", []):
        if entry.get("matcher") == "clear" and any("archive_hook.py" in h.get("command", "") for h in entry.get("hooks", [])):
            if script.exists():
                return ItemResult("archive-hook:exists", "PASS", kind=EXISTS)
            return ItemResult("archive-hook:exists", "FAIL", "hooks.json wires archive_hook.py but the script is missing", kind=EXISTS)
    return ItemResult("archive-hook:exists", "FAIL", "no SessionEnd(clear) -> archive_hook.py entry in hooks.json", kind=EXISTS)


def _archive_behaves_item(repo_root: Path, current_settings: dict) -> ItemResult:
    """BEHAVES: only a real /clear proves it. Evidence-based: an INDEX row
    whose archive file exists in this repo means it has already happened
    here. Never a PASS without that evidence."""
    switch = _get_path(current_settings, "env.RICE_SESSION_ARCHIVE")
    if switch is not _MISSING and str(switch) != "1":
        return ItemResult("archive:behaves", "PASS", "archiving is switched off for this repo (RICE_SESSION_ARCHIVE != 1); nothing to exercise", kind=BEHAVES)
    index = repo_root / "session-history" / "INDEX.md"
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5 and cells[-1].endswith(".md") and (repo_root / "session-history" / cells[-1]).exists():
                return ItemResult("archive:behaves", "PASS", f"evidence: INDEX row -> session-history/{cells[-1]} exists", kind=BEHAVES)
    return ItemResult("archive:behaves", "NOT_VERIFIED", "not yet exercised in this repo: do a real /clear with RICE_SESSION_ARCHIVE=1 and re-run check (see #29)", kind=BEHAVES)


import os
from datetime import datetime, timezone


def transcript_dir(repo_root: Path) -> Path:
    """Claude Code keeps one JSONL transcript per session under
    <config dir>/projects/<cwd with / replaced by ->/. Under normal Claude
    Code execution the harness writes every tool_result there itself, so a
    permission denial found there is mechanical evidence produced by the
    harness. rice never treats the agent's own statement as PASS; it reads
    this record instead. No stronger claim (tamper-proofness) is made."""
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    return cfg / "projects" / str(Path(repo_root).resolve()).replace("/", "-")


def _settings_mtime(repo_root: Path) -> float:
    p = repo_root / ".claude" / "settings.json"
    return p.stat().st_mtime if p.exists() else 0.0


def _iter_tool_outcomes(tdir: Path):
    """(timestamp, tool_name, tool_input, result_text) for every tool_use
    that has a tool_result, across this repo's session transcripts."""
    if not tdir.is_dir():
        return
    for f in sorted(tdir.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        uses, results = {}, []
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    c = (r.get("message") or {}).get("content")
                    if not isinstance(c, list):
                        continue
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            uses[b.get("id")] = (b.get("name"), b.get("input") or {})
                        elif b.get("type") == "tool_result":
                            txt = b.get("content")
                            if not isinstance(txt, str):
                                txt = " ".join(x.get("text", "") for x in (txt or []) if isinstance(x, dict))
                            results.append((r.get("timestamp") or "", b.get("tool_use_id"), txt))
        except OSError:
            continue
        for ts, uid, txt in results:
            if uid in uses:
                name, inp = uses[uid]
                yield ts, name, inp, txt


def _ts_to_epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


GIT_DENY_PROBE = "git branch --show-current"


def _git_deny_evidence_item(repo_root: Path) -> ItemResult:
    """BEHAVES, Gate A. Evidence = the harness recorded a denial for the
    exact probe command in a session of this repo, no older than the
    current settings.json. The probe is side-effect-free either way; the
    agent's role is to attempt it, the harness produces the record."""
    since = _settings_mtime(repo_root)
    for ts, name, inp, txt in _iter_tool_outcomes(transcript_dir(repo_root)):
        if name == "Bash" and (inp.get("command") or "").strip() == GIT_DENY_PROBE and "denied" in txt.lower():
            if _ts_to_epoch(ts) >= since:
                return ItemResult("git-deny:behaves", "PASS", f"harness denied `{GIT_DENY_PROBE}` at {ts}", kind=BEHAVES)
    return ItemResult(
        "git-deny:behaves", "NOT_VERIFIED",
        f"no harness denial of `{GIT_DENY_PROBE}` recorded for this repo since settings.json changed -- "
        "run that exact command through Bash in this repo (expected: denied), then re-run verify",
        kind=BEHAVES,
    )


def _premium_ask_evidence_item(repo_root: Path) -> ItemResult:
    """BEHAVES, Gate B (needs the OWNER at the prompt). Evidence = an Agent
    tool_use with model `opus` whose harness-recorded result is a denial /
    declined approval, no older than the current settings.json."""
    since = _settings_mtime(repo_root)
    for ts, name, inp, txt in _iter_tool_outcomes(transcript_dir(repo_root)):
        if name == "Agent" and str(inp.get("model") or "").lower() == "opus":
            low = txt.lower()
            if ("denied" in low or "doesn't want to proceed" in low or "does not want to proceed" in low) \
                    and _ts_to_epoch(ts) >= since:
                return ItemResult("premium-ask:behaves", "PASS", f"harness recorded the declined opus request at {ts}", kind=BEHAVES)
    return ItemResult(
        "premium-ask:behaves", "NOT_VERIFIED",
        "no declined `opus` subagent request recorded for this repo since settings.json changed -- with the OWNER "
        "present, request a subagent with model `opus`, have the OWNER decline the prompt, then re-run verify --final",
        kind=BEHAVES,
    )


def _rtk_item(rtk_probe) -> ItemResult:
    try:
        r = rtk_probe(["rtk", "--version"])
    except FileNotFoundError:
        return ItemResult("rtk", "FAIL", "rtk not found on PATH", kind=EXISTS)
    except OSError as exc:
        return ItemResult("rtk", "NOT_VERIFIED", f"could not run rtk --version: {exc}", kind=EXISTS)
    if r.returncode != 0:
        return ItemResult("rtk", "FAIL", f"rtk --version exited {r.returncode}", kind=EXISTS)
    return ItemResult("rtk", "PASS", r.stdout.strip(), kind=EXISTS)


def check(repo_root, *, rtk_probe=None, plugin_probe=None) -> list[ItemResult]:
    """Collect every rubric item of this build. This function has NO
    verdict authority: it returns items and nothing else. Gates, the
    RICE-VERIFIED / FINAL-VERIFIED lines, and the version stamp live in
    verify.py, which is the only place allowed to emit them."""
    rtk_probe = rtk_probe or _default_rtk_probe
    plugin_probe = plugin_probe or _default_plugin_probe
    repo_root = Path(repo_root)
    current = _current_settings(repo_root)
    ref = _current_ref(current)
    items = _plugin_items(plugin_probe)
    items.append(_output_style_bundled_item())
    items.append(_output_style_choice_item(repo_root, current, plugin_probe))
    settings_items, _ = _settings_items(current, ref, write=False, is_rice_update=False)
    items.extend(settings_items)
    items.append(_rice_md_item(repo_root, write=False, is_rice_update=False))
    items.append(_rice_md_import_item(repo_root))
    items.append(_claude_md_item(repo_root, write=False))
    items.append(_project_state_item(repo_root))
    items.append(_archive_hook_item())
    items.append(_git_deny_evidence_item(repo_root))
    items.append(_archive_behaves_item(repo_root, current))
    items.append(_premium_ask_evidence_item(repo_root))
    items.append(_matt_item(repo_root, plugin_probe))
    items.append(_rtk_item(rtk_probe))
    return items


def apply(repo_root, *, is_rice_update: bool = False, rtk_probe=None,
          plugin_probe=None) -> list[ItemResult]:
    rtk_probe = rtk_probe or _default_rtk_probe
    plugin_probe = plugin_probe or _default_plugin_probe
    repo_root = Path(repo_root)
    settings_path = repo_root / ".claude" / "settings.json"
    current = _current_settings(repo_root)
    ref = _current_ref(current)
    items = _plugin_items(plugin_probe)  # informational; apply never installs
    settings_items, mutated = _settings_items(current, ref, write=True, is_rice_update=is_rice_update)
    items.extend(settings_items)
    if any(i.state in ("created", "updated") for i in settings_items):
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(mutated, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                 encoding="utf-8")

    items.append(_rice_md_item(repo_root, write=True, is_rice_update=is_rice_update))
    items.append(_claude_md_item(repo_root, write=True))
    items.append(_matt_item(repo_root, plugin_probe))
    items.append(_rtk_item(rtk_probe))
    return items
