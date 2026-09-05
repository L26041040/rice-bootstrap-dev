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
    REF_PATH,
)

MARKETPLACE_NAME = "rice-bootstrap"
PLUGIN_KEY = "rice@rice-bootstrap"


def _get(d, dotted):
    for part in dotted.split("."):
        d = d[part]
    return d


class TestDesiredSettings(unittest.TestCase):
    def test_marketplace_uses_official_nested_source_shape(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        entry = s["extraKnownMarketplaces"][MARKETPLACE_NAME]
        # docs: extraKnownMarketplaces.<name>.source is an OBJECT with its own
        # source/repo/ref fields -- not flattened onto the entry.
        self.assertEqual(set(entry.keys()), {"source"})
        self.assertEqual(entry["source"]["source"], "github")
        self.assertEqual(entry["source"]["repo"], "L26041040/rice-bootstrap")
        self.assertEqual(entry["source"]["ref"], "v0.1.0")
        self.assertTrue(s["enabledPlugins"][PLUGIN_KEY])

    def test_ref_path_constant_matches_real_shape(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertEqual(_get(s, REF_PATH), "v0.1.0")

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

    def test_destructive_git_deny_rules_present(self):
        deny = desired_settings(ref="v0.1.0", archive_default=False)["permissions"]["deny"]
        for pattern in [
            "Bash(git push --force*)",
            "Bash(git push * --force*)",
            "Bash(git push -f*)",
            "Bash(git push * -f)",
            "Bash(git push * -f *)",
            "Bash(git push +*)",
            "Bash(git push * +*)",
            "Bash(git push --delete *)",
            "Bash(git push * --delete *)",
            "Bash(git reset --hard*)",
            "Bash(git rebase*)",
            "Bash(git commit --amend*)",
            "Bash(git filter-branch*)",
            "Bash(git filter-repo*)",
            "Bash(git reflog expire*)",
            "Bash(git clean -f*)",
        ]:
            self.assertIn(pattern, deny, f"missing destructive deny rule: {pattern}")

    def test_branch_creation_is_permission_only_whole_class(self):
        deny = desired_settings(ref="v0.1.0", archive_default=False)["permissions"]["deny"]
        # RICE-BOOTSTRAP-019: the whole `git branch` subcommand is denied
        # (also matches the bare command), replacing #17's hook entirely.
        self.assertIn("Bash(git branch *)", deny)
        for pattern in [
            "Bash(git checkout -b *)",
            "Bash(git checkout -B *)",
            "Bash(git checkout --orphan *)",
            "Bash(git switch -c *)",
            "Bash(git switch -C *)",
            "Bash(git switch --create *)",
            "Bash(git worktree add *)",
            "Bash(git stash branch *)",
        ]:
            self.assertIn(pattern, deny, f"missing branch-creation deny rule: {pattern}")
        self.assertIn("mcp__github__create_branch", deny)

    def test_no_hook_based_git_safety_remains(self):
        hooks_json = Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"
        self.assertNotIn("git_safety_hook", hooks_json.read_text(encoding="utf-8"))
        self.assertFalse((Path(__file__).resolve().parents[1] / "scripts" / "git_safety_hook.py").exists())

    def test_git_safety_does_not_deny_ordinary_work(self):
        deny = desired_settings(ref="v0.1.0", archive_default=False)["permissions"]["deny"]
        for pattern in [
            "Bash(git checkout *)",
            "Bash(git switch *)",
            "Bash(git commit *)",
            "Bash(git push *)",
            "Bash(git status *)",
            "Bash(git rev-parse *)",
            "Bash(git *)",
        ]:
            self.assertNotIn(pattern, deny, f"{pattern} would over-block ordinary work")

    def test_premium_model_ask_rule(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertIn("Agent(model:opus)", s["permissions"]["ask"])

    def test_every_classified_path_exists_in_desired_settings(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        for dotted in RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS:
            _get(s, dotted)  # KeyError = a classified path that doesn't exist

    def test_every_leaf_in_desired_settings_is_classified(self):
        s = desired_settings(ref="v0.1.0", archive_default=False)
        classified = RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS

        def leaves(d, prefix=""):
            for k, v in d.items():
                path = f"{prefix}{k}"
                if isinstance(v, dict) and path not in classified:
                    yield from leaves(v, path + ".")
                else:
                    yield path

        unclassified = [p for p in leaves(s) if p not in classified]
        self.assertEqual(unclassified, [])

    def test_ref_and_archive_toggle_are_owner_defined(self):
        self.assertIn(REF_PATH, OWNER_DEFINED_KEYS)
        self.assertIn("env.RICE_SESSION_ARCHIVE", OWNER_DEFINED_KEYS)
        self.assertTrue(RICE_DEFINED_KEYS.isdisjoint(OWNER_DEFINED_KEYS))

    def test_permissions_allow_is_project_owned(self):
        self.assertNotIn("permissions", RICE_DEFINED_KEYS)
        self.assertNotIn("permissions.allow", RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS)


if __name__ == "__main__":
    unittest.main()
