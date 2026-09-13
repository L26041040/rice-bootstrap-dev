"""Ticket #15: the rice-owned desired-state template for a target repo's
.claude/settings.json.

This module defines DATA (what the settings should look like for the
current release), not the reconcile logic that writes/compares it -- that
lives in reconcile.py (ticket #23).

Every rice-owned leaf in settings.json is addressed by a dotted path and
classified as either rice-defined (kept in sync on every reconcile pass)
or owner-defined (written once at bootstrap, never reasserted -- see #13's
Ownership model). Paths are the REAL nesting of the official settings
schema (docs: plugin-marketplaces -> extraKnownMarketplaces), so they can
be used verbatim for lookup. Anything not listed here is project-owned and
never touched -- e.g. `permissions.allow`, other marketplaces, other
plugins, other env vars.

The subagent model is NOT a setting rice writes (RICE-BOOTSTRAP-046). It
was `env.CLAUDE_CODE_SUBAGENT_MODEL`, hard-enforcing Sonnet on every repo
rice touched -- a value the OWNER may legitimately want different per repo,
per task, or not at all. It is a working rule now, in `.claude/rice.md`:
prefer Sonnet for subagents, name another model when a task wants one.
Nothing here enforces it, and uninstall has nothing to take back.

Rice's own marketplace/plugin declaration is NOT a setting rice writes
either (RICE-FIX-001). Through 0.1.3 this template also declared
`extraKnownMarketplaces.rice-bootstrap` and `enabledPlugins.rice@rice-bootstrap`
at PROJECT scope, pinned to the release that bootstrapped the repo. Rice's
distribution is user scope only (RICE-BOOTSTRAP-044C) -- the Environment's
own setup script installs it once, the same way Matt Pocock's plugin is
installed -- and a project also declaring the plugin risked Claude Code
reading it as a second, unsupported install, exactly what `plugin:scope`
already exists to fail. Nothing here writes those keys again;
`reconcile.py` only ever migrates a 0.1.3-era declaration back out.

Git safety is permission-only (RICE-BOOTSTRAP-019 superseded the narrow
PreToolUse hook of #17): the whole `git branch` command class is denied,
so Claude never creates a branch through it and also can't use it to list
branches. Use `git status` or `git rev-parse --abbrev-ref HEAD` to see the
current branch instead.
"""
import json
import re
from pathlib import Path

MARKETPLACE_NAME = "rice-bootstrap"
MARKETPLACE_REPO = "L26041040/rice-bootstrap"
PLUGIN_NAME = "rice"
PLUGIN_KEY = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"

# RICE-FINAL-001: a release is a branch on the public repository, named for
# its exact version and immutable once pushed. That branch name IS the
# delivery ref a repo records -- derived from the build itself, so nothing
# ever asks upstream what "latest" is and the OWNER never types a SHA. It is
# rice-defined, not an OWNER choice: whichever build verifies a repo writes
# its own release branch there, so the record can never drift from the build
# that actually configured the repo. There is no update lifecycle; a newer
# rice is a newer Environment install, and a repo only changes when a newer
# build bootstraps it.
RELEASE_BRANCH_PREFIX = "release/"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def build_version() -> str:
    """The version of the build running this code -- the only version a PASS
    can ever be claimed for, and the release branch it came from."""
    try:
        return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "?")
    except (OSError, json.JSONDecodeError):
        return "?"


def release_ref(version: str | None = None) -> str | None:
    """The public branch that carries exactly this version, or None when the
    version is not readable SemVer -- rice never pins a ref it made up."""
    version = build_version() if version is None else version
    return f"{RELEASE_BRANCH_PREFIX}{version}" if _SEMVER.match(version or "") else None


RICE_STYLE = "rice:老弟"  # the plugin's bundled style, namespaced <plugin>:<name> (runtime-verified, #18)

# `permissions.deny` is NOT a rice-owned leaf (RICE-BOOTSTRAP-045/-046): it
# is a list a repo may already be using, so rice owns exactly ONE occurrence
# of each rule below -- a multiset, not a set. A repo that already denies
# `Bash(git rebase*)` itself keeps its own copy: a fresh install adds rice's
# alongside it (1 + 1 = 2), and uninstall takes back exactly one, leaving
# the project's. `permissions.allow`, `permissions.ask` and every other
# permissions key stay project-owned.
DENY_PATH = "permissions.deny"

OWNER_DEFINED_KEYS = {
    "env.RICE_SESSION_ARCHIVE",
}

# RICE-BOOTSTRAP-023: the output style is the OWNER's choice, made once at
# bootstrap (rice:老弟 recommended / Claude default / an existing style / a
# custom project style). Absent key == Claude's native default -- never a
# placeholder value. The verifier checks that whatever is configured
# actually resolves to an existing style (scripts/output_styles.py);
# reconcile never writes or reasserts it, in any mode.
OWNER_CHOICE_KEYS = {
    "outputStyle",
}

# Destructive / history-rewriting git operations. Bash rules match the whole
# command text; `*` stands in for any text; deny applies if ANY subcommand
# of a compound command matches (docs: permissions -> Bash rules).
_DESTRUCTIVE_DENY = [
    # force push: --force / --force-with-lease, before or after other args
    "Bash(git push --force*)",
    "Bash(git push * --force*)",
    # -f, before or after other args, at the end or not
    "Bash(git push -f*)",
    "Bash(git push * -f)",
    "Bash(git push * -f *)",
    # +refspec form (git push origin +main, git push origin +HEAD:main)
    "Bash(git push +*)",
    "Bash(git push * +*)",
    # remote branch deletion
    "Bash(git push --delete *)",
    "Bash(git push * --delete *)",
    "Bash(git push -d *)",
    "Bash(git push * -d *)",
    "Bash(git reset --hard*)",
    "Bash(git rebase*)",
    "Bash(git commit --amend*)",
    "Bash(git filter-branch*)",
    "Bash(git filter-repo*)",
    "Bash(git reflog expire*)",
    "Bash(git clean -f*)",
]

# Branch creation. `Bash(git branch *)` also matches the bare `git branch`
# (trailing ` *` rule), so the entire subcommand is off-limits, listing
# included -- accepted trade-off for a parser-free hard rule.
_BRANCH_CREATION_DENY = [
    "Bash(git branch *)",
    "Bash(git checkout -b *)",
    "Bash(git checkout -B *)",
    "Bash(git checkout --orphan *)",
    "Bash(git switch -c *)",
    "Bash(git switch -C *)",
    "Bash(git switch --create *)",
    "Bash(git worktree add *)",
    "Bash(git stash branch *)",
]

_MCP_DENY = [
    "mcp__github__create_branch",
]

# The exact deny entries this build owns. Entries an older build shipped that
# are no longer listed here are indistinguishable from a project's own rules,
# so they are treated as project-owned: never re-added, never removed.
RICE_DENY_ENTRIES = tuple(_DESTRUCTIVE_DENY + _BRANCH_CREATION_DENY + _MCP_DENY)


def desired_settings(archive_default: bool, output_style: str | None = RICE_STYLE) -> dict:
    """The full desired-state settings.json content this repo should carry:
    this release's archive default and the OWNER's output-style choice
    (None = Claude default: the key is simply absent). Never the marketplace
    or the plugin (RICE-FIX-001) -- rice's own install is user scope only,
    and no repo-level desired state may declare it.

    `archive_default` is what THIS release ships as the default; bootstrap
    passes True/False for it depending on whether the OWNER asked to
    override it. Once written, `RICE_SESSION_ARCHIVE` and `outputStyle`
    are the OWNER's -- ordinary reconcile never reasserts them."""
    settings = {
        "env": {
            "RICE_SESSION_ARCHIVE": "1" if archive_default else "0",
        },
        "permissions": {
            "deny": list(RICE_DENY_ENTRIES),
        },
    }
    if output_style is not None:
        settings["outputStyle"] = output_style
    return settings
