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
    DENY_PATH,
    RICE_DENY_ENTRIES,
    OWNER_CHOICE_KEYS,
    REF_PATH,
    RICE_STYLE,
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

    def test_no_memory_override_and_no_premium_gate(self):
        # RICE-BOOTSTRAP-024: Claude's native auto memory is left alone, and
        # a subagent model other than the default is the OWNER saying so out
        # loud -- no ask rule, no approval mechanism of any kind.
        s = desired_settings(ref="v0.1.0", archive_default=False)
        self.assertNotIn("autoMemoryEnabled", s)
        self.assertNotIn("ask", s["permissions"])
        self.assertNotIn("permissions.ask", RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)
        self.assertNotIn("autoMemoryEnabled", RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)

    def test_output_style_is_owner_choice(self):
        # recommended default when the OWNER picks option 1
        self.assertEqual(desired_settings(ref="v0.1.0", archive_default=False)["outputStyle"], RICE_STYLE)
        # Claude default = the key is simply absent, never a placeholder value
        self.assertNotIn("outputStyle", desired_settings(ref="v0.1.0", archive_default=False, output_style=None))
        # any other choice is written verbatim
        self.assertEqual(desired_settings("v0.1.0", False, output_style="Explanatory")["outputStyle"], "Explanatory")
        self.assertIn("outputStyle", OWNER_CHOICE_KEYS)
        self.assertNotIn("outputStyle", RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS)

    def test_the_subagent_model_is_not_a_setting_rice_writes(self):
        """RICE-BOOTSTRAP-046: hard-enforcing Sonnet per repo was rice's call
        to make and it should not have been. It is a working rule in
        `.claude/rice.md` now, and nothing writes or removes the key."""
        s = desired_settings(ref="v0.1.0", archive_default=True)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", s.get("env", {}))
        self.assertNotIn("env.CLAUDE_CODE_SUBAGENT_MODEL",
                         RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)
        rice_md = (Path(__file__).resolve().parents[1] / "resources" / "rice.md").read_text(encoding="utf-8")
        self.assertIn("Sonnet", rice_md)
        # the only place the retired key may still be named is the
        # declaration that lets uninstall take it back from repos that were
        # bootstrapped when rice did write it
        import reconcile
        self.assertEqual(reconcile.RETIRED_LEAVES, {"env.CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"})
        for py in (Path(__file__).resolve().parents[1] / "scripts").glob("*.py"):
            code = "\n".join(l for l in py.read_text(encoding="utf-8").splitlines()
                             if not l.lstrip().startswith("#"))
            code = "".join(code.split('"""')[::2])  # drop docstrings
            if py.name == "reconcile.py":
                code = code.replace('RETIRED_LEAVES = {"env.CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}', "")
            self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", code, py.name)

    def test_deny_is_owned_entry_by_entry_not_as_a_leaf(self):
        """RICE-BOOTSTRAP-045: a repo may already be using permissions.deny,
        so rice owns the entries it adds and never the list itself."""
        self.assertNotIn(DENY_PATH, RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)
        self.assertEqual(list(desired_settings(ref="v0.1.0", archive_default=False)["permissions"]["deny"]),
                         list(RICE_DENY_ENTRIES))
        self.assertTrue(RICE_DENY_ENTRIES)

    def test_permissions_allow_is_project_owned(self):
        self.assertNotIn("permissions", RICE_DEFINED_KEYS)
        self.assertNotIn("permissions.allow", RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS)
        self.assertNotIn("permissions.ask", RICE_DEFINED_KEYS | OWNER_DEFINED_KEYS)


if __name__ == "__main__":
    unittest.main()
