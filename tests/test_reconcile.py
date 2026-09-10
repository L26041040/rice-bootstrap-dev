"""Tests for scripts/reconcile.py (ticket #23).

Run: python3 -m unittest tests.test_reconcile -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reconcile  # noqa: E402
import settings_template  # noqa: E402

REF = "v1.0.0"
REF_PATH = settings_template.REF_PATH


def _states(items):
    return {i.name: i.state for i in items}


def _write_settings(repo_root: Path, settings: dict) -> Path:
    p = repo_root / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings), encoding="utf-8")
    return p


def _read_settings(repo_root: Path) -> dict:
    return json.loads((repo_root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _fully_correct_settings() -> dict:
    return settings_template.desired_settings(REF, True)


def _make_correct_repo(repo_root: Path) -> None:
    """A repo where every rice-owned item is already exactly correct."""
    _write_settings(repo_root, _fully_correct_settings())
    rice_md_dest = repo_root / ".claude" / "rice.md"
    rice_md_dest.write_text(reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    (repo_root / "CLAUDE.md").write_text(f"# Some project\n\n{reconcile.IMPORT_LINE}\n", encoding="utf-8")
    (repo_root / "AGENTS.md").write_text(reconcile.AGENTS_POINTER, encoding="utf-8")


def _ok_rtk_probe(cmd):
    class R:
        returncode = 0
        stdout = "rtk 0.46.0\n"
    return R()


def _missing_rtk_probe(cmd):
    raise FileNotFoundError("rtk not found")


def _erroring_rtk_probe(cmd):
    raise PermissionError("cannot execute")


def _installed_plugin_probe(cmd):
    return _Result(0, '[{"id":"rice@rice-bootstrap","version":"0.1.1","scope":"user","enabled":true}]')


class ReconcileTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        # keep tests hermetic: never shell out to the real `claude` CLI
        self._real_plugin_probe = reconcile._default_plugin_probe
        reconcile._default_plugin_probe = _installed_plugin_probe
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.repo_root / "cfg")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        reconcile._default_plugin_probe = self._real_plugin_probe
        self._tmp.cleanup()


class TestSettingsKeyStates(ReconcileTestBase):
    def test_rice_defined_key_missing_is_created_on_apply_and_fails_check(self):
        settings = _fully_correct_settings()
        del settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"]
        _write_settings(self.repo_root, settings)

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "created")
        self.assertEqual(_read_settings(self.repo_root)["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")

    def test_rice_defined_key_match_is_noop(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "PASS")
        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "noop")

    def test_rice_defined_key_drift_without_rice_update_is_conflict_and_untouched(self):
        settings = _fully_correct_settings()
        settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "opus"
        _write_settings(self.repo_root, settings)

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, is_rice_update=False, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "conflict")
        self.assertEqual(_read_settings(self.repo_root)["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "opus")

    def test_rice_defined_key_drift_with_rice_update_is_overwritten(self):
        settings = _fully_correct_settings()
        settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "opus"
        _write_settings(self.repo_root, settings)

        apply_items = reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"], "updated")
        self.assertEqual(_read_settings(self.repo_root)["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")

    def test_output_style_is_owner_choice_never_written_or_reasserted(self):
        # OWNER chose a custom project style -> apply (even rice-update) leaves it alone
        settings = _fully_correct_settings(); settings["outputStyle"] = "my-style"
        _write_settings(self.repo_root, settings)
        (self.repo_root / ".claude" / "output-styles").mkdir(parents=True)
        (self.repo_root / ".claude" / "output-styles" / "my.md").write_text("---\nname: my-style\n---\nbody\n")
        for is_update in (False, True):
            reconcile.apply(self.repo_root, is_rice_update=is_update, rtk_probe=_ok_rtk_probe)
            self.assertEqual(_read_settings(self.repo_root)["outputStyle"], "my-style")
        # OWNER chose Claude default -> key absent stays absent
        settings = _fully_correct_settings(); del settings["outputStyle"]
        _write_settings(self.repo_root, settings)
        reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        self.assertNotIn("outputStyle", _read_settings(self.repo_root))


class TestOwnerDefinedKeys(ReconcileTestBase):
    def test_owner_defined_key_present_is_pass_and_never_written(self):
        settings = _fully_correct_settings()
        _write_settings(self.repo_root, settings)
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.RICE_SESSION_ARCHIVE"], "PASS")

        before = _read_settings(self.repo_root)["env"]["RICE_SESSION_ARCHIVE"]
        reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        after = _read_settings(self.repo_root)["env"]["RICE_SESSION_ARCHIVE"]
        self.assertEqual(before, after)

    def test_owner_defined_key_missing_is_fail_and_never_created(self):
        settings = _fully_correct_settings()
        del settings["env"]["RICE_SESSION_ARCHIVE"]
        _write_settings(self.repo_root, settings)

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.RICE_SESSION_ARCHIVE"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:env.RICE_SESSION_ARCHIVE"], "FAIL")
        self.assertNotIn("RICE_SESSION_ARCHIVE", _read_settings(self.repo_root)["env"])

    def test_ref_never_written_even_under_rice_update_with_newer_release_available(self):
        settings = _fully_correct_settings()
        _write_settings(self.repo_root, settings)
        # Even though the caller is in "rice-update" mode (as if a newer
        # release exists), apply() has no code path that writes `ref` --
        # it only ever reads it to know which version's desired state to
        # converge everything else against.
        reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        current = _read_settings(self.repo_root)
        self.assertEqual(
            current["extraKnownMarketplaces"][settings_template.MARKETPLACE_NAME]["source"]["ref"], REF
        )

    def test_ref_missing_entirely_reports_fail_and_apply_writes_nothing(self):
        settings = _fully_correct_settings()
        del settings["extraKnownMarketplaces"][settings_template.MARKETPLACE_NAME]["source"]["ref"]
        _write_settings(self.repo_root, settings)
        before = (self.repo_root / ".claude" / "settings.json").read_text()

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)[f"settings:{REF_PATH}"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)[f"settings:{REF_PATH}"], "FAIL")
        after = (self.repo_root / ".claude" / "settings.json").read_text()
        self.assertEqual(before, after)


class TestRiceMdWholeFile(ReconcileTestBase):
    def test_missing_is_created_on_apply_and_fails_check(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["rice.md"], "created")
        dest = self.repo_root / ".claude" / "rice.md"
        self.assertEqual(dest.read_text(), reconcile.RICE_MD_RESOURCE.read_text())

    def test_matching_content_is_noop(self):
        _make_correct_repo(self.repo_root)
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "PASS")
        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["rice.md"], "noop")

    def test_hand_edited_same_version_is_conflict_and_untouched(self):
        _make_correct_repo(self.repo_root)
        dest = self.repo_root / ".claude" / "rice.md"
        dest.write_text("# hand-edited\n", encoding="utf-8")

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, is_rice_update=False, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["rice.md"], "conflict")
        self.assertEqual(dest.read_text(), "# hand-edited\n")

    def test_drift_under_rice_update_is_overwritten(self):
        _make_correct_repo(self.repo_root)
        dest = self.repo_root / ".claude" / "rice.md"
        dest.write_text("# stale content from an older version\n", encoding="utf-8")

        apply_items = reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["rice.md"], "updated")
        self.assertEqual(dest.read_text(), reconcile.RICE_MD_RESOURCE.read_text())


class TestClaudeMdImportLine(ReconcileTestBase):
    def test_file_missing_creates_minimal_file_with_only_import_line(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "created")
        content = (self.repo_root / "CLAUDE.md").read_text()
        self.assertEqual(content.strip(), reconcile.IMPORT_LINE)

    def test_file_present_without_line_appends_without_touching_rest(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        existing = "# My Project\n\nSome project-owned content.\n"
        (self.repo_root / "CLAUDE.md").write_text(existing, encoding="utf-8")

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "created")
        content = (self.repo_root / "CLAUDE.md").read_text()
        self.assertIn(existing.rstrip("\n"), content)
        self.assertIn(reconcile.IMPORT_LINE, content.splitlines())

    def test_file_present_with_line_is_noop(self):
        _make_correct_repo(self.repo_root)
        original = (self.repo_root / "CLAUDE.md").read_text()

        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "PASS")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "noop")
        self.assertEqual((self.repo_root / "CLAUDE.md").read_text(), original)


class TestAgentsMdPointer(ReconcileTestBase):
    """RICE-BOOTSTRAP-042: AGENTS.md is the cross-agent entry point. It holds
    one rice-owned pointer block and never a copy of the rules; the rules
    live only in .claude/rice.md."""

    def test_fresh_bootstrap_creates_agents_md_with_only_the_pointer(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["AGENTS.md:rice-pointer"], "created")
        self.assertEqual((self.repo_root / "AGENTS.md").read_text(encoding="utf-8"), reconcile.AGENTS_POINTER)
        self.assertEqual(_states(reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "PASS")
        self.assertEqual(_states(reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "noop")

    def test_one_canonical_policy_source(self):
        """CLAUDE.md imports the rules, AGENTS.md points at them, neither
        restates them."""
        _write_settings(self.repo_root, _fully_correct_settings())
        (self.repo_root / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
        (self.repo_root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        rules = (self.repo_root / ".claude" / "rice.md").read_text(encoding="utf-8")
        self.assertEqual(rules, reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))
        claude_md = (self.repo_root / "CLAUDE.md").read_text(encoding="utf-8")
        agents_md = (self.repo_root / "AGENTS.md").read_text(encoding="utf-8")
        for body_heading in ("## Control hierarchy", "## Conversation numbering", "## Questions to the OWNER"):
            self.assertIn(body_heading, rules)
            self.assertNotIn(body_heading, claude_md)
            self.assertNotIn(body_heading, agents_md)
        self.assertIn(reconcile.IMPORT_LINE, claude_md)          # Claude: a real import
        self.assertNotIn(reconcile.IMPORT_LINE, agents_md)       # other agents: no @import semantics
        self.assertIn("`.claude/rice.md`", agents_md)             # ... so a pointer instruction
        self.assertEqual(agents_md.count(reconcile.AGENTS_BEGIN), 1)

    def test_project_owned_content_survives_bootstrap_and_update(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        project = "# Agents\n\nProject-owned guidance.\n\n## Build\n\nmake\n"
        (self.repo_root / "AGENTS.md").write_text(project, encoding="utf-8")
        for mode in (False, True, True):
            reconcile.apply(self.repo_root, is_rice_update=mode, rtk_probe=_ok_rtk_probe)
            text = (self.repo_root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(project), text)
            self.assertEqual(text.count(reconcile.AGENTS_BEGIN), 1)
            self.assertTrue(text.endswith(reconcile.AGENTS_POINTER))

    def test_hand_edited_block_is_conflict_until_update_or_repair_converges_it(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        project = "# Agents\n\nkeep me\n"
        (self.repo_root / "AGENTS.md").write_text(
            project + f"\n{reconcile.AGENTS_BEGIN}\nedited by hand\n{reconcile.AGENTS_END}\n", encoding="utf-8")
        self.assertEqual(_states(reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "FAIL")
        self.assertEqual(_states(reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "conflict")
        self.assertIn("edited by hand", (self.repo_root / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertEqual(_states(reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "updated")
        text = (self.repo_root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text, project + "\n" + reconcile.AGENTS_POINTER)


class TestMattAndRtkPresence(ReconcileTestBase):
    def _matt_repo(self, *, heading=True, issue=True, domain=True, triage_heading=False, triage_doc=False):
        import shutil
        _make_correct_repo(self.repo_root)  # rewrites CLAUDE.md fresh
        shutil.rmtree(self.repo_root / "docs", ignore_errors=True)
        claude_md = self.repo_root / "CLAUDE.md"
        extra = ""
        if heading:
            extra += "\n## Agent skills\n\n### Issue tracker\n\nsee docs/agents/issue-tracker.md\n"
        if triage_heading:
            extra += "\n### Triage labels\n\nsee docs/agents/triage-labels.md\n"
        claude_md.write_text(claude_md.read_text() + extra, encoding="utf-8")
        d = self.repo_root / "docs" / "agents"; d.mkdir(parents=True, exist_ok=True)
        if issue: (d / "issue-tracker.md").write_text("x", encoding="utf-8")
        if domain: (d / "domain.md").write_text("x", encoding="utf-8")
        if triage_doc: (d / "triage-labels.md").write_text("x", encoding="utf-8")

    def test_matt_artifacts_present_exact_observed_shape(self):
        self._matt_repo()
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["matt:artifacts"], "PASS")

    def test_matt_each_required_artifact_alone_fails(self):
        for kw in ({"heading": False}, {"issue": False}, {"domain": False}):
            self._matt_repo(**kw)
            items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
            self.assertEqual(_states(items)["matt:artifacts"], "FAIL", kw)

    def test_matt_triage_doc_required_only_when_triage_capability_present(self):
        # triage heading present in CLAUDE.md (Matt writes it when triage is installed) -> doc required
        self._matt_repo(triage_heading=True, triage_doc=False)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["matt:artifacts"], "FAIL")
        self._matt_repo(triage_heading=True, triage_doc=True)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["matt:artifacts"], "PASS")
        # installed mattpocock plugin that ships a triage skill -> doc required even without the heading
        plugin_root = self.repo_root / "mp"; (plugin_root / "skills" / "engineering" / "triage").mkdir(parents=True)
        (plugin_root / "skills" / "engineering" / "triage" / "SKILL.md").write_text("---\nname: triage\n---\n")
        probe = lambda cmd: _Result(0, json.dumps([
            {"id": "rice@rice-bootstrap", "version": "0.1.1", "scope": "user", "enabled": True},
            {"id": "mattpocock-skills@mattpocock", "version": "1.2.3", "installPath": str(plugin_root)}]))
        self._matt_repo(triage_heading=False, triage_doc=False)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["matt:artifacts"], "FAIL")
        self._matt_repo(triage_heading=False, triage_doc=True)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["matt:artifacts"], "PASS")

    def test_matt_artifacts_missing(self):
        _make_correct_repo(self.repo_root)
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["matt:artifacts"], "FAIL")

    def test_matt_artifacts_never_written_by_apply(self):
        _make_correct_repo(self.repo_root)
        reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertFalse((self.repo_root / "docs").exists())

    def test_rtk_present(self):
        _make_correct_repo(self.repo_root)
        check_items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "PASS")

    def test_rtk_missing(self):
        _make_correct_repo(self.repo_root)
        check_items = reconcile.check(self.repo_root, rtk_probe=_missing_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "FAIL")

    def test_rtk_probe_error_is_not_verified(self):
        _make_correct_repo(self.repo_root)
        check_items = reconcile.check(self.repo_root, rtk_probe=_erroring_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "NOT_VERIFIED")


class TestIdempotentApplyAndSummary(ReconcileTestBase):
    def test_apply_on_already_correct_repo_is_pure_noop(self):
        _make_correct_repo(self.repo_root)
        settings_path = self.repo_root / ".claude" / "settings.json"
        before_bytes = settings_path.read_bytes()
        before_mtime = settings_path.stat().st_mtime_ns

        items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)

        after_bytes = settings_path.read_bytes()
        self.assertEqual(before_bytes, after_bytes)
        self.assertEqual(before_mtime, settings_path.stat().st_mtime_ns)
        # matt/rtk are REQUIRED Gate B items, but apply never acts on them
        # (Matt / the environment provide them), so a FAIL there doesn't mean
        # apply left something undone.
        rice_owned = [i for i in items if not i.name.startswith(("matt:", "rtk"))]
        actionable = [i for i in rice_owned if i.state in ("created", "updated", "conflict", "FAIL")]
        self.assertEqual(actionable, [], actionable)

    def test_check_returns_items_with_known_states_and_no_verdict(self):
        _make_correct_repo(self.repo_root)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        for i in items:
            self.assertIn(i.state, ("PASS", "FAIL", "NOT_VERIFIED"))
            self.assertNotIn("VERIFIED rice", i.detail)


if __name__ == "__main__":
    unittest.main()


class TestProjectOwnedSettingsPreserved(ReconcileTestBase):
    def test_project_owned_keys_survive_apply_in_every_mode(self):
        settings = _fully_correct_settings()
        settings["permissions"]["allow"] = ["Bash(npm test *)"]
        settings["enabledPlugins"]["other-plugin@other-marketplace"] = True
        settings["extraKnownMarketplaces"]["other-marketplace"] = {
            "source": {"source": "github", "repo": "someone/else"}
        }
        settings["env"]["MY_OWN_VAR"] = "keep-me"
        settings["someOtherKey"] = {"nested": True}
        # force a rice-defined drift so apply actually writes the file
        settings["outputStyle"] = "drifted"
        settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "opus"
        settings["permissions"]["ask"] = ["Bash(rm *)"]  # project-owned now (-024)
        _write_settings(self.repo_root, settings)

        reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        after = _read_settings(self.repo_root)
        self.assertEqual(after["outputStyle"], "drifted")  # OWNER choice: untouched by apply
        self.assertEqual(after["permissions"]["allow"], ["Bash(npm test *)"])
        self.assertEqual(after["permissions"]["ask"], ["Bash(rm *)"])
        self.assertEqual(after["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")  # rice-defined: converged
        self.assertTrue(after["enabledPlugins"]["other-plugin@other-marketplace"])
        self.assertIn("other-marketplace", after["extraKnownMarketplaces"])
        self.assertEqual(after["env"]["MY_OWN_VAR"], "keep-me")
        self.assertEqual(after["someOtherKey"], {"nested": True})


class TestPluginInstalledPresence(ReconcileTestBase):
    """#18 runtime fact: declaring the marketplace/plugin in project settings
    does not install it (and Cloud never trusts the folder, so it is ignored
    there entirely). `check` therefore reports, presence-only, whether the
    rice plugin is actually installed on this machine, via the official CLI."""

    def test_installed_is_pass_with_version_in_detail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"rice@rice-bootstrap","version":"0.1.1","scope":"user","enabled":true}]')
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        st = {i.name: i for i in items}
        self.assertEqual(st["plugin:installed"].state, "PASS")
        self.assertIn("0.1.1", st["plugin:installed"].detail)

    def test_not_installed_is_fail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"other@mp","version":"1.0.0"}]')
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["plugin:installed"], "FAIL")

    def test_cli_unavailable_is_not_verified(self):
        _make_correct_repo(self.repo_root)
        def probe(cmd):
            raise FileNotFoundError("claude")
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["plugin:installed"], "NOT_VERIFIED")

    def test_apply_never_installs(self):
        _make_correct_repo(self.repo_root)
        calls = []
        def probe(cmd):
            calls.append(cmd)
            return _Result(0, '[]')
        reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertTrue(all(c[:3] == ["claude", "plugin", "list"] for c in calls), calls)


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


class TestRubricKinds(ReconcileTestBase):
    """Every item carries a kind and the v1 list is covered. Verdicts are
    NOT reconcile's business (RICE-BOOTSTRAP-021) -- see tests/test_verify.py."""

    def test_every_item_has_a_kind_and_v1_coverage(self):
        _make_correct_repo(self.repo_root)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        for i in items:
            self.assertIn(i.kind, (reconcile.EXISTS, reconcile.MATCHES, reconcile.BEHAVES), i)
        names = {i.name for i in items}
        for required in [
            "plugin:installed", "plugin:build", "output-style:bundled", "output-style:choice",
            "settings:permissions.deny",
            "settings:env.CLAUDE_CODE_SUBAGENT_MODEL", "rice.md", "CLAUDE.md:import-line",
            "AGENTS.md:rice-pointer",
            "archive-hook:exists", "archive-sync-hook:exists", "continuation-hook:exists",
            "git-deny:behaves", "matt:artifacts", "rtk",
        ]:
            self.assertIn(required, names)
        # archive:behaves is deliberately NOT part of the rubric
        # (RICE-BOOTSTRAP-032) -- it gates nothing, so it must not appear
        # in check()'s output (RICE-BOOTSTRAP-021's "no informational
        # escape hatch" invariant, see tests/test_verify.py).
        self.assertNotIn("archive:behaves", names)

    def test_behaves_items_are_not_verified_without_evidence(self):
        _make_correct_repo(self.repo_root)
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        st = {i.name: i for i in items}
        self.assertEqual(st["git-deny:behaves"].state, "NOT_VERIFIED")
        self.assertIn("git branch --show-current", st["git-deny:behaves"].detail)


def _git_r(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def _init_archive_repo(root: Path, remote: Path) -> None:
    _git_r(remote, "init", "-q", "--bare", "-b", "main")
    _git_r(root, "init", "-q", "-b", "main")
    _git_r(root, "config", "user.email", "test@example.com")
    _git_r(root, "config", "user.name", "Test")
    _git_r(root, "remote", "add", "origin", str(remote))
    (root / "README.md").write_text("init\n")
    _git_r(root, "add", "README.md")
    _git_r(root, "commit", "-q", "-m", "init")
    _git_r(root, "push", "-q", "-u", "origin", "main")


class TestArchiveBehavesDiagnostic(ReconcileTestBase):
    """_archive_behaves_item is a standalone diagnostic (RICE-BOOTSTRAP-032),
    not part of reconcile.check()'s rubric -- see TestRubricKinds above."""

    def setUp(self):
        super().setUp()
        self._remote_tmp = tempfile.TemporaryDirectory()
        _init_archive_repo(self.repo_root, Path(self._remote_tmp.name))

    def tearDown(self):
        self._remote_tmp.cleanup()
        super().tearDown()

    def test_pass_when_switched_off_regardless_of_git_state(self):
        settings = _fully_correct_settings(); settings["env"]["RICE_SESSION_ARCHIVE"] = "0"
        self.assertEqual(reconcile._archive_behaves_item(self.repo_root, settings).state, "PASS")

    def test_not_verified_when_nothing_produced_yet(self):
        settings = _fully_correct_settings()
        self.assertEqual(reconcile._archive_behaves_item(self.repo_root, settings).state, "NOT_VERIFIED")

    def test_never_pass_from_local_file_evidence_alone(self):
        """The exact false-PASS this diagnostic exists to close: a Rice
        archive commit sitting locally, unpushed, must never read PASS."""
        settings = _fully_correct_settings()
        sh = self.repo_root / "session-history"; sh.mkdir()
        (sh / "INDEX.md").write_text(
            "# Session Archive Index\n\n| Date | Title | Excerpt | Turns | File |\n|---|---|---|---|---|\n"
            "| 2026-09-05 | t | e | 4 | 2026-09-05_x.md |\n")
        (sh / "2026-09-05_x.md").write_text("# archive\n")
        _git_r(self.repo_root, "add", "session-history")
        _git_r(self.repo_root, "commit", "-q", "-m", f"session history: x\n\n{reconcile.archive_hook.RICE_ARCHIVE_TRAILER}")
        self.assertEqual(reconcile._archive_behaves_item(self.repo_root, settings).state, "NOT_VERIFIED")

    def test_pass_once_the_archive_commit_reaches_upstream(self):
        settings = _fully_correct_settings()
        sh = self.repo_root / "session-history"; sh.mkdir()
        (sh / "INDEX.md").write_text(
            "# Session Archive Index\n\n| Date | Title | Excerpt | Turns | File |\n|---|---|---|---|---|\n"
            "| 2026-09-05 | t | e | 4 | 2026-09-05_x.md |\n")
        (sh / "2026-09-05_x.md").write_text("# archive\n")
        _git_r(self.repo_root, "add", "session-history")
        _git_r(self.repo_root, "commit", "-q", "-m", f"session history: x\n\n{reconcile.archive_hook.RICE_ARCHIVE_TRAILER}")
        _git_r(self.repo_root, "push", "-q", "origin", "HEAD:main")
        self.assertEqual(reconcile._archive_behaves_item(self.repo_root, settings).state, "PASS")

    def test_plugin_build_mismatch_is_fail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"rice@rice-bootstrap","version":"0.0.1","scope":"user","enabled":true}]')
        items = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        st = {i.name: i.state for i in items}
        self.assertEqual(st["plugin:installed"], "PASS")
        self.assertEqual(st["plugin:build"], "FAIL")
