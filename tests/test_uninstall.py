"""Tests for scripts/uninstall.py (RICE-BOOTSTRAP-025): rice leaves a repo as
cleanly as it arrived. Only rice-owned / rice-created state goes; everything
project-owned is byte-identical afterwards; only the script's own check may
say RICE-UNINSTALLED.

Run: python3 -m unittest tests.test_uninstall -v
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reconcile  # noqa: E402
import settings_template  # noqa: E402
import uninstall  # noqa: E402
from reconcile import enter_waiting_for_matt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PROJECT_CLAUDE_MD = "# My project\n\nProject-owned instructions.\n\n## Agent skills\n\n### Issue tracker\n\nsee docs/agents/issue-tracker.md\n"
PROJECT_AGENTS_MD = "# Agents\n\nProject-owned guidance for every agent.\n"


def _states(items):
    return {i.name: i.state for i in items}


def _bootstrapped_repo(root: Path, *, extra_project_settings=True) -> None:
    """A fully rice-bootstrapped repo that also carries project-owned
    content of every kind rice must leave alone."""
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    s = settings_template.desired_settings(True)
    if extra_project_settings:
        s["permissions"]["allow"] = ["Bash(npm test *)"]
        s["permissions"]["ask"] = ["Bash(rm *)"]
        s["permissions"]["deny"] = ["Bash(curl *)"] + s["permissions"]["deny"] + ["Bash(wget *)"]
        s["enabledPlugins"] = {"other@mp": True}
        s["extraKnownMarketplaces"] = {"mp": {"source": {"source": "github", "repo": "x/y"}}}
        s["env"]["MY_VAR"] = "keep"
        s["hooks"] = {"SessionEnd": [{"matcher": "clear", "hooks": [{"type": "command", "command": "echo hi"}]}]}
        s["someOtherKey"] = {"nested": True}
    (root / ".claude" / "settings.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
    (root / ".claude" / "rice.md").write_text(reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".claude" / "rice-version").write_text("0.1.1\n", encoding="utf-8")
    enter_waiting_for_matt(root)
    (root / "CLAUDE.md").write_text(PROJECT_CLAUDE_MD + f"\n{reconcile.IMPORT_LINE}\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(PROJECT_AGENTS_MD + f"\n{reconcile.AGENTS_POINTER}", encoding="utf-8")
    # project-owned things rice must not touch
    (root / ".claude" / "output-styles").mkdir(exist_ok=True)
    (root / ".claude" / "output-styles" / "mine.md").write_text("---\nname: mine\n---\nbe terse\n", encoding="utf-8")
    (root / "session-history").mkdir(exist_ok=True)
    (root / "session-history" / "INDEX.md").write_text("| a | b |\n", encoding="utf-8")
    (root / "session-history" / "2026-01-01_x.md").write_text("# archived\n", encoding="utf-8")
    (root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "agents" / "issue-tracker.md").write_text("matt\n", encoding="utf-8")
    (root / "docs" / "agents" / "domain.md").write_text("matt\n", encoding="utf-8")
    (root / "src").mkdir(exist_ok=True); (root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class UninstallBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        expected = uninstall.expected_after(self.root)
        uninstall.uninstall(self.root)
        return uninstall.check(self.root, expected=expected)


class TestCleanUninstall(UninstallBase):
    def test_clean_uninstall_removes_only_rice_state(self):
        _bootstrapped_repo(self.root)
        items, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED", "\n".join(f"{i.name} {i.state} {i.detail}" for i in items))
        self.assertFalse((self.root / ".claude" / "rice.md").exists())
        self.assertFalse((self.root / ".claude" / "rice-version").exists())
        self.assertFalse((self.root / ".claude" / "WAITING_FOR_MATT").exists())
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("rice-bootstrap", s["extraKnownMarketplaces"])
        self.assertNotIn("rice@rice-bootstrap", s["enabledPlugins"])
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", s["env"])
        self.assertNotIn("RICE_SESSION_ARCHIVE", s["env"])
        self.assertNotIn("outputStyle", s)  # rice's own canonical value goes with rice
        for rule in settings_template.desired_settings(True)["permissions"]["deny"]:
            self.assertNotIn(rule, s["permissions"]["deny"])

    def test_project_owned_settings_preserved(self):
        _bootstrapped_repo(self.root)
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(s["permissions"]["allow"], ["Bash(npm test *)"])
        self.assertEqual(s["permissions"]["ask"], ["Bash(rm *)"])
        self.assertEqual(s["permissions"]["deny"], ["Bash(curl *)", "Bash(wget *)"])  # unrelated deny rules, in order
        self.assertTrue(s["enabledPlugins"]["other@mp"])
        self.assertEqual(s["extraKnownMarketplaces"]["mp"]["source"]["repo"], "x/y")
        self.assertEqual(s["env"]["MY_VAR"], "keep")
        self.assertEqual(s["hooks"]["SessionEnd"][0]["hooks"][0]["command"], "echo hi")
        self.assertEqual(s["someOtherKey"], {"nested": True})

    def test_custom_output_style_and_non_rice_subagent_model_preserved(self):
        _bootstrapped_repo(self.root)
        p = self.root / ".claude" / "settings.json"
        s = json.loads(p.read_text(encoding="utf-8"))
        s["outputStyle"] = "mine"; s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "opus"
        p.write_text(json.dumps(s), encoding="utf-8")
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        s = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(s["outputStyle"], "mine")
        self.assertEqual(s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "opus")
        self.assertEqual((self.root / ".claude" / "output-styles" / "mine.md").read_text(), "---\nname: mine\n---\nbe terse\n")

    def test_session_history_matt_artifacts_repo_files_byte_identical(self):
        _bootstrapped_repo(self.root)
        before = _tree(self.root)
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        after = _tree(self.root)
        for rel in ("session-history/INDEX.md", "session-history/2026-01-01_x.md",
                    "docs/agents/issue-tracker.md", "docs/agents/domain.md", "src/main.py",
                    ".claude/output-styles/mine.md"):
            self.assertEqual(after[rel], before[rel], rel)

    def test_claude_md_other_content_byte_identical_only_import_line_removed(self):
        _bootstrapped_repo(self.root)
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), PROJECT_CLAUDE_MD)

    def test_agents_md_project_content_byte_identical_only_pointer_block_removed(self):
        _bootstrapped_repo(self.root)
        items, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED", "\n".join(f"{i.name} {i.state}" for i in items))
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), PROJECT_AGENTS_MD)

    def test_agents_md_rice_created_only_pointer_block_is_removed_as_rice_created(self):
        _bootstrapped_repo(self.root)
        (self.root / "AGENTS.md").write_text(reconcile.AGENTS_POINTER, encoding="utf-8")
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_claude_md_rice_created_only_import_line_is_removed_as_rice_created(self):
        _bootstrapped_repo(self.root)
        (self.root / "CLAUDE.md").write_text(f"{reconcile.IMPORT_LINE}\n", encoding="utf-8")
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        self.assertFalse((self.root / "CLAUDE.md").exists())

    def test_rtk_untouched_no_subprocess_no_global_plugin_uninstall(self):
        src = (REPO / "scripts" / "uninstall.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", src)
        self.assertNotIn("plugin uninstall", src)
        self.assertNotIn("marketplace remove", src)
        for name in ("rtk", "claude"):
            self.assertNotRegex(src, rf'\[\s*"{name}"')
        _bootstrapped_repo(self.root)
        with mock.patch("os.system") as sysc:
            self._run()
        sysc.assert_not_called()

    def test_repeated_uninstall_is_idempotent_and_absent_entries_do_not_error(self):
        _bootstrapped_repo(self.root)
        self._run()
        snapshot = _tree(self.root)
        items, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        self.assertEqual(_tree(self.root), snapshot)
        # never bootstrapped at all: still a clean, non-erroring RICE-UNINSTALLED
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "README.md").write_text("x")
            expected = uninstall.expected_after(root); uninstall.uninstall(root)
            items, verdict = uninstall.check(root, expected=expected)
            self.assertEqual(verdict, "RICE-UNINSTALLED")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["README.md"])

    def test_a_013_era_project_scope_leftover_is_migrated_away_too(self):
        """RICE-FIX-001: uninstall never touches the user-level plugin
        install, but a 0.1.3-era PROJECT-scope declaration is rice's own
        leftover in the repo, and goes with everything else."""
        _bootstrapped_repo(self.root)
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        s["extraKnownMarketplaces"][settings_template.MARKETPLACE_NAME] = {
            "source": {"source": "github", "repo": settings_template.MARKETPLACE_REPO, "ref": "release/0.1.3"}
        }
        s["enabledPlugins"][settings_template.PLUGIN_KEY] = True
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        items, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED", "\n".join(f"{i.name} {i.state} {i.detail}" for i in items))
        after = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn(settings_template.MARKETPLACE_NAME, after["extraKnownMarketplaces"])
        self.assertNotIn(settings_template.PLUGIN_KEY, after["enabledPlugins"])
        # ... and the project's own, unrelated entries in those same containers survive
        self.assertTrue(after["enabledPlugins"]["other@mp"])
        self.assertEqual(after["extraKnownMarketplaces"]["mp"]["source"]["repo"], "x/y")

    def test_claude_dir_removed_only_when_nothing_project_owned_remains(self):
        _bootstrapped_repo(self.root, extra_project_settings=False)
        import shutil; shutil.rmtree(self.root / ".claude" / "output-styles")
        _, verdict = self._run()
        self.assertEqual(verdict, "RICE-UNINSTALLED")
        self.assertFalse((self.root / ".claude").exists())
        _bootstrapped_repo(self.root)  # with project-owned content -> dir stays
        self._run()
        self.assertTrue((self.root / ".claude" / "settings.json").exists())


class TestUninstallCheck(UninstallBase):
    def test_check_fails_closed_on_every_leftover(self):
        leftovers = {
            "rice-md:absent": lambda r: (r / ".claude" / "rice.md").write_text("x"),
            "verified-version:absent": lambda r: (r / ".claude" / "rice-version").write_text("0.1.1\n"),
            "waiting-marker:absent": lambda r: enter_waiting_for_matt(r),
            "claude-md:import-absent": lambda r: (r / "CLAUDE.md").write_text(PROJECT_CLAUDE_MD + f"\n{reconcile.IMPORT_LINE}\n"),
        }
        for name, plant in leftovers.items():
            _bootstrapped_repo(self.root); self._run()
            (self.root / ".claude").mkdir(exist_ok=True); plant(self.root)
            items, verdict = uninstall.check(self.root)
            self.assertTrue(verdict.startswith("RICE-UNINSTALL FAIL"), name)
            self.assertEqual(_states(items)[name], "FAIL", name)
        # every kind of rice settings leftover, caught by the one item whose
        # answer comes from reconcile's own projection (RICE-BOOTSTRAP-045)
        settings_leftovers = {
            "marketplace": lambda s: s.setdefault("extraKnownMarketplaces", {}).__setitem__(
                "rice-bootstrap", {"source": {"source": "github", "repo": "L26041040/rice-bootstrap"}}),
            "enabled-plugin": lambda s: s.setdefault("enabledPlugins", {}).__setitem__("rice@rice-bootstrap", True),
            "archive-switch": lambda s: s.setdefault("env", {}).__setitem__("RICE_SESSION_ARCHIVE", "1"),
            "rice output style": lambda s: s.__setitem__("outputStyle", settings_template.RICE_STYLE),
        }
        # not listed: a lone deny rule. Under multiset ownership
        # (RICE-BOOTSTRAP-046) a single occurrence with no other rice state is
        # indistinguishable from a project rule, and rice keeps no manifest --
        # so it is the project's, and leaving it is the correct answer.
        for what, plant in settings_leftovers.items():
            _bootstrapped_repo(self.root); self._run()
            p = self.root / ".claude" / "settings.json"
            s = json.loads(p.read_text()) if p.exists() else {}
            plant(s); p.parent.mkdir(exist_ok=True); p.write_text(json.dumps(s))
            items, verdict = uninstall.check(self.root)
            self.assertTrue(verdict.startswith("RICE-UNINSTALL FAIL"), what)
            self.assertEqual(_states(items)["settings:rice-absent"], "FAIL", what)

    def test_sentinel_drift_fails(self):
        _bootstrapped_repo(self.root)
        expected = uninstall.expected_after(self.root)
        uninstall.uninstall(self.root)
        (self.root / "session-history" / "INDEX.md").write_text("tampered\n")
        items, verdict = uninstall.check(self.root, expected=expected)
        self.assertTrue(verdict.startswith("RICE-UNINSTALL FAIL"))
        self.assertEqual(_states(items)["sentinel:session-history/INDEX.md"], "FAIL")

    def test_exception_is_error_not_pass(self):
        _bootstrapped_repo(self.root); self._run()
        with mock.patch.object(uninstall, "_collect_items", side_effect=RuntimeError("boom")):
            items, verdict = uninstall.check(self.root)
        self.assertTrue(verdict.startswith("RICE-UNINSTALL FAIL"))
        self.assertEqual(items[0].state, "ERROR")

    def test_unknown_states_never_pass(self):
        for bad in ("FAIL", "NOT_VERIFIED", "ERROR", "pass", "", "noop"):
            items = [uninstall.Item("rice-md:absent", bad)]
            self.assertTrue(uninstall.verdict(items).startswith("RICE-UNINSTALL FAIL"), bad)
        self.assertEqual(uninstall.verdict([uninstall.Item("x", "PASS")]), "RICE-UNINSTALLED")
        self.assertTrue(uninstall.verdict([]).startswith("RICE-UNINSTALL FAIL"))

    def test_cli_runs_snapshot_uninstall_check_and_exit_code(self):
        _bootstrapped_repo(self.root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = uninstall.main([str(self.root)])
        self.assertEqual(rc, 0); self.assertEqual(buf.getvalue().strip().splitlines()[-1], "RICE-UNINSTALLED")
        (self.root / ".claude").mkdir(exist_ok=True); (self.root / ".claude" / "rice.md").write_text("x")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = uninstall.main([str(self.root), "--check-only"])
        self.assertEqual(rc, 1); self.assertIn("RICE-UNINSTALL FAIL", buf.getvalue())

    def test_verdict_string_only_from_uninstall_script(self):
        for py in (REPO / "scripts").glob("*.py"):
            if py.name == "uninstall.py":
                continue
            self.assertNotIn("RICE-UNINSTALLED", py.read_text(encoding="utf-8"), py.name)


if __name__ == "__main__":
    unittest.main()
