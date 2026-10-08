"""Ticket #23 / RICE-BOOTSTRAP-045/-046: what rice OWNS in a target repo,
and the four verbs over it -- `check_owned`, `apply`, `remove`, `remnants`.

Ownership is declared once here (RICE-BOOTSTRAP-045) and every lifecycle is
a verb over that declaration: install and update converge it, repair
restores what the verifier failed, uninstall subtracts it. Nothing else
keeps a list of what rice owns.

This module knows nothing about the ENVIRONMENT rice runs in -- no plugin
list, no hooks, no transcript, no Matt, no RTK, no subprocess at all. Those
are facts, not ownership, and they live in `evidence.py`
(RICE-BOOTSTRAP-046). It has no verdict either: it returns items, and
`verify.py` alone turns them into PASS or FAIL. And it has no git
responsibility: it writes files, it does not commit them.

Both `apply` and `check_owned` work against this build's desired state;
nothing about the plugin itself is recorded in the repo (RICE-FIX-001).
`apply(converge_drift=True)` is repair's mode: it overwrites rice-owned
state that drifted, which ordinary reconcile reports as a conflict and
leaves alone.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import settings_template

# --------------------------------------------------------------------------
# What rice owns in a target repo -- declared once (RICE-BOOTSTRAP-045).
#
# Every lifecycle is a different verb over these same declarations: install
# and update converge them, repair restores exactly the ones the verifier
# failed, uninstall subtracts them. No other module keeps a list.
#
# Three ownership shapes, and the shape decides what "safe on an existing
# repo" means for that artifact:
#
#   whole-file   `.claude/rice.md` -- rice wrote the file, so rice replaces
#                it wholesale. An ordinary reconcile reports a copy that
#                differs from this build's as a conflict and leaves it; the
#                repair every bootstrap runs on that FAIL restores it, so an
#                update -- or any re-run -- brings it back to this build's.
#                The OWNER's own rules belong in their own files, not here.
#   additive     the `@.claude/rice.md` line in CLAUDE.md, the marker block
#                in AGENTS.md, rice's own `permissions.deny` entries -- rice
#                owns exactly what it added and nothing around it. Install
#                adds what is missing, uninstall subtracts exactly that, and
#                project-owned bytes are never read as drift.
#   leaf         one settings value at one dotted path -- today only the
#                owner-defined `env.RICE_SESSION_ARCHIVE`, written once and
#                never reasserted, and the retired leaves an update or an
#                uninstall takes back wherever they still hold rice's value.
#
# The pending marker is rice-created repo state too, so it lives here with
# the rest of what rice puts in a repo (RICE-BOOTSTRAP-045 folded the old
# bootstrap_state module in). Only verify.py removes it, on a Gate B pass.
IMPORT_LINE = "@.claude/rice.md"

MARKER_NAME = "WAITING_FOR_MATT"
_MARKER_CONTENT = (
    "rice bootstrap is waiting for you to personally run the official\n"
    "Matt Pocock setup skill (setup-matt-pocock-skills). Claude cannot\n"
    "invoke it for you -- see .claude/rice.md.\n"
    "\n"
    "This file is removed automatically once that setup is verified\n"
    "complete.\n"
)


# Files rice creates in a target repo. The verified-version file is NOT one
# of them: verify.py owns that, and only verify.py writes it.
# Leaves rice used to write and no longer does, each with the exact value it
# wrote. Nothing re-creates or reasserts them; wherever one is found with
# that value it is rice's, and it is taken back -- by an update (re-running
# bootstrap, RICE-UPGRADE-MATRIX-001) and by uninstall alike. A different
# value at the same path is the project's and stays. RICE-BOOTSTRAP-046
# retired the subagent pin, written by the rice 0.1.1 builds public `main`
# shipped before release branches existed: which model a subagent uses is
# the OWNER's call per repo, so it became a working rule in `.claude/rice.md`
# instead of a setting.
RETIRED_LEAVES = {"env.CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}

OWNED_FILES = {
    ".claude/rice.md": "rice-md:absent",
    f".claude/{MARKER_NAME}": "waiting-marker:absent",
}


def _marker_path(repo_root) -> Path:
    return Path(repo_root) / ".claude" / MARKER_NAME


def enter_waiting_for_matt(repo_root) -> None:
    """The deterministic pending marker (#13 directive H): a fresh session can
    check it instead of Claude remembering across turns."""
    p = _marker_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(_MARKER_CONTENT, encoding="utf-8")


def is_waiting_for_matt(repo_root) -> bool:
    return _marker_path(repo_root).exists()

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

# settings_template's classification sets hold real dotted paths into the
# official settings shape, usable verbatim for lookup. Sorted so report
# order is stable.
_OWNER_LEAF_KEYS = sorted(settings_template.OWNER_DEFINED_KEYS)

# RICE-FIX-001: through 0.1.3, rice also declared itself at PROJECT scope --
# exactly the marketplace entry named `rice-bootstrap` pointing at this
# build's own public repo, and `enabledPlugins.rice@rice-bootstrap`. Rice's
# distribution is user scope only (RICE-BOOTSTRAP-044C); nothing here ever
# writes these keys again. They are identified narrowly enough that a
# project's own unrelated marketplace or plugin entry is never touched, and
# migrated away wherever they are found -- on `apply` (so re-running
# `/rice:bootstrap` on an old repo cleans them up before continuing) and on
# `remove` (so uninstalling one never leaves them behind either).
LEGACY_MARKETPLACE_PATH = f"extraKnownMarketplaces.{settings_template.MARKETPLACE_NAME}"
LEGACY_ENABLED_PLUGIN_PATH = f"enabledPlugins.{settings_template.PLUGIN_KEY}"

_MISSING = object()


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
    owned: bool = False  # rice owns this artifact: apply() can converge it,
    #                      so repair asks the item instead of keeping a list


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


def current_settings(repo_root) -> dict | None:
    """The repo's settings.json as a dict, or None when it exists but is not
    a JSON object (which uninstall must not silently treat as empty)."""
    p = Path(repo_root) / ".claude" / "settings.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _current_settings(repo_root: Path) -> dict:
    p = repo_root / ".claude" / "settings.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_rice_legacy_marketplace(entry) -> bool:
    """Only a marketplace entry of exactly the shape rice's own builds wrote
    -- `{"source": {"source": "github", "repo": <this build's public repo>,
    "ref": <a string>}}`, nothing more -- is 0.1.3-era rice state. The same
    repo with a key added, or anything else at the same name, is the
    project's, and is left alone."""
    if not isinstance(entry, dict) or set(entry) != {"source"}:
        return False
    source = entry["source"]
    return (isinstance(source, dict) and set(source) == {"source", "repo", "ref"}
            and source["source"] == "github" and source["repo"] == settings_template.MARKETPLACE_REPO
            and isinstance(source["ref"], str))


def _legacy_project_scope_present(d: dict) -> tuple[bool, bool]:
    """(rice's marketplace entry present, rice's enablement present). Every
    build that declared itself at project scope wrote the enablement as
    exactly `true` beside its own marketplace entry, so only that shape is
    rice's: a `false` (the project switching rice off), or the enablement of
    a same-named marketplace that is not rice's, is the project's."""
    mp = _get_path(d, LEGACY_MARKETPLACE_PATH)
    has_mp = mp is not _MISSING and _is_rice_legacy_marketplace(mp)
    has_plugin = (_get_path(d, LEGACY_ENABLED_PLUGIN_PATH) is True
                  and (mp is _MISSING or has_mp))
    return has_mp, has_plugin


def _legacy_project_scope_paths(d: dict) -> list[str]:
    """The paths of a 0.1.3-era declaration present in `d`, marketplace first."""
    has_mp, has_plugin = _legacy_project_scope_present(d)
    return [path for path, present in ((LEGACY_MARKETPLACE_PATH, has_mp), (LEGACY_ENABLED_PLUGIN_PATH, has_plugin))
            if present]


def _strip_legacy_project_scope(d: dict) -> bool:
    """Remove a 0.1.3-era project-scope Rice declaration in place. Returns
    whether anything was removed. Unconditional and always safe: the
    identity check above is narrow enough that nothing project-owned is
    ever at risk.

    0.1.2/0.1.3 created `extraKnownMarketplaces` and `enabledPlugins` to hold
    that declaration, so a container this removal empties goes with it
    (RICE-UPGRADE-MATRIX-001)."""
    paths = _legacy_project_scope_paths(d)
    for path in paths:
        _take_back(d, path)
    return bool(paths)


def _take_back(d: dict, dotted: str) -> None:
    """Delete one rice leaf and every container that deletion emptied --
    the rule uninstall's projection applies (`_prune_emptied`): a container
    rice emptied is rice's, one still holding a project entry stays."""
    parts = dotted.split(".")
    chain, cur = [d], d
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            return
        chain.append(cur)
    chain[-1].pop(parts[-1], None)
    for depth in range(len(parts) - 1, 0, -1):
        if chain[depth]:
            break
        chain[depth - 1].pop(parts[depth - 1], None)


def _retired_present(d: dict) -> list[str]:
    return [key for key, value in RETIRED_LEAVES.items() if _get_path(d, key) == value]


def _retired_leaves_item(current: dict, *, write: bool) -> ItemResult:
    """Every retired leaf still carrying the exact value rice wrote is
    rice's to take back -- on an update as on uninstall (RICE-UPGRADE-MATRIX-001).
    Until then the repo is not in this build's state, so the verifier cannot
    say final while one remains."""
    name = "settings:retired-leaves"
    present = _retired_present(current)
    if not present:
        return ItemResult(name, "noop" if write else "PASS", kind=EXISTS, owned=True)
    found = ", ".join(f"{k}={RETIRED_LEAVES[k]!r}" for k in present)
    if not write:
        return ItemResult(name, "FAIL", f"a setting rice no longer writes is still present ({found}) -- run "
                          "bootstrap again to take it back", kind=EXISTS, owned=True)
    for key in present:
        _take_back(current, key)
    return ItemResult(name, "removed", f"took back {found}, which rice no longer writes", kind=EXISTS, owned=True)


def _legacy_project_scope_item(current: dict, *, write: bool) -> ItemResult:
    name = "settings:legacy-project-scope"
    paths = _legacy_project_scope_paths(current)
    if not paths:
        return ItemResult(name, "noop" if write else "PASS", kind=EXISTS, owned=True)
    if not write:
        found = ", ".join(paths)
        return ItemResult(
            name, "FAIL",
            f"0.1.3-era project-scope Rice plugin declaration still present ({found}) -- rice is "
            "distributed at user scope only; run bootstrap again to migrate it away", kind=EXISTS, owned=True,
        )
    _strip_legacy_project_scope(current)
    return ItemResult(name, "removed", "migrated away a 0.1.3-era project-scope Rice plugin declaration",
                      kind=EXISTS, owned=True)


def _settings_items(repo_root: Path, current: dict, *, write: bool, converge_drift: bool):
    """Returns (items, current) -- `current` is mutated in place when
    write=True and an item needs creating/updating."""
    # read before anything below is written: it is the marker itself
    fresh = _is_fresh_install(repo_root)
    items = [_legacy_project_scope_item(current, write=write)]
    items.append(_retired_leaves_item(current, write=write))
    items.append(_deny_item(current, write=write, fresh=fresh))

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


DENY_PATH = settings_template.DENY_PATH
RICE_DENY_ENTRIES = settings_template.RICE_DENY_ENTRIES


def _deny_entries(current: dict):
    """(the repo's deny list, is it usable). A non-list value is not
    something rice can add to without destroying it."""
    cur = _get_path(current, DENY_PATH)
    if cur is _MISSING:
        return [], True
    return (cur, True) if isinstance(cur, list) else (cur, False)


def rice_installed_here(repo_root) -> bool:
    """Is rice installed in this repo, right now? RICE-FIX-001 retired the
    settings leaves that used to answer this (they were rice's PROJECT-scope
    plugin declaration, itself removed) -- deliberately not a manifest, this
    reads it instead from rice's own artifacts on disk: `.claude/rice.md`,
    the import line in CLAUDE.md, the pointer block in AGENTS.md. Any ONE is
    enough, the same way the old leaf pair required BOTH gone to read
    "fresh": losing a single artifact to an unrelated edit or a partial
    manual cleanup must never misread a still-bootstrapped repo as absent
    and skip taking back rice's own `permissions.deny` occurrence on
    uninstall. `apply` creates all three together; `remove` takes all three
    away together."""
    repo_root = Path(repo_root)
    if (repo_root / ".claude" / "rice.md").exists():
        return True
    claude_md = repo_root / "CLAUDE.md"
    if claude_md.exists() and any(
            l.strip() == IMPORT_LINE for l in claude_md.read_text(encoding="utf-8").splitlines()):
        return True
    agents_md = repo_root / "AGENTS.md"
    if agents_md.exists() and _agents_block_span(agents_md.read_text(encoding="utf-8")) is not None:
        return True
    return False


def _is_fresh_install(repo_root: Path) -> bool:
    """Is this apply the moment rice arrives in this repo -- the one moment
    it may add its own occurrence of a deny rule the project may already
    have (RICE-BOOTSTRAP-046)? Read from `rice_installed_here`, which is
    exactly this question asked before `apply` writes anything."""
    return not rice_installed_here(repo_root)


def _deny_item(current: dict, *, write: bool, fresh: bool = False) -> ItemResult:
    """Multiset ownership (RICE-BOOTSTRAP-045/-046): rice owns exactly ONE
    occurrence of each rule it declares, never the list.

    A fresh install appends one occurrence of every rule, even one the repo
    already denies itself -- so a project rule and rice's rule coexist
    (1 + 1 = 2) and uninstall can take back exactly rice's. Afterwards the
    repo is 'established' and convergence only restores a rule whose count
    has fallen to zero, so re-running bootstrap or an update can never grow
    the multiset. Whatever the repo already denies keeps its value, its
    order and its multiplicity, and is never read as drift."""
    name = f"settings:{DENY_PATH}"
    cur, usable = _deny_entries(current)
    if not usable:
        return ItemResult(name, "conflict" if write else "FAIL",
                          f"{DENY_PATH} is {type(cur).__name__}, not a list -- rice cannot add its "
                          "rules without destroying what is there", kind=EXISTS, owned=True)
    add = list(RICE_DENY_ENTRIES) if (write and fresh) else [e for e in RICE_DENY_ENTRIES if e not in cur]
    if not add:
        return ItemResult(name, "noop" if write else "PASS",
                          f"all {len(RICE_DENY_ENTRIES)} rice rules in effect; "
                          f"{len(cur) - len(RICE_DENY_ENTRIES)} other rule occurrence(s) untouched",
                          kind=EXISTS, owned=True)
    if write:
        _set_path(current, DENY_PATH, list(cur) + add)
        return ItemResult(name, "created" if not cur else "updated",
                          f"added rice's occurrence of {len(add)} rule(s); every rule already there kept",
                          kind=EXISTS, owned=True)
    return ItemResult(name, "FAIL", f"{len(add)} rice rule(s) not in effect: "
                      + ", ".join(add[:3]) + ("..." if len(add) > 3 else ""),
                      kind=EXISTS, owned=True)


def _rice_md_item(repo_root: Path, *, write: bool, converge_drift: bool) -> ItemResult:
    desired = RICE_MD_RESOURCE.read_text(encoding="utf-8")
    dest = repo_root / ".claude" / "rice.md"
    if not dest.exists():
        if write:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(desired, encoding="utf-8")
            return ItemResult("rice.md", "created", owned=True)
        return ItemResult("rice.md", "FAIL", "missing; run apply", owned=True)
    current = dest.read_text(encoding="utf-8")
    if current == desired:
        return ItemResult("rice.md", "noop" if write else "PASS", owned=True)
    if write and converge_drift:
        dest.write_text(desired, encoding="utf-8")
        return ItemResult("rice.md", "updated", owned=True)
    return ItemResult(
        "rice.md", "conflict" if write else "FAIL",
        "hand-edited or drifted at the same version; not overwritten", owned=True,
    )


def _claude_md_item(repo_root: Path, *, write: bool) -> ItemResult:
    dest = repo_root / "CLAUDE.md"
    if not dest.exists():
        if write:
            dest.write_text(f"{IMPORT_LINE}\n", encoding="utf-8")
            return ItemResult("CLAUDE.md:import-line", "created", "CLAUDE.md did not exist; created with only the import line", owned=True)
        return ItemResult("CLAUDE.md:import-line", "FAIL", "CLAUDE.md missing", owned=True)
    content = dest.read_text(encoding="utf-8")
    if any(line.strip() == IMPORT_LINE for line in content.splitlines()):
        return ItemResult("CLAUDE.md:import-line", "noop" if write else "PASS", owned=True)
    if write:
        sep = "" if content.endswith("\n") else "\n"
        dest.write_text(f"{content}{sep}\n{IMPORT_LINE}\n", encoding="utf-8")
        return ItemResult("CLAUDE.md:import-line", "created", "import line appended; rest of file untouched", owned=True)
    return ItemResult("CLAUDE.md:import-line", "FAIL", "import line missing", owned=True)


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


def _agents_md_item(repo_root: Path, *, write: bool, converge_drift: bool) -> ItemResult:
    """The rice pointer block in AGENTS.md: created if the file or block is
    missing, left alone when exact, and -- like rice.md -- treated as a
    conflict when hand-edited unless repair is converging the repo. Project-owned content outside the markers is never touched."""
    name = "AGENTS.md:rice-pointer"
    dest = repo_root / "AGENTS.md"
    if not dest.exists():
        if write:
            dest.write_text(AGENTS_POINTER, encoding="utf-8")
            return ItemResult(name, "created", "AGENTS.md did not exist; created with only the rice pointer block", kind=EXISTS, owned=True)
        return ItemResult(name, "FAIL", "AGENTS.md missing", kind=EXISTS, owned=True)
    content = dest.read_text(encoding="utf-8")
    span = _agents_block_span(content)
    if span is None:
        if write:
            sep = "" if (content.endswith("\n") or not content) else "\n"
            dest.write_text(f"{content}{sep}\n{AGENTS_POINTER}" if content else AGENTS_POINTER, encoding="utf-8")
            return ItemResult(name, "created", "rice pointer block appended; rest of file untouched", kind=EXISTS, owned=True)
        return ItemResult(name, "FAIL", "rice pointer block missing", kind=EXISTS, owned=True)
    if content[span[0]:span[1]] == AGENTS_POINTER:
        return ItemResult(name, "noop" if write else "PASS", kind=EXISTS, owned=True)
    if write and converge_drift:
        dest.write_text(content[:span[0]] + AGENTS_POINTER + content[span[1]:], encoding="utf-8")
        return ItemResult(name, "updated", "rice pointer block replaced; rest of file untouched", kind=EXISTS, owned=True)
    return ItemResult(name, "conflict" if write else "FAIL",
                      "rice pointer block hand-edited or drifted; not overwritten", kind=EXISTS, owned=True)


def check_owned(repo_root) -> list[ItemResult]:
    """Every rice-owned artifact in this repo, compared with what this build
    declares. Owned state only: no probe, no subprocess, no environment
    (RICE-BOOTSTRAP-046) -- `evidence.py` answers those, and `verify.py`
    alone turns both into a verdict. This function has NO verdict authority:
    it returns items and nothing else."""
    repo_root = Path(repo_root)
    current = _current_settings(repo_root)
    items, _ = _settings_items(repo_root, current, write=False, converge_drift=False)
    items.append(_rice_md_item(repo_root, write=False, converge_drift=False))
    items.append(_claude_md_item(repo_root, write=False))
    items.append(_agents_md_item(repo_root, write=False, converge_drift=False))
    return items


UNSET = object()  # "the OWNER was not asked", as distinct from None


def write_owner_choices(repo_root, *, archive=None, output_style=UNSET) -> list[ItemResult]:
    """Bootstrap's first -- and only -- write of the OWNER-defined keys.

    Deliberately NOT part of `apply`: `env.RICE_SESSION_ARCHIVE` and
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
    wanted = [("env.RICE_SESSION_ARCHIVE", None if archive is None else ("1" if archive else "0"),
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


def apply(repo_root, *, converge_drift: bool = False) -> list[ItemResult]:
    """Converge every rice-owned artifact onto what this build declares.
    Create-if-missing / no-op-if-correct / conflict-if-drifted, plus
    overwrite-on-update for the artifacts rice owns outright. Never installs
    anything, never inspects the environment, never commits."""
    repo_root = Path(repo_root)
    settings_path = repo_root / ".claude" / "settings.json"
    current = _current_settings(repo_root)
    items, mutated = _settings_items(repo_root, current, write=True, converge_drift=converge_drift)
    if any(i.state in ("created", "updated", "removed") for i in items):
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(mutated, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    items.append(_rice_md_item(repo_root, write=True, converge_drift=converge_drift))
    items.append(_claude_md_item(repo_root, write=True))
    items.append(_agents_md_item(repo_root, write=True, converge_drift=converge_drift))
    return items


def project_settings(repo_root, current: dict) -> dict:
    """`current` minus rice's own entries. A 0.1.3-era project-scope
    declaration goes unconditionally (RICE-FIX-001); owner-defined leaves and
    retired ones go by key/value; deny entries are subtracted one by one,
    keeping every project rule in its original order; containers rice
    emptied are dropped, containers with project content stay.

    `repo_root` decides whether the deny subtraction runs at all --
    `rice_installed_here(repo_root)`, read at the moment this is called, is
    rice's only marker of "was rice actually here" now that there is no
    settings leaf left to answer it. That makes removal idempotent by
    construction: called before `remove()` deletes `.claude/rice.md` it sees
    rice installed and subtracts; called again afterwards (a second
    uninstall, or a plain check) it does not, so a project's own identical
    deny rule is never eaten."""
    out = json.loads(json.dumps(current))  # deep copy, JSON-shaped
    was_installed = rice_installed_here(repo_root)
    _strip_legacy_project_scope(out)
    for key in _OWNER_LEAF_KEYS:
        _del_path(out, key)
    for key in _retired_present(out):
        _del_path(out, key)
    cur, usable = _deny_entries(out)
    if usable and cur and was_installed:
        kept = _subtract_rice_deny(cur)
        if kept:
            _set_path(out, DENY_PATH, kept)
        else:
            _del_path(out, DENY_PATH)
    if _get_path(out, "outputStyle") == settings_template.RICE_STYLE:
        _del_path(out, "outputStyle")
    return _prune_emptied(out, current)


def _subtract_rice_deny(entries: list) -> list:
    """Take back exactly one occurrence of each rule rice declares -- the
    last one, which is where a fresh install appended it, so a project's own
    copy and its position survive."""
    out = list(entries)
    for rule in RICE_DENY_ENTRIES:
        for i in range(len(out) - 1, -1, -1):
            if out[i] == rule:
                del out[i]
                break
    return out


def _del_path(d: dict, dotted: str) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _prune_emptied(projected, original):
    """Drop only the containers this projection emptied. A container the
    project itself left empty (`"hooks": {}`) is project-owned and stays --
    dropping it would be rice deleting something it never put there."""
    if not isinstance(projected, dict):
        return projected
    was = original if isinstance(original, dict) else {}
    out = {}
    for k, v in projected.items():
        v = _prune_emptied(v, was.get(k))
        if isinstance(v, dict) and not v and was.get(k) != {}:
            continue  # rice emptied it
        out[k] = v
    return out


def project_claude_md(text: str) -> str | None:
    """CLAUDE.md without the rice import line (and the blank line apply put in
    front of it when appending). None = nothing project-owned was ever in it
    (rice created it with only the import line) -> the file goes too."""
    lines = text.splitlines(keepends=True)
    kept = [l for l in lines if l.strip() != IMPORT_LINE]
    if not "".join(kept).strip():
        return None
    if len(kept) < len(lines):
        while len(kept) >= 2 and kept[-1].strip() == "" and kept[-2].endswith("\n"):
            kept.pop()
    return "".join(kept)


def project_agents_md(text: str) -> str | None:
    """AGENTS.md without the rice pointer block. None = rice created the file
    with nothing but that block."""
    span = _agents_block_span(text)
    if span is None:
        return text
    kept = text[:span[0]] + text[span[1]:]
    if not kept.strip():
        return None
    if kept.endswith("\n\n"):
        kept = kept[:-1]
    return kept


def remove(repo_root) -> list[str]:
    """Subtract rice from this repo. Returns the actions taken; idempotent,
    and byte-for-byte a no-op on everything rice does not own."""
    repo_root = Path(repo_root)
    actions = []
    s_path = repo_root / ".claude" / "settings.json"
    current = current_settings(repo_root) if s_path.exists() else None
    # computed before `.claude/rice.md` is deleted below: `project_settings`
    # reads "was rice here" from that file, and this is the one moment its
    # answer must be "yes" for a repo rice actually configured.
    projected = project_settings(repo_root, current) if current is not None else None
    for rel in OWNED_FILES:
        path = repo_root / rel
        if path.exists():
            path.unlink(); actions.append(f"removed {rel}")
    if current is not None:  # None = not a JSON object: rice touches nothing it cannot read
        if projected != current:
            if projected:
                s_path.write_text(json.dumps(projected, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
                actions.append("removed rice entries from .claude/settings.json")
            else:
                s_path.unlink(); actions.append("removed .claude/settings.json (held only rice entries)")
    for rel, project_view, what in (("CLAUDE.md", project_claude_md, f"`{IMPORT_LINE}` line"),
                                    ("AGENTS.md", project_agents_md, "rice pointer block")):
        path = repo_root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        projected = project_view(text)
        if projected is None:
            path.unlink(); actions.append(f"removed {rel} (rice-created, held only the {what})")
        elif projected != text:
            path.write_text(projected, encoding="utf-8"); actions.append(f"removed the {what} from {rel}")
    return actions


def remnants(repo_root) -> dict:
    """{label: detail or ""} for every rice-owned thing still present after a
    removal -- the mechanical answer to "is rice gone". An empty detail means
    the label is clean; uninstall.py turns this into its PASS/FAIL items."""
    repo_root = Path(repo_root)
    out = {}
    for rel, label in OWNED_FILES.items():
        out[label] = f"{rel} is still there" if (repo_root / rel).exists() else ""
    for rel, label, project_view, what in (
            ("CLAUDE.md", "claude-md:import-absent", project_claude_md, f"the `{IMPORT_LINE}` line"),
            ("AGENTS.md", "agents-md:pointer-absent", project_agents_md, "the rice pointer block")):
        path = repo_root / rel
        text = path.read_text(encoding="utf-8") if path.exists() else None
        out[label] = f"{rel} still carries {what}" if (text is not None and project_view(text) != text) else ""
    current = current_settings(repo_root)
    if current is None:
        out["settings:rice-absent"] = ".claude/settings.json is not a JSON object"
    else:
        out["settings:rice-absent"] = ("rice entries remain in .claude/settings.json"
                                       if project_settings(repo_root, current) != current else "")
    return out

