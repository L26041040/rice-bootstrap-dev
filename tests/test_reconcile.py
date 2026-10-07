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

import archive_hook  # noqa: E402
import evidence  # noqa: E402
import reconcile  # noqa: E402
import settings_template  # noqa: E402

REF = settings_template.release_ref()
LEGACY_ITEM = "settings:legacy-project-scope"


def _plant_legacy(settings: dict) -> None:
    """A 0.1.3-era project-scope Rice declaration, exactly as that build's
    `desired_settings` used to write it (RICE-FIX-001)."""
    settings.setdefault("extraKnownMarketplaces", {})[settings_template.MARKETPLACE_NAME] = {
        "source": {"source": "github", "repo": settings_template.MARKETPLACE_REPO, "ref": REF}
    }
    settings.setdefault("enabledPlugins", {})[settings_template.PLUGIN_KEY] = True


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
    return settings_template.desired_settings(True)


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


def _rubric(repo_root, **probes):
    """The full rubric the verifier evaluates: rice-owned state plus
    environment evidence (RICE-BOOTSTRAP-046 split them)."""
    return reconcile.check_owned(repo_root) + evidence.collect(repo_root, **probes)


class ReconcileTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        # keep tests hermetic: never shell out to the real `claude` CLI
        self._real_plugin_probe = evidence._default_plugin_probe
        evidence._default_plugin_probe = _installed_plugin_probe
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.repo_root / "cfg")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        evidence._default_plugin_probe = self._real_plugin_probe
        self._tmp.cleanup()


class TestLegacyProjectScopeMigration(ReconcileTestBase):
    """RICE-FIX-001: 0.1.3 declared the plugin at PROJECT scope too. Rice's
    distribution is user scope only; nothing here writes those keys again,
    apply only ever migrates a leftover one away."""

    def test_absent_is_pass_and_apply_is_a_noop(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)[LEGACY_ITEM], "PASS")
        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)[LEGACY_ITEM], "noop")

    def test_a_013_era_declaration_fails_check_and_is_migrated_away_on_apply(self):
        settings = _fully_correct_settings()
        _plant_legacy(settings)
        _write_settings(self.repo_root, settings)

        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)[LEGACY_ITEM], "FAIL")

        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)[LEGACY_ITEM], "removed")
        after = _read_settings(self.repo_root)
        self.assertNotIn(settings_template.MARKETPLACE_NAME, after.get("extraKnownMarketplaces", {}))
        self.assertNotIn(settings_template.PLUGIN_KEY, after.get("enabledPlugins", {}))

        # ... and it stays gone: a second apply is a plain noop
        self.assertEqual(_states(reconcile.apply(self.repo_root))[LEGACY_ITEM], "noop")
        self.assertEqual(_states(_rubric(self.repo_root, rtk_probe=_ok_rtk_probe))[LEGACY_ITEM], "PASS")

    def test_the_containers_rice_created_go_with_its_declaration(self):
        """RICE-UPGRADE-MATRIX-001: 0.1.2/0.1.3 created `extraKnownMarketplaces`
        and `enabledPlugins` to hold its declaration. Migrating the
        declaration away must not leave the two `{}` husks behind -- a fresh
        install has neither, and uninstall would read a husk as the
        project's own empty container and keep it forever."""
        _make_correct_repo(self.repo_root)  # an already-bootstrapped repo
        settings = _read_settings(self.repo_root)
        _plant_legacy(settings)
        _write_settings(self.repo_root, settings)
        reconcile.apply(self.repo_root)
        after = _read_settings(self.repo_root)
        self.assertNotIn("extraKnownMarketplaces", after)
        self.assertNotIn("enabledPlugins", after)
        self.assertEqual(after, _fully_correct_settings())

    def test_a_container_still_holding_project_entries_stays(self):
        _make_correct_repo(self.repo_root)
        settings = _read_settings(self.repo_root)
        settings["enabledPlugins"] = {"other-plugin@other-marketplace": True}
        _plant_legacy(settings)
        _write_settings(self.repo_root, settings)
        reconcile.apply(self.repo_root)
        after = _read_settings(self.repo_root)
        self.assertEqual(after["enabledPlugins"], {"other-plugin@other-marketplace": True})
        self.assertNotIn("extraKnownMarketplaces", after)

    def test_a_projects_own_false_enablement_is_never_read_as_rices(self):
        """Every build that declared itself at project scope wrote `true`;
        `false` is the project switching rice off, and stays."""
        _make_correct_repo(self.repo_root)
        settings = _read_settings(self.repo_root)
        settings["enabledPlugins"] = {settings_template.PLUGIN_KEY: False}
        _write_settings(self.repo_root, settings)
        self.assertEqual(_states(_rubric(self.repo_root, rtk_probe=_ok_rtk_probe))[LEGACY_ITEM], "PASS")
        reconcile.apply(self.repo_root)
        self.assertIs(_read_settings(self.repo_root)["enabledPlugins"][settings_template.PLUGIN_KEY], False)

    def test_the_enablement_of_a_same_named_marketplace_that_is_not_rices_stays(self):
        _make_correct_repo(self.repo_root)
        settings = _read_settings(self.repo_root)
        fork = {"source": {"source": "github", "repo": "someone/rice-fork"}}
        settings["extraKnownMarketplaces"] = {settings_template.MARKETPLACE_NAME: fork}
        settings["enabledPlugins"] = {settings_template.PLUGIN_KEY: True}
        _write_settings(self.repo_root, settings)
        self.assertEqual(_states(_rubric(self.repo_root, rtk_probe=_ok_rtk_probe))[LEGACY_ITEM], "PASS")
        reconcile.apply(self.repo_root)
        after = _read_settings(self.repo_root)
        self.assertEqual(after["extraKnownMarketplaces"][settings_template.MARKETPLACE_NAME], fork)
        self.assertIs(after["enabledPlugins"][settings_template.PLUGIN_KEY], True)

    def test_a_projects_own_marketplace_or_plugin_at_another_name_is_untouched(self):
        settings = _fully_correct_settings()
        settings["extraKnownMarketplaces"] = {"other-marketplace": {"source": {"source": "github", "repo": "someone/else"}}}
        settings["enabledPlugins"] = {"other-plugin@other-marketplace": True}
        _write_settings(self.repo_root, settings)
        self.assertEqual(_states(_rubric(self.repo_root, rtk_probe=_ok_rtk_probe))[LEGACY_ITEM], "PASS")
        reconcile.apply(self.repo_root)
        after = _read_settings(self.repo_root)
        self.assertTrue(after["enabledPlugins"]["other-plugin@other-marketplace"])
        self.assertIn("other-marketplace", after["extraKnownMarketplaces"])

    def test_output_style_is_owner_choice_never_written_or_reasserted(self):
        # OWNER chose a custom project style -> apply (even converging drift) leaves it alone
        settings = _fully_correct_settings(); settings["outputStyle"] = "my-style"
        _write_settings(self.repo_root, settings)
        (self.repo_root / ".claude" / "output-styles").mkdir(parents=True)
        (self.repo_root / ".claude" / "output-styles" / "my.md").write_text("---\nname: my-style\n---\nbody\n")
        for is_update in (False, True):
            reconcile.apply(self.repo_root, converge_drift=is_update)
            self.assertEqual(_read_settings(self.repo_root)["outputStyle"], "my-style")
        # OWNER chose Claude default -> key absent stays absent
        settings = _fully_correct_settings(); del settings["outputStyle"]
        _write_settings(self.repo_root, settings)
        reconcile.apply(self.repo_root, converge_drift=True)
        self.assertNotIn("outputStyle", _read_settings(self.repo_root))


class TestOwnerAndRiceDefinedKeys(ReconcileTestBase):
    def test_owner_defined_key_present_is_pass_and_never_written(self):
        settings = _fully_correct_settings()
        _write_settings(self.repo_root, settings)
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.RICE_SESSION_ARCHIVE"], "PASS")

        before = _read_settings(self.repo_root)["env"]["RICE_SESSION_ARCHIVE"]
        reconcile.apply(self.repo_root, converge_drift=True)
        after = _read_settings(self.repo_root)["env"]["RICE_SESSION_ARCHIVE"]
        self.assertEqual(before, after)

    def test_owner_defined_key_missing_is_fail_and_never_created(self):
        settings = _fully_correct_settings()
        del settings["env"]["RICE_SESSION_ARCHIVE"]
        _write_settings(self.repo_root, settings)

        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["settings:env.RICE_SESSION_ARCHIVE"], "FAIL")

        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)["settings:env.RICE_SESSION_ARCHIVE"], "FAIL")
        self.assertNotIn("RICE_SESSION_ARCHIVE", _read_settings(self.repo_root)["env"])

class TestRiceMdWholeFile(ReconcileTestBase):
    def test_missing_is_created_on_apply_and_fails_check(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "FAIL")

        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)["rice.md"], "created")
        dest = self.repo_root / ".claude" / "rice.md"
        self.assertEqual(dest.read_text(), reconcile.RICE_MD_RESOURCE.read_text())

    def test_matching_content_is_noop(self):
        _make_correct_repo(self.repo_root)
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "PASS")
        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)["rice.md"], "noop")

    def test_hand_edited_same_version_is_conflict_and_untouched(self):
        _make_correct_repo(self.repo_root)
        dest = self.repo_root / ".claude" / "rice.md"
        dest.write_text("# hand-edited\n", encoding="utf-8")

        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rice.md"], "FAIL")

        apply_items = reconcile.apply(self.repo_root, converge_drift=False)
        self.assertEqual(_states(apply_items)["rice.md"], "conflict")
        self.assertEqual(dest.read_text(), "# hand-edited\n")

    def test_drift_under_rice_update_is_overwritten(self):
        _make_correct_repo(self.repo_root)
        dest = self.repo_root / ".claude" / "rice.md"
        dest.write_text("# stale content from an older version\n", encoding="utf-8")

        apply_items = reconcile.apply(self.repo_root, converge_drift=True)
        self.assertEqual(_states(apply_items)["rice.md"], "updated")
        self.assertEqual(dest.read_text(), reconcile.RICE_MD_RESOURCE.read_text())


class TestClaudeMdImportLine(ReconcileTestBase):
    def test_file_missing_creates_minimal_file_with_only_import_line(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "FAIL")

        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "created")
        content = (self.repo_root / "CLAUDE.md").read_text()
        self.assertEqual(content.strip(), reconcile.IMPORT_LINE)

    def test_file_present_without_line_appends_without_touching_rest(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        existing = "# My Project\n\nSome project-owned content.\n"
        (self.repo_root / "CLAUDE.md").write_text(existing, encoding="utf-8")

        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "FAIL")

        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "created")
        content = (self.repo_root / "CLAUDE.md").read_text()
        self.assertIn(existing.rstrip("\n"), content)
        self.assertIn(reconcile.IMPORT_LINE, content.splitlines())

    def test_file_present_with_line_is_noop(self):
        _make_correct_repo(self.repo_root)
        original = (self.repo_root / "CLAUDE.md").read_text()

        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["CLAUDE.md:import-line"], "PASS")

        apply_items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(apply_items)["CLAUDE.md:import-line"], "noop")
        self.assertEqual((self.repo_root / "CLAUDE.md").read_text(), original)


class TestAgentsMdPointer(ReconcileTestBase):
    """RICE-BOOTSTRAP-042: AGENTS.md is the cross-agent entry point. It holds
    one rice-owned pointer block and never a copy of the rules; the rules
    live only in .claude/rice.md."""

    def test_fresh_bootstrap_creates_agents_md_with_only_the_pointer(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        items = reconcile.apply(self.repo_root)
        self.assertEqual(_states(items)["AGENTS.md:rice-pointer"], "created")
        self.assertEqual((self.repo_root / "AGENTS.md").read_text(encoding="utf-8"), reconcile.AGENTS_POINTER)
        self.assertEqual(_states(_rubric(self.repo_root, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "PASS")
        self.assertEqual(_states(reconcile.apply(self.repo_root))["AGENTS.md:rice-pointer"], "noop")

    def test_one_canonical_policy_source(self):
        """CLAUDE.md imports the rules, AGENTS.md points at them, neither
        restates them."""
        _write_settings(self.repo_root, _fully_correct_settings())
        (self.repo_root / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
        (self.repo_root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        reconcile.apply(self.repo_root)
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
            reconcile.apply(self.repo_root, converge_drift=mode)
            text = (self.repo_root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(project), text)
            self.assertEqual(text.count(reconcile.AGENTS_BEGIN), 1)
            self.assertTrue(text.endswith(reconcile.AGENTS_POINTER))

    def test_hand_edited_block_is_conflict_until_update_or_repair_converges_it(self):
        _write_settings(self.repo_root, _fully_correct_settings())
        project = "# Agents\n\nkeep me\n"
        (self.repo_root / "AGENTS.md").write_text(
            project + f"\n{reconcile.AGENTS_BEGIN}\nedited by hand\n{reconcile.AGENTS_END}\n", encoding="utf-8")
        self.assertEqual(_states(_rubric(self.repo_root, rtk_probe=_ok_rtk_probe))["AGENTS.md:rice-pointer"], "FAIL")
        self.assertEqual(_states(reconcile.apply(self.repo_root))["AGENTS.md:rice-pointer"], "conflict")
        self.assertIn("edited by hand", (self.repo_root / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertEqual(_states(reconcile.apply(self.repo_root, converge_drift=True))["AGENTS.md:rice-pointer"], "updated")
        text = (self.repo_root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text, project + "\n" + reconcile.AGENTS_POINTER)


class TestIdempotentApplyAndSummary(ReconcileTestBase):
    def test_apply_on_already_correct_repo_is_pure_noop(self):
        _make_correct_repo(self.repo_root)
        settings_path = self.repo_root / ".claude" / "settings.json"
        before_bytes = settings_path.read_bytes()
        before_mtime = settings_path.stat().st_mtime_ns

        items = reconcile.apply(self.repo_root)

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
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        for i in items:
            self.assertIn(i.state, ("PASS", "FAIL", "NOT_VERIFIED"))
            self.assertNotIn("VERIFIED rice", i.detail)


if __name__ == "__main__":
    unittest.main()


class TestProjectOwnedSettingsPreserved(ReconcileTestBase):
    def test_project_owned_keys_survive_apply_in_every_mode(self):
        settings = _fully_correct_settings()
        settings["permissions"]["allow"] = ["Bash(npm test *)"]
        settings["enabledPlugins"] = {"other-plugin@other-marketplace": True}
        settings["extraKnownMarketplaces"] = {"other-marketplace": {"source": {"source": "github", "repo": "someone/else"}}}
        settings["env"]["MY_OWN_VAR"] = "keep-me"
        settings["someOtherKey"] = {"nested": True}
        # a 0.1.3-era legacy declaration, alongside the project's own --
        # force a real write so apply actually touches the file
        _plant_legacy(settings)
        settings["outputStyle"] = "drifted"
        settings["permissions"]["ask"] = ["Bash(rm *)"]  # project-owned now (-024)
        _write_settings(self.repo_root, settings)

        reconcile.apply(self.repo_root, converge_drift=True)
        after = _read_settings(self.repo_root)
        self.assertEqual(after["outputStyle"], "drifted")  # OWNER choice: untouched by apply
        self.assertEqual(after["permissions"]["allow"], ["Bash(npm test *)"])
        self.assertEqual(after["permissions"]["ask"], ["Bash(rm *)"])
        # the legacy rice declaration is migrated away ...
        self.assertNotIn(settings_template.MARKETPLACE_NAME, after["extraKnownMarketplaces"])
        self.assertNotIn(settings_template.PLUGIN_KEY, after["enabledPlugins"])
        # ... and the project's own, differently-named entries are not
        self.assertTrue(after["enabledPlugins"]["other-plugin@other-marketplace"])
        self.assertIn("other-marketplace", after["extraKnownMarketplaces"])
        self.assertEqual(after["env"]["MY_OWN_VAR"], "keep-me")
        self.assertEqual(after["someOtherKey"], {"nested": True})


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


class TestRubricKinds(ReconcileTestBase):
    """Every item carries a kind and the v1 list is covered. Verdicts are
    NOT reconcile's business (RICE-BOOTSTRAP-021) -- see tests/test_verify.py."""

    def test_every_item_has_a_kind_and_v1_coverage(self):
        _make_correct_repo(self.repo_root)
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        for i in items:
            self.assertIn(i.kind, (reconcile.EXISTS, reconcile.MATCHES, reconcile.BEHAVES), i)
        names = {i.name for i in items}
        for required in [
            "plugin:installed", "plugin:build", "plugin:scope",
            "output-style:bundled", "output-style:choice",
            "settings:permissions.deny",
            LEGACY_ITEM, "rice.md", "CLAUDE.md:import-line",
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
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
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


