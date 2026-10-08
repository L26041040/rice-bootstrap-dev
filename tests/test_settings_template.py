"""Tests for scripts/settings_template.py (ticket #15, RICE-FIX-001).

Run: python3 -m unittest tests.test_settings_template -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_public  # noqa: E402

from settings_template import (  # noqa: E402
    desired_settings,
    OWNER_DEFINED_KEYS,
    DENY_PATH,
    RICE_DENY_ENTRIES,
    OWNER_CHOICE_KEYS,
    MARKETPLACE_NAME,
    MARKETPLACE_REPO,
    PLUGIN_KEY,
    RICE_STYLE,
)


class TestDesiredSettings(unittest.TestCase):
    def test_no_project_scope_plugin_declaration(self):
        """RICE-FIX-001: rice is distributed at user scope only (the
        Environment's setup script). The repo desired-state template never
        declares the marketplace or the plugin at project scope -- doing so
        used to create a second, unsupported install that `plugin:scope`
        already fails on."""
        s = desired_settings(archive_default=False)
        self.assertNotIn("extraKnownMarketplaces", s)
        self.assertNotIn("enabledPlugins", s)
        # the identity constants remain -- they still name rice's own
        # marketplace/plugin for the user-scope install instructions and the
        # migration that takes a 0.1.3-era declaration back out
        self.assertEqual(MARKETPLACE_NAME, "rice-bootstrap")
        self.assertEqual(MARKETPLACE_REPO, "L26041040/rice-bootstrap")
        self.assertEqual(PLUGIN_KEY, "rice@rice-bootstrap")

    def test_no_memory_override_and_no_premium_gate(self):
        # RICE-BOOTSTRAP-024: Claude's native auto memory is left alone, and
        # a subagent model other than the default is the OWNER saying so out
        # loud -- no ask rule, no approval mechanism of any kind.
        s = desired_settings(archive_default=False)
        self.assertNotIn("autoMemoryEnabled", s)
        self.assertNotIn("ask", s["permissions"])
        self.assertNotIn("permissions.ask", OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)
        self.assertNotIn("autoMemoryEnabled", OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)

    def test_output_style_is_owner_choice(self):
        # recommended default when the OWNER picks option 1
        self.assertEqual(desired_settings(archive_default=False)["outputStyle"], RICE_STYLE)
        # Claude default = the key is simply absent, never a placeholder value
        self.assertNotIn("outputStyle", desired_settings(archive_default=False, output_style=None))
        # any other choice is written verbatim
        self.assertEqual(desired_settings(False, output_style="Explanatory")["outputStyle"], "Explanatory")
        self.assertIn("outputStyle", OWNER_CHOICE_KEYS)
        self.assertNotIn("outputStyle", OWNER_DEFINED_KEYS)

    def test_the_subagent_model_is_not_a_setting_rice_writes(self):
        """RICE-BOOTSTRAP-046: hard-enforcing Sonnet per repo was rice's call
        to make and it should not have been. It is a working rule in
        `.claude/rice.md` now, and nothing writes or removes the key."""
        s = desired_settings(archive_default=True)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", s.get("env", {}))
        self.assertNotIn("env.CLAUDE_CODE_SUBAGENT_MODEL", OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)
        rice_md = (Path(__file__).resolve().parents[1] / "resources" / "rice.md").read_text(encoding="utf-8")
        self.assertIn("Sonnet", rice_md)
        # ... nor one rice takes back (RICE-OWNERSHIP-FIX-001): no shipped
        # script names the key in code -- it is the project's, whatever its
        # value. (Private release tooling may, to put it in a fixture.)
        for py in (Path(__file__).resolve().parents[1] / "scripts").glob("*.py"):
            if export_public.is_forbidden(f"scripts/{py.name}"):
                continue
            code = "\n".join(l for l in py.read_text(encoding="utf-8").splitlines()
                             if not l.lstrip().startswith("#"))
            code = "".join(code.split('"""')[::2])  # drop docstrings
            self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", code, py.name)

    def test_deny_is_owned_entry_by_entry_not_as_a_leaf(self):
        """RICE-BOOTSTRAP-045: a repo may already be using permissions.deny,
        so rice owns the entries it adds and never the list itself."""
        self.assertNotIn(DENY_PATH, OWNER_DEFINED_KEYS | OWNER_CHOICE_KEYS)
        self.assertEqual(list(desired_settings(archive_default=False)["permissions"]["deny"]),
                         list(RICE_DENY_ENTRIES))
        self.assertTrue(RICE_DENY_ENTRIES)

    def test_permissions_allow_is_project_owned(self):
        self.assertNotIn("permissions.allow", OWNER_DEFINED_KEYS)
        self.assertNotIn("permissions.ask", OWNER_DEFINED_KEYS)


if __name__ == "__main__":
    unittest.main()
