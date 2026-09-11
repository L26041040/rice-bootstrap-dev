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
bootstrap's job at first-write.

Matt setup artifacts and RTK are REQUIRED (v1 is the OWNER's actual
workflow; Gate B never passes without both). They are presence-only,
never content-validated, and never written by apply -- Matt/environment
own that content.

This module has no git responsibility of its own: it writes files, it
does not commit them. Committing rice-owned changes is the caller's job
(bootstrap #25 / rice-update #26).
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import archive_hook
import output_styles
import settings_template

IMPORT_LINE = "@.claude/rice.md"

# RICE-BOOTSTRAP-042: AGENTS.md is the cross-agent entry point. Codex (and
# every other AGENTS.md reader checked) concatenates the file verbatim and has
# no import syntax, so the only thing that can be mechanical there is an
# instruction to read the canonical file -- never a copy of it. Claude Code
# reads CLAUDE.md, not AGENTS.md, and there `@.claude/rice.md` is a real
# import, so Claude gets the rules mechanically through CLAUDE.md. Either way
# the rules themselves live in exactly one place: .claude/rice.md.
AGENTS_BEGIN = "<!-- rice:begin -->"
AGENTS_END = "<!-- rice:end -->"
AGENTS_POINTER = (
    f"{AGENTS_BEGIN}\n"
    "## Rice working rules\n"
    "\n"
    "Before doing anything in this repo, read `.claude/rice.md` and follow it.\n"
    "It is the single canonical source of the OWNER's working rules for every\n"
    "coding agent; nothing here restates it.\n"
    f"{AGENTS_END}\n"
)
RICE_MD_RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "rice.md"

REF_PATH = settings_template.REF_PATH
# settings_template's classification sets hold real dotted paths into the
# official settings shape, usable verbatim for lookup. Sorted so report
# order is stable.
_RICE_LEAF_KEYS = sorted(settings_template.RICE_DEFINED_KEYS)
_OWNER_LEAF_KEYS = sorted(settings_template.OWNER_DEFINED_KEYS)

_MISSING = object()


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

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


def _agents_block_span(content: str) -> tuple[int, int] | None:
    """(start, end) of the rice-owned block in AGENTS.md, markers included,
    or None. Everything outside the markers is project-owned."""
    b = content.find(AGENTS_BEGIN)
    if b < 0:
        return None
    e = content.find(AGENTS_END, b)
    if e < 0:
        return None
    e += len(AGENTS_END)
    if content[e:e + 1] == "\n":
        e += 1
    return b, e


def _agents_md_item(repo_root: Path, *, write: bool, is_rice_update: bool) -> ItemResult:
    """The rice pointer block in AGENTS.md: created if the file or block is
    missing, left alone when exact, and -- like rice.md -- treated as a
    conflict when hand-edited unless a rice-update (or repair) is converging
    the repo. Project-owned content outside the markers is never touched."""
    name = "AGENTS.md:rice-pointer"
    dest = repo_root / "AGENTS.md"
    if not dest.exists():
        if write:
            dest.write_text(AGENTS_POINTER, encoding="utf-8")
            return ItemResult(name, "created", "AGENTS.md did not exist; created with only the rice pointer block", kind=EXISTS)
        return ItemResult(name, "FAIL", "AGENTS.md missing", kind=EXISTS)
    content = dest.read_text(encoding="utf-8")
    span = _agents_block_span(content)
    if span is None:
        if write:
            sep = "" if (content.endswith("\n") or not content) else "\n"
            dest.write_text(f"{content}{sep}\n{AGENTS_POINTER}" if content else AGENTS_POINTER, encoding="utf-8")
            return ItemResult(name, "created", "rice pointer block appended; rest of file untouched", kind=EXISTS)
        return ItemResult(name, "FAIL", "rice pointer block missing", kind=EXISTS)
    if content[span[0]:span[1]] == AGENTS_POINTER:
        return ItemResult(name, "noop" if write else "PASS", kind=EXISTS)
    if write and is_rice_update:
        dest.write_text(content[:span[0]] + AGENTS_POINTER + content[span[1]:], encoding="utf-8")
        return ItemResult(name, "updated", "rice pointer block replaced; rest of file untouched", kind=EXISTS)
    return ItemResult(name, "conflict" if write else "FAIL",
                      "rice pointer block hand-edited or drifted; not overwritten", kind=EXISTS)


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


MATT_HEADING_LABEL = f"CLAUDE.md `{MATT_HEADING}` heading"


def matt_presence(repo_root: Path, plugin_probe) -> dict[str, bool]:
    """Every Matt artifact this repo requires -> is it there. Keys are the
    labels the verifier reports (`MATT_HEADING_LABEL` for the CLAUDE.md
    section, relative paths for the docs). Presence only, never content."""
    claude_md = repo_root / "CLAUDE.md"
    present = {MATT_HEADING_LABEL: claude_md.exists() and any(
        l.strip() == MATT_HEADING for l in claude_md.read_text(encoding="utf-8").splitlines())}
    required = list(MATT_REQUIRED_DOCS)
    if _triage_capability_present(repo_root, plugin_probe):
        required.append(MATT_TRIAGE_DOC)
    for rel in required:
        present[rel] = (repo_root / rel).is_file()
    return present


def _matt_item(repo_root: Path, plugin_probe) -> ItemResult:
    """Presence-only, never content-validated, never written here -- Matt's
    artifacts remain Matt/project-owned. Required shape is the real output
    observed from the official setup run in this repo (RICE-BOOTSTRAP-022):
    a `## Agent skills` heading line in CLAUDE.md, docs/agents/issue-tracker.md,
    docs/agents/domain.md, and docs/agents/triage-labels.md whenever the
    triage capability is present."""
    present = matt_presence(repo_root, plugin_probe)
    missing = [label for label, ok in present.items() if not ok]
    required = [label for label in present if label != MATT_HEADING_LABEL]
    if missing:
        return ItemResult("matt:artifacts", "FAIL", "official Matt setup artifacts missing: " + ", ".join(missing), kind=EXISTS)
    return ItemResult("matt:artifacts", "PASS", "present: " + ", ".join([f"`{MATT_HEADING}`", *required]), kind=EXISTS)


def _default_rtk_probe(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


_default_plugin_probe = _default_rtk_probe


CANONICAL_SCOPE = "user"  # RICE-BOOTSTRAP-044C: rice is a user-scope plugin, like Matt's


def install_scopes(plugin_probe=None) -> tuple[dict, str | None]:
    """({scope: [versions]} for this plugin, error). The one read of *where*
    rice is installed, shared by the verifier's rubric and the update
    lifecycle so there is never a second truth about it."""
    entries, err = installed_entries(plugin_probe)
    if err:
        return {}, err
    scopes: dict[str, list[str]] = {}
    for e in entries or []:
        scopes.setdefault(str(e.get("scope") or "?"), []).append(str(e.get("version")))
    return scopes, None


def installed_entries(plugin_probe=None):
    """(this plugin's entries from `claude plugin list --json`, error) --
    the public read of what is actually installed, for callers that need the
    install state itself rather than a rubric item."""
    return _installed_entries(plugin_probe or _default_plugin_probe)


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


def classify_install(plugin_probe=None) -> tuple:
    """(canonical versions, state, detail, scopes) -- the single decision on
    whether this environment's rice install is the supported one
    (RICE-BOOTSTRAP-044C).

    Rice's canonical install is user scope, exactly once, the same way Matt's
    plugin is installed. Two installs -- at different scopes, or twice at the
    canonical one -- is an unsupported duplicate, not a healthy install:
    which one a session loads is not something rice may guess at. The
    verifier's `plugin:scope` item and the update lifecycle both read this,
    so there is never a second truth about it; only how the answer is
    presented differs."""
    scopes, err = install_scopes(plugin_probe)
    if err:
        # `scopes is None` means the question could not be asked at all, which
        # is not the same answer as the empty `{}` of "nothing is installed"
        return None, "NOT_VERIFIED", err, None
    if not scopes:
        return None, "NOT_VERIFIED", "nothing installed to classify", {}
    where = "; ".join(f"{k} {sorted(set(v))}" for k, v in sorted(scopes.items()))
    if "?" in scopes:
        # the install list did not say where this came from: unclassifiable,
        # never a FAIL whose only remedy would be an unrunnable command
        return None, "NOT_VERIFIED", f"`claude plugin list --json` reports no scope for this plugin ({where})", scopes
    others = sorted(k for k in scopes if k != CANONICAL_SCOPE)
    if CANONICAL_SCOPE not in scopes:
        return None, "FAIL", (
            f"{settings_template.PLUGIN_KEY} is installed at {where}, but the canonical install is "
            f"{CANONICAL_SCOPE} scope. Install it there (`claude plugin install "
            f"{settings_template.PLUGIN_KEY}`) and remove the non-canonical one(s): "
            + "; ".join(f"`claude plugin uninstall {settings_template.PLUGIN_KEY} --scope {k}`" for k in others)
        ), scopes
    if others:
        return None, "FAIL", (
            f"{settings_template.PLUGIN_KEY} is installed at more than one scope ({where}) -- an "
            f"unsupported duplicate installation. Keep the {CANONICAL_SCOPE}-scope install and "
            "remove the others: "
            + "; ".join(f"`claude plugin uninstall {settings_template.PLUGIN_KEY} --scope {k}`" for k in others)
        ), scopes
    versions = scopes[CANONICAL_SCOPE]
    if len(set(versions)) > 1:
        return None, "FAIL", (
            f"{settings_template.PLUGIN_KEY} is installed more than once at {CANONICAL_SCOPE} scope "
            f"({sorted(set(versions))}) -- an unsupported duplicate installation. Leave exactly one"
        ), scopes
    return versions, "PASS", where, scopes


def _plugin_scope_item(plugin_probe) -> ItemResult:
    _, state, detail, _ = classify_install(plugin_probe)
    return ItemResult("plugin:scope", state, detail, kind=EXISTS)


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


def _hook_wired_item(name: str, event: str, matcher: str | None, script_name: str,
                     *fragments: str) -> ItemResult:
    """Registration-only check, shared by every hook this build ships: is the
    hook wired in hooks.json for `event` (with `matcher`, when the event uses
    one) to a command containing every one of `fragments`, and does its script
    exist? Never asks whether the hook has actually run."""
    hooks = PLUGIN_ROOT / "hooks" / "hooks.json"
    script = PLUGIN_ROOT / "scripts" / script_name
    wanted = fragments or (script_name,)
    try:
        cfg = json.loads(hooks.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ItemResult(name, "FAIL", "hooks/hooks.json missing or invalid in this build", kind=EXISTS)
    for entry in cfg.get("hooks", {}).get(event, []):
        if entry.get("matcher") != matcher:
            continue
        if any(all(f in h.get("command", "") for f in wanted) for h in entry.get("hooks", [])):
            if script.exists():
                return ItemResult(name, "PASS", kind=EXISTS)
            return ItemResult(name, "FAIL", f"hooks.json wires {script_name} but the script is missing", kind=EXISTS)
    shape = f"{event}({matcher})" if matcher else event
    return ItemResult(name, "FAIL", f"no {shape} -> {' '.join(wanted)} entry in hooks.json", kind=EXISTS)


def _archive_hook_item() -> ItemResult:
    return _hook_wired_item("archive-hook:exists", "SessionEnd", "clear", "archive_hook.py")


def _archive_sync_hook_item() -> ItemResult:
    """RICE-BOOTSTRAP-032: registration-only, like archive-hook:exists --
    is the SessionStart sync hook wired, never has it actually run/pushed."""
    return _hook_wired_item("archive-sync-hook:exists", "SessionStart", "clear",
                            "archive_hook.py", "archive_hook.py", "--sync")


def _continuation_hook_item() -> ItemResult:
    """RICE-BOOTSTRAP-028: registration-only -- is the Stop hook that continues
    bootstrap after the OWNER's Matt setup wired, never has it actually run.
    No matcher: Stop takes none (confirmed by #27's runtime probe)."""
    return _hook_wired_item("continuation-hook:exists", "Stop", None, "bootstrap_continue.py")


def _archive_behaves_item(repo_root: Path, current_settings: dict) -> ItemResult:
    """A standalone, explicit diagnostic (RICE-BOOTSTRAP-032) -- deliberately
    NOT part of `check()`'s rubric, since every item the rubric produces
    must be required by a gate (RICE-BOOTSTRAP-021) and this one gates
    nothing. Call it directly (release E2E, a manual check) to ask: has
    every Rice archive commit this repo has produced actually reached the
    branch's upstream? PASS only from that git-state evidence -- never from
    local file evidence alone, which would be a false PASS whenever a
    commit is sitting unpushed."""
    switch = _get_path(current_settings, "env.RICE_SESSION_ARCHIVE")
    if switch is not _MISSING and str(switch) != "1":
        return ItemResult("archive:behaves", "PASS", "archiving is switched off for this repo (RICE_SESSION_ARCHIVE != 1); nothing to exercise", kind=BEHAVES)
    index = repo_root / "session-history" / "INDEX.md"
    if not index.exists():
        return ItemResult("archive:behaves", "NOT_VERIFIED", "no archive has been produced yet in this repo", kind=BEHAVES)
    branch = archive_hook._current_branch(repo_root)
    if not branch:
        return ItemResult("archive:behaves", "NOT_VERIFIED", "detached HEAD; cannot compare against an upstream", kind=BEHAVES)
    upstream = archive_hook._upstream_ref(repo_root, branch)
    if not upstream:
        return ItemResult("archive:behaves", "NOT_VERIFIED", f"{branch} has no upstream configured", kind=BEHAVES)
    safe, detail = archive_hook._range_is_safe_to_push(repo_root, upstream)
    if not safe:
        return ItemResult("archive:behaves", "NOT_VERIFIED", f"pending, unsafe to auto-push: {detail}", kind=BEHAVES)
    if detail == "nothing to sync":
        return ItemResult("archive:behaves", "PASS", "every produced archive commit is present on the upstream", kind=BEHAVES)
    return ItemResult("archive:behaves", "NOT_VERIFIED", f"pending: {detail}, not yet pushed", kind=BEHAVES)


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
    items.append(_plugin_scope_item(plugin_probe))
    items.append(_output_style_bundled_item())
    items.append(_output_style_choice_item(repo_root, current, plugin_probe))
    settings_items, _ = _settings_items(current, ref, write=False, is_rice_update=False)
    items.extend(settings_items)
    items.append(_rice_md_item(repo_root, write=False, is_rice_update=False))
    items.append(_claude_md_item(repo_root, write=False))
    items.append(_agents_md_item(repo_root, write=False, is_rice_update=False))
    items.append(_archive_hook_item())
    items.append(_archive_sync_hook_item())
    items.append(_continuation_hook_item())
    items.append(_git_deny_evidence_item(repo_root))
    # archive:behaves is deliberately NOT part of the rubric (RICE-BOOTSTRAP-032):
    # it is an explicit, standalone diagnostic (see `_archive_behaves_item`),
    # called directly by release E2E / manual checks, never mixed into
    # `check()`'s output -- every item this rubric produces must be required
    # by a gate (RICE-BOOTSTRAP-021), and archive:behaves gates nothing.
    items.append(_matt_item(repo_root, plugin_probe))
    items.append(_rtk_item(rtk_probe))
    return items


def current_ref(repo_root) -> str | None:
    """The delivery ref this repo is pinned to, or None if unset."""
    return _current_ref(_current_settings(Path(repo_root)))


def move_ref(repo_root, sha: str) -> ItemResult:
    """Repin the delivery ref. The update lifecycle is the ONLY caller (#13's
    "Reconcile, check, and rice-update"): ordinary reconcile never creates,
    fixes or reasserts this key, and bootstrap writes it only when absent."""
    repo_root = Path(repo_root)
    current = _current_settings(repo_root)
    was = _current_ref(current)
    if was == sha:
        return ItemResult(f"settings:{REF_PATH}", "noop", f"already pinned to {sha}", kind=EXISTS)
    _set_path(current, REF_PATH, sha)
    p = repo_root / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return ItemResult(f"settings:{REF_PATH}", "updated", f"{was!r} -> {sha!r}", kind=EXISTS)


UNSET = object()  # "the OWNER was not asked", as distinct from None


def write_owner_choices(repo_root, *, ref=None, archive=None, output_style=UNSET) -> list[ItemResult]:
    """Bootstrap's first -- and only -- write of the OWNER-defined keys.

    Deliberately NOT part of `apply`: `ref`, `env.RICE_SESSION_ARCHIVE` and
    `outputStyle` are the OWNER's, written once when they are absent and
    never created, fixed or reasserted by an ordinary reconcile pass (#13's
    Ownership model). Each value here is written only if its key is missing,
    so re-running bootstrap can never overwrite an OWNER's answer.

    `output_style` distinguishes three cases: UNSET (not asked -- leave the
    key exactly as it is), None (the OWNER chose Claude's native default --
    the key stays absent, never a placeholder value), or a style name."""
    repo_root = Path(repo_root)
    current = _current_settings(repo_root)
    items, wrote = [], False
    # (key, value, what an absent key means when there is no value to write)
    wanted = [(REF_PATH, ref, "not supplied; the key stays missing and the verifier will fail it"),
              ("env.RICE_SESSION_ARCHIVE", None if archive is None else ("1" if archive else "0"),
               "not supplied; the key stays missing and the verifier will fail it")]
    if output_style is not UNSET:
        # the one key whose absence is itself a valid answer: Claude's native default
        wanted.append(("outputStyle", output_style, "left absent by the OWNER's choice (Claude default)"))
    for key, value, absent_detail in wanted:
        cur = _get_path(current, key)
        if cur is not _MISSING:
            items.append(ItemResult(f"settings:{key}", "noop", "owner-defined; already answered", kind=EXISTS))
        elif value is None:
            items.append(ItemResult(f"settings:{key}", "noop", absent_detail, kind=EXISTS))
        else:
            _set_path(current, key, value)
            wrote = True
            items.append(ItemResult(f"settings:{key}", "created", f"set to {value!r}", kind=EXISTS))
    if wrote:
        p = repo_root / ".claude" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
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
    items.append(_agents_md_item(repo_root, write=True, is_rice_update=is_rice_update))
    items.append(_matt_item(repo_root, plugin_probe))
    items.append(_rtk_item(rtk_probe))
    return items
