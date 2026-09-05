"""Tests for scripts/reconcile.py (ticket #23).

Run: python3 -m unittest tests.test_reconcile -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

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

    def tearDown(self):
        reconcile._default_plugin_probe = self._real_plugin_probe
        self._tmp.cleanup()


class TestSettingsKeyStates(ReconcileTestBase):
    def test_rice_defined_key_missing_is_created_on_apply_and_fails_check(self):
        settings = _fully_correct_settings()
        del settings["outputStyle"]
        _write_settings(self.repo_root, settings)

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:outputStyle"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:outputStyle"], "created")
        self.assertEqual(_read_settings(self.repo_root)["outputStyle"], "rice:老弟")

    def test_rice_defined_key_match_is_noop(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:outputStyle"], "PASS")
        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:outputStyle"], "noop")

    def test_rice_defined_key_drift_without_rice_update_is_conflict_and_untouched(self):
        settings = _fully_correct_settings()
        settings["outputStyle"] = "some-other-style"
        _write_settings(self.repo_root, settings)

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:outputStyle"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, is_rice_update=False, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:outputStyle"], "conflict")
        self.assertEqual(_read_settings(self.repo_root)["outputStyle"], "some-other-style")

    def test_rice_defined_key_drift_with_rice_update_is_overwritten(self):
        settings = _fully_correct_settings()
        settings["outputStyle"] = "some-other-style"
        _write_settings(self.repo_root, settings)

        apply_items = reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["settings:outputStyle"], "updated")
        self.assertEqual(_read_settings(self.repo_root)["outputStyle"], "rice:老弟")


class TestOwnerDefinedKeys(ReconcileTestBase):
    def test_owner_defined_key_present_is_pass_and_never_written(self):
        settings = _fully_correct_settings()
        _write_settings(self.repo_root, settings)
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.RICE_SESSION_ARCHIVE"], "PASS")

        before = _read_settings(self.repo_root)["env"]["RICE_SESSION_ARCHIVE"]
        reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        after = _read_settings(self.repo_root)["env"]["RICE_SESSION_ARCHIVE"]
        self.assertEqual(before, after)

    def test_owner_defined_key_missing_is_fail_and_never_created(self):
        settings = _fully_correct_settings()
        del settings["env"]["RICE_SESSION_ARCHIVE"]
        _write_settings(self.repo_root, settings)

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
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

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)[f"settings:{REF_PATH}"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)[f"settings:{REF_PATH}"], "FAIL")
        after = (self.repo_root / ".claude" / "settings.json").read_text()
        self.assertEqual(before, after)


class TestRiceMdWholeFile(ReconcileTestBase):
    def test_missing_is_created_on_apply_and_fails_check(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["rice.md"], "created")
        dest = self.repo_root / ".claude" / "rice.md"
        self.assertEqual(dest.read_text(), reconcile.RICE_MD_RESOURCE.read_text())

    def test_matching_content_is_noop(self):
        _make_correct_repo(self.repo_root)
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "PASS")
        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["rice.md"], "noop")

    def test_hand_edited_same_version_is_conflict_and_untouched(self):
        _make_correct_repo(self.repo_root)
        dest = self.repo_root / ".claude" / "rice.md"
        dest.write_text("# hand-edited\n", encoding="utf-8")

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
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
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "created")
        content = (self.repo_root / "CLAUDE.md").read_text()
        self.assertEqual(content.strip(), reconcile.IMPORT_LINE)

    def test_file_present_without_line_appends_without_touching_rest(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        existing = "# My Project\n\nSome project-owned content.\n"
        (self.repo_root / "CLAUDE.md").write_text(existing, encoding="utf-8")

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "created")
        content = (self.repo_root / "CLAUDE.md").read_text()
        self.assertIn(existing.rstrip("\n"), content)
        self.assertIn(reconcile.IMPORT_LINE, content.splitlines())

    def test_file_present_with_line_is_noop(self):
        _make_correct_repo(self.repo_root)
        original = (self.repo_root / "CLAUDE.md").read_text()

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "PASS")

        apply_items = reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "noop")
        self.assertEqual((self.repo_root / "CLAUDE.md").read_text(), original)


class TestProjectStateNeverTouched(ReconcileTestBase):
    def test_project_state_untouched_across_check_and_every_apply_mode(self):
        _make_correct_repo(self.repo_root)
        ps = self.repo_root / "PROJECT_STATE.md"
        custom = "# PROJECT_STATE\n\ncustom in-progress content\n"
        ps.write_text(custom, encoding="utf-8")

        reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(ps.read_text(), custom)

        for is_rice_update in (False, True):
            items = reconcile.apply(self.repo_root, is_rice_update=is_rice_update, rtk_probe=_ok_rtk_probe)
            self.assertEqual(ps.read_text(), custom)
            self.assertFalse(any("PROJECT_STATE" in i.name for i in items))

    def test_project_state_missing_is_never_created_by_reconcile(self):
        _make_correct_repo(self.repo_root)
        self.assertFalse((self.repo_root / "PROJECT_STATE.md").exists())
        reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertFalse((self.repo_root / "PROJECT_STATE.md").exists())


class TestMattAndRtkPresence(ReconcileTestBase):
    def test_matt_artifacts_present(self):
        _make_correct_repo(self.repo_root)
        claude_md = self.repo_root / "CLAUDE.md"
        claude_md.write_text(claude_md.read_text() + "\n## Agent skills\n\nsee docs/agents\n", encoding="utf-8")
        (self.repo_root / "docs" / "agents").mkdir(parents=True)
        (self.repo_root / "docs" / "agents" / "issue-tracker.md").write_text("x", encoding="utf-8")

        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["matt:artifacts"], "PASS")

    def test_matt_artifacts_missing(self):
        _make_correct_repo(self.repo_root)
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["matt:artifacts"], "FAIL")

    def test_matt_artifacts_never_written_by_apply(self):
        _make_correct_repo(self.repo_root)
        reconcile.apply(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertFalse((self.repo_root / "docs").exists())

    def test_rtk_present(self):
        _make_correct_repo(self.repo_root)
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "PASS")

    def test_rtk_missing(self):
        _make_correct_repo(self.repo_root)
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_missing_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "FAIL")

    def test_rtk_probe_error_is_not_verified(self):
        _make_correct_repo(self.repo_root)
        check_items, _ = reconcile.check(self.repo_root, rtk_probe=_erroring_rtk_probe)
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
        # matt/rtk items are informational-only -- apply never acts on them,
        # so a FAIL there doesn't mean apply left something undone.
        rice_owned = [i for i in items if not i.name.startswith(("matt:", "rtk"))]
        actionable = [i for i in rice_owned if i.state in ("created", "updated", "conflict", "FAIL")]
        self.assertEqual(actionable, [], actionable)

    def test_check_summary_line_is_one_line_and_greppable(self):
        _make_correct_repo(self.repo_root)
        items, summary = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(len(summary.splitlines()), 1)
        self.assertIn("RICE-CHECK", summary)
        for i in items:
            self.assertIn(i.state, ("PASS", "FAIL", "NOT_VERIFIED"))


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
        _write_settings(self.repo_root, settings)

        reconcile.apply(self.repo_root, is_rice_update=True, rtk_probe=_ok_rtk_probe)
        after = _read_settings(self.repo_root)
        self.assertEqual(after["outputStyle"], "rice:老弟")  # rice-defined: fixed
        self.assertEqual(after["permissions"]["allow"], ["Bash(npm test *)"])
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
        items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        st = {i.name: i for i in items}
        self.assertEqual(st["plugin:installed"].state, "PASS")
        self.assertIn("0.1.1", st["plugin:installed"].detail)

    def test_not_installed_is_fail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"other@mp","version":"1.0.0"}]')
        items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["plugin:installed"], "FAIL")

    def test_cli_unavailable_is_not_verified(self):
        _make_correct_repo(self.repo_root)
        def probe(cmd):
            raise FileNotFoundError("claude")
        items, _ = reconcile.check(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
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
