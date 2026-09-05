"""Ticket #15: the rice-owned desired-state template for a target repo's
.claude/settings.json.

This module defines DATA (what the settings should look like for the
current release), not the reconcile logic that writes/compares it -- that
lives in reconcile.py (ticket #23).

Every top-level settings.json key rice manages must be classified as
either rice-defined (kept in sync on every reconcile pass) or
owner-defined (written once at bootstrap, never reasserted -- see #13's
Ownership model). Sub-keys are addressed with dotted paths.
"""

MARKETPLACE_NAME = "rice-bootstrap"
MARKETPLACE_REPO = "L26041040/rice-bootstrap"
PLUGIN_KEY = "rice@rice-bootstrap"

RICE_DEFINED_KEYS = {
    "extraKnownMarketplaces",
    "enabledPlugins",
    "autoMemoryEnabled",
    "outputStyle",
    "env",  # container; see env.CLAUDE_CODE_SUBAGENT_MODEL sub-key below
    "env.CLAUDE_CODE_SUBAGENT_MODEL",
    "permissions",
}

OWNER_DEFINED_KEYS = {
    "extraKnownMarketplaces.ref",
    "env.RICE_SESSION_ARCHIVE",
}

# Destructive / history-rewriting git operations -- categorical, no
# argument-semantic ambiguity, safe to deny outright.
_DESTRUCTIVE_DENY = [
    "Bash(git push * --force*)",
    "Bash(git push --force*)",
    "Bash(git push * -f *)",
    "Bash(git push -f*)",
    "Bash(git reset --hard*)",
    "Bash(git rebase*)",
    "Bash(git commit --amend*)",
    "Bash(git branch -D*)",
    "Bash(git branch --delete --force*)",
    "Bash(git filter-branch*)",
    "Bash(git filter-repo*)",
    "Bash(git reflog expire*)",
    "Bash(git clean -f*)",
]

# Branch creation that carries a distinguishing flag -- the flag alone is
# the categorical signal, so this is also safe as a plain permission rule.
# (Bare "git branch <name>" is NOT here -- that's the one case the narrow
# PreToolUse hook in ticket #17 handles, because it's a positional
# argument, not a flag, that decides creation vs. listing.)
_BRANCH_CREATION_DENY = [
    "Bash(git checkout -b *)",
    "Bash(git checkout -B *)",
    "Bash(git switch -c *)",
    "Bash(git switch -C *)",
    "Bash(git switch --create *)",
    "Bash(git checkout --orphan *)",
    "Bash(git worktree add -b *)",
    "Bash(git stash branch *)",
]

_MCP_DENY = [
    "mcp__github__create_branch",
]

_PREMIUM_MODEL_ASK = [
    "Agent(model:opus)",
]


def desired_settings(ref: str, archive_default: bool) -> dict:
    """The full desired-state settings.json content for the given pinned
    ref and this release's archive default.

    `archive_default` is what THIS release ships as the default; bootstrap
    passes True/False for it depending on whether the OWNER asked to
    override it. Once written, `RICE_SESSION_ARCHIVE` is owner-defined --
    ordinary reconcile never reasserts it (see #13 Ownership model)."""
    return {
        "extraKnownMarketplaces": {
            MARKETPLACE_NAME: {
                "source": "github",
                "repo": MARKETPLACE_REPO,
                "ref": ref,
            }
        },
        "enabledPlugins": {
            PLUGIN_KEY: True,
        },
        "autoMemoryEnabled": False,
        "outputStyle": "老弟",
        "env": {
            "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet",
            "RICE_SESSION_ARCHIVE": "1" if archive_default else "0",
        },
        "permissions": {
            "deny": _DESTRUCTIVE_DENY + _BRANCH_CREATION_DENY + _MCP_DENY,
            "ask": list(_PREMIUM_MODEL_ASK),
        },
    }
