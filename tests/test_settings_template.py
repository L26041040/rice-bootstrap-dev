"""Tests for scripts/settings_template.py (ticket #15).

Run: python3 -m unittest tests.test_settings_template -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from settings_template import (  # noqa: E402
    desired_settings,
    RICE_DEFINED_KEYS,
    OWNER_DEFINED_KEYS,
)

MARKETPLACE_NAME = "rice-bootstrap"
PLUGIN_KEY = "rice@rice-bootstrap"


class TestDesiredSettings(unittest.TestCase):
    def test_marketplace_and_plugin_declaration(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        marketplace = s["extraKnownMarketplaces"][MARKETPLACE_NAME]
        self.assertEqual(marketplace["source"], "github")
        self.assertEqual(marketplace["repo"], "L26041040/rice-bootstrap")
        self.assertEqual(marketplace["ref"], "v0.1.0")
        self.assertTrue(s["enabledPlugins"][PLUGIN_KEY])

    def test_auto_memory_disabled(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertFalse(s["autoMemoryEnabled"])

    def test_output_style(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertEqual(s["outputStyle"], "老弟")

    def test_sonnet_default_no_force(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertEqual(s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", s["env"])

    def test_archive_toggle_respects_release_default_off(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "0")

    def test_archive_toggle_respects_release_default_on(self):
        s = desired_settings(ref="v0.1.0", archive_default=True)
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "1")

    def test_git_safety_deny_rules_present(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        deny = s["permissions"]["deny"]
        # destructive operations
        for pattern in [
            "Bash(git push * --force*)",
            "Bash(git push --force*)",
            "Bash(git push * -f *)",
            "Bash(git reset --hard*)",
            "Bash(git rebase*)",
            "Bash(git commit --amend*)",
            "Bash(git branch -D*)",
            "Bash(git branch --delete --force*)",
            "Bash(git filter-branch*)",
            "Bash(git filter-repo*)",
            "Bash(git reflog expire*)",
            "Bash(git clean -f*)",
        ]:
            self.assertIn(pattern, deny, f"missing destructive deny rule: {pattern}")
        # flag-based branch creation
        for pattern in [
            "Bash(git checkout -b *)",
            "Bash(git checkout -B *)",
            "Bash(git switch -c *)",
            "Bash(git switch -C *)",
            "Bash(git switch --create *)",
            "Bash(git checkout --orphan *)",
            "Bash(git worktree add -b *)",
            "Bash(git stash branch *)",
        ]:
            self.assertIn(pattern, deny, f"missing branch-creation deny rule: {pattern}")
        # MCP create_branch, denied by tool name
        self.assertIn("mcp__github__create_branch", deny)

    def test_git_safety_does_not_deny_allowed_forms(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        deny = s["permissions"]["deny"]
        # None of these should ever appear as a literal deny pattern.
        forbidden_denials = [
            "Bash(git checkout *)",
            "Bash(git switch *)",
            "Bash(git branch *)",
            "Bash(git commit *)",
            "Bash(git push *)",
        ]
        for pattern in forbidden_denials:
            self.assertNotIn(
                pattern, deny,
                f"{pattern} would over-block plain switch/commit/push/list",
            )

    def test_premium_model_ask_rule(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertIn("Agent(model:opus)", s["permissions"]["ask"])

    def test_key_classification_covers_every_key(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        top_level_keys = set(s.keys())
        classified = RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS
        self.assertTrue(
            top_level_keys.issubset(classified),
            f"unclassified keys: {top_level_keys - classified}",
        )

    def test_ref_and_archive_toggle_are_owner_defined(self):
        # These two must never be silently reasserted by ordinary reconcile.
        self.assertIn("extraKnownMarketplaces.ref", OWNER_DEFINED_KEYS)
        self.assertIn("env.RICE_SESSION_ARCHIVE", OWNER_DEFINED_KEYS)

    def test_snapshot_matches_fixed_expectation(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertEqual(
            sorted(s.keys()),
            ["autoMemoryEnabled", "enabledPlugins", "env", "extraKnownMarketplaces",
             "outputStyle", "permissions"],
        )


if __name__ == "__main__":
    unittest.main()
