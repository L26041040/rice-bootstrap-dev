#!/usr/bin/env python3
"""RICE-BOOTSTRAP-046: what is true about the ENVIRONMENT this build runs in.

`reconcile.py` knows what rice owns in a repo and how to write it. This
module knows everything else the rubric needs and rice does not own: which
plugin build is installed and where, whether the OWNER's chosen output style
resolves, whether this build's hooks are wired, whether the OWNER's Matt
setup has landed, whether RTK is there, and what the harness recorded about
the git-deny rules.

It collects facts. It evaluates no gate, writes no file, repairs nothing and
has no verdict -- `verify.py` remains the only authority on PASS/FAIL, and
`repair.py` the only thing that changes anything. Every external command
goes through an injectable probe so the facts can be faked in a test and are
never guessed at in production: a probe that cannot answer produces
NOT_VERIFIED, which is not PASS, so a gate fails closed.

The one thing imported from `reconcile` is the item vocabulary
(`ItemResult` and the EXISTS/MATCHES/BEHAVES kinds) that both halves speak.
"""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import output_styles
import settings_template
from reconcile import BEHAVES, EXISTS, MATCHES, ItemResult

PLUGIN_ROOT = settings_template.PLUGIN_ROOT
build_version = settings_template.build_version  # what build is running (one reader)


def _repo_settings(repo_root: Path) -> dict:
    """The repo's settings.json, read the same lenient way the rubric reads
    everything else: unreadable is empty, and the items say so themselves."""
    p = Path(repo_root) / ".claude" / "settings.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


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


def _labels_item(repo_root: Path, github_probe) -> ItemResult:
    """RICE-BOOTSTRAP-UX-FIX-001: when Matt's setup chose GitHub Issues, the
    triage labels its vocabulary names exist on the repository. Read-only;
    creating them is repair's."""
    import github_labels  # local: it reads this module's Matt constants
    c = github_labels.check(repo_root, github_probe)
    if not c.applies:
        return ItemResult("matt:labels", "PASS", "the issue tracker is not GitHub Issues (or names no triage "
                          "labels): nothing to create", kind=EXISTS)
    if c.error:
        return ItemResult("matt:labels", "NOT_VERIFIED", f"triage labels on {c.repo or 'GitHub'}: {c.error}",
                          kind=EXISTS)
    if c.missing:
        return ItemResult("matt:labels", "FAIL", f"triage labels missing on {c.repo}: {', '.join(c.missing)}",
                          kind=EXISTS)
    return ItemResult("matt:labels", "PASS", f"all {len(c.wanted)} triage labels present on {c.repo}", kind=EXISTS)


def _default_rtk_probe(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


_default_plugin_probe = _default_rtk_probe


CANONICAL_SCOPE = "user"  # RICE-BOOTSTRAP-044C: rice is a user-scope plugin, like Matt's


def install_scopes(plugin_probe=None) -> tuple[dict, str | None]:
    """({scope: [versions]} for this plugin, error). The one read of *where*
    rice is installed, shared by the verifier's rubric and the bootstrap
    engine so there is never a second truth about it."""
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
            f"`claude plugin marketplace add {settings_template.MARKETPLACE_REPO} && claude plugin "
            f"install {settings_template.PLUGIN_KEY}`, then start a new session",
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
            f"installed {sorted(versions)} but this session is running build {bv} -- they must be "
            "the same build. If the install is already the release you want, start a fresh "
            "session (or run /reload-plugins) so it is the one that loads; otherwise reinstall it "
            f"(`claude plugin marketplace add {settings_template.MARKETPLACE_REPO}[@release/<x.y.z>]"
            f" && claude plugin install {settings_template.PLUGIN_KEY}`) and start a fresh session "
            "or run /reload-plugins",
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
    verifier's `plugin:scope` item and the bootstrap engine both read this,
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
    value = current_settings.get(settings_template.STYLE_KEY) if isinstance(current_settings, dict) else None
    ok, detail = output_styles.resolve(value, repo_root, plugin_probe,
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


def archive_behaves(repo_root, current_settings: dict | None = None) -> ItemResult:
    """A standalone, explicit diagnostic (RICE-BOOTSTRAP-032) -- deliberately
    NOT part of `check()`'s rubric, since every item the rubric produces
    must be required by a gate (RICE-BOOTSTRAP-021) and this one gates
    nothing. Call it directly (release E2E, a manual check) to ask: has
    every Rice archive commit this repo has produced actually reached the
    branch's upstream? PASS only from that git-state evidence -- never from
    local file evidence alone, which would be a false PASS whenever a
    commit is sitting unpushed."""
    import archive_hook  # local: the hook's git helpers, needed only here in this module

    repo_root = Path(repo_root)
    if current_settings is None:
        current_settings = _repo_settings(repo_root)
    env = current_settings.get("env") if isinstance(current_settings, dict) else None
    absent = object()
    switch = env.get("RICE_SESSION_ARCHIVE", absent) if isinstance(env, dict) else absent
    if switch is not absent and str(switch) != "1":
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




def collect(repo_root, *, rtk_probe=None, plugin_probe=None, github_probe=None, final=False) -> list[ItemResult]:
    """Every environment fact the rubric needs, as items. No verdict, no
    gate, no write -- see this module's docstring.

    `archive:behaves` is deliberately NOT here (RICE-BOOTSTRAP-032): every
    item a collector produces must be required by some gate, and that one
    gates nothing. It is a standalone diagnostic; call it directly."""
    rtk_probe = rtk_probe or _default_rtk_probe
    plugin_probe = plugin_probe or _default_plugin_probe
    repo_root = Path(repo_root)
    current = _repo_settings(repo_root)
    items = _plugin_items(plugin_probe)
    items.append(_plugin_scope_item(plugin_probe))
    items.append(_output_style_bundled_item())
    items.append(_output_style_choice_item(repo_root, current, plugin_probe))
    items.append(_archive_hook_item())
    items.append(_archive_sync_hook_item())
    items.append(_continuation_hook_item())
    items.append(_git_deny_evidence_item(repo_root))
    items.append(_matt_item(repo_root, plugin_probe))
    # GitHub is asked only when the labels gate anything: final verification
    items.append(_labels_item(repo_root, github_probe) if final else
                 ItemResult("matt:labels", "NOT_VERIFIED", "checked by final verification only", kind=EXISTS))
    items.append(_rtk_item(rtk_probe))
    return items
