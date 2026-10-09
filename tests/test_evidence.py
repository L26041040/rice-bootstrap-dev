"""Tests for scripts/evidence.py (RICE-BOOTSTRAP-046): the environment half
of the rubric.

`reconcile` knows what rice owns; this module knows what is true about the
environment rice is running in -- the installed plugin and its scope, the
output styles that resolve, this build's hook wiring, the OWNER's Matt
setup, RTK, and what the harness recorded about the git-deny rules. It
collects facts and has no verdict: `verify.py` alone turns them into one.

Run: python3 -m unittest tests.test_evidence -v
"""
import inspect
import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_hook  # noqa: E402
import verify  # noqa: E402
import evidence  # noqa: E402
import reconcile  # noqa: E402
import settings_template  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF = settings_template.release_ref()


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def _ok_rtk_probe(cmd):
    return _Result(0, "rtk 0.46.0\n")


def _missing_rtk_probe(cmd):
    raise FileNotFoundError("rtk not found")


def _erroring_rtk_probe(cmd):
    raise OSError("boom")


def _installed_plugin_probe(cmd):
    return _Result(0, '[{"id":"rice@rice-bootstrap","version":"0.1.1","scope":"user","enabled":true}]')


def _fully_correct_settings() -> dict:
    return settings_template.desired_settings(True)


def _write_settings(repo_root: Path, settings: dict) -> Path:
    p = repo_root / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings), encoding="utf-8")
    return p


def _make_correct_repo(repo_root: Path) -> None:
    _write_settings(repo_root, _fully_correct_settings())
    (repo_root / ".claude" / "rice.md").write_text(
        reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    (repo_root / "CLAUDE.md").write_text(f"# Some project\n\n{reconcile.IMPORT_LINE}\n", encoding="utf-8")
    (repo_root / "AGENTS.md").write_text(reconcile.AGENTS_POINTER, encoding="utf-8")


def _states(items):
    return {i.name: i.state for i in items}


def _rubric(repo_root, **probes):
    return reconcile.check_owned(repo_root) + evidence.collect(repo_root, **probes)


def _git_r(cwd: Path, *args: str):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


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
        evidence._default_plugin_probe = self._real_plugin_probe
        self._env.stop()
        self._tmp.cleanup()


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
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["matt:artifacts"], "PASS")

    def test_matt_each_required_artifact_alone_fails(self):
        for kw in ({"heading": False}, {"issue": False}, {"domain": False}):
            self._matt_repo(**kw)
            items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
            self.assertEqual(_states(items)["matt:artifacts"], "FAIL", kw)

    def test_matt_triage_doc_required_only_when_triage_capability_present(self):
        # triage heading present in CLAUDE.md (Matt writes it when triage is installed) -> doc required
        self._matt_repo(triage_heading=True, triage_doc=False)
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["matt:artifacts"], "FAIL")
        self._matt_repo(triage_heading=True, triage_doc=True)
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(items)["matt:artifacts"], "PASS")
        # installed mattpocock plugin that ships a triage skill -> doc required even without the heading
        plugin_root = self.repo_root / "mp"; (plugin_root / "skills" / "engineering" / "triage").mkdir(parents=True)
        (plugin_root / "skills" / "engineering" / "triage" / "SKILL.md").write_text("---\nname: triage\n---\n")
        probe = lambda cmd: _Result(0, json.dumps([
            {"id": "rice@rice-bootstrap", "version": "0.1.1", "scope": "user", "enabled": True},
            {"id": "mattpocock-skills@mattpocock", "version": "1.2.3", "installPath": str(plugin_root)}]))
        self._matt_repo(triage_heading=False, triage_doc=False)
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["matt:artifacts"], "FAIL")
        self._matt_repo(triage_heading=False, triage_doc=True)
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["matt:artifacts"], "PASS")

    def test_matt_artifacts_missing(self):
        _make_correct_repo(self.repo_root)
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["matt:artifacts"], "FAIL")

    def test_matt_artifacts_never_written_by_apply(self):
        _make_correct_repo(self.repo_root)
        reconcile.apply(self.repo_root)
        self.assertFalse((self.repo_root / "docs").exists())

    def test_rtk_present(self):
        _make_correct_repo(self.repo_root)
        check_items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "PASS")

    def test_rtk_missing(self):
        _make_correct_repo(self.repo_root)
        check_items = _rubric(self.repo_root, rtk_probe=_missing_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "FAIL")

    def test_rtk_probe_error_is_not_verified(self):
        _make_correct_repo(self.repo_root)
        check_items = _rubric(self.repo_root, rtk_probe=_erroring_rtk_probe)
        self.assertEqual(_states(check_items)["rtk"], "NOT_VERIFIED")


class TestPluginInstalledPresence(ReconcileTestBase):
    """#18 runtime fact: declaring the marketplace/plugin in project settings
    does not install it (and Cloud never trusts the folder, so it is ignored
    there entirely). `check` therefore reports, presence-only, whether the
    rice plugin is actually installed on this machine, via the official CLI."""

    def test_installed_is_pass_with_version_in_detail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"rice@rice-bootstrap","version":"0.1.1","scope":"user","enabled":true}]')
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        st = {i.name: i for i in items}
        self.assertEqual(st["plugin:installed"].state, "PASS")
        self.assertIn("0.1.1", st["plugin:installed"].detail)

    def test_not_installed_is_fail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"other@mp","version":"1.0.0"}]')
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["plugin:installed"], "FAIL")

    def test_cli_unavailable_is_not_verified(self):
        _make_correct_repo(self.repo_root)
        def probe(cmd):
            raise FileNotFoundError("claude")
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        self.assertEqual(_states(items)["plugin:installed"], "NOT_VERIFIED")

    def test_apply_never_installs(self):
        _make_correct_repo(self.repo_root)
        calls = []
        def probe(cmd):
            calls.append(cmd)
            return _Result(0, '[]')
        reconcile.apply(self.repo_root)
        self.assertTrue(all(c[:3] == ["claude", "plugin", "list"] for c in calls), calls)


class TestArchiveBehavesDiagnostic(ReconcileTestBase):
    """_archive_behaves_item is a standalone diagnostic (RICE-BOOTSTRAP-032),
    not part of _rubric()'s rubric -- see TestRubricKinds above."""

    def setUp(self):
        super().setUp()
        self._remote_tmp = tempfile.TemporaryDirectory()
        _init_archive_repo(self.repo_root, Path(self._remote_tmp.name))

    def tearDown(self):
        self._remote_tmp.cleanup()
        super().tearDown()

    def test_pass_when_switched_off_regardless_of_git_state(self):
        settings = _fully_correct_settings(); settings["env"]["RICE_SESSION_ARCHIVE"] = "0"
        self.assertEqual(evidence.archive_behaves(self.repo_root, settings).state, "PASS")

    def test_not_verified_when_nothing_produced_yet(self):
        settings = _fully_correct_settings()
        self.assertEqual(evidence.archive_behaves(self.repo_root, settings).state, "NOT_VERIFIED")

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
        _git_r(self.repo_root, "commit", "-q", "-m", f"session history: x\n\n{archive_hook.RICE_ARCHIVE_TRAILER}")
        self.assertEqual(evidence.archive_behaves(self.repo_root, settings).state, "NOT_VERIFIED")

    def test_pass_once_the_archive_commit_reaches_upstream(self):
        settings = _fully_correct_settings()
        sh = self.repo_root / "session-history"; sh.mkdir()
        (sh / "INDEX.md").write_text(
            "# Session Archive Index\n\n| Date | Title | Excerpt | Turns | File |\n|---|---|---|---|---|\n"
            "| 2026-09-05 | t | e | 4 | 2026-09-05_x.md |\n")
        (sh / "2026-09-05_x.md").write_text("# archive\n")
        _git_r(self.repo_root, "add", "session-history")
        _git_r(self.repo_root, "commit", "-q", "-m", f"session history: x\n\n{archive_hook.RICE_ARCHIVE_TRAILER}")
        _git_r(self.repo_root, "push", "-q", "origin", "HEAD:main")
        self.assertEqual(evidence.archive_behaves(self.repo_root, settings).state, "PASS")

    def test_canonical_scope_is_user_and_duplicates_are_never_healthy(self):
        """RICE-BOOTSTRAP-044C. The verifier -- not the update lifecycle --
        decides that an install is unsupported, so there is one truth."""
        def probe(*pairs):
            return lambda cmd: _Result(0, json.dumps(
                [{"id": "rice@rice-bootstrap", "version": v, "scope": sc, "enabled": True}
                 for sc, v in pairs]))

        bv = evidence.build_version()
        self.assertEqual(evidence.CANONICAL_SCOPE, "user")
        ok = evidence._plugin_scope_item(probe(("user", bv)))
        self.assertEqual(ok.state, "PASS")

        dup = evidence._plugin_scope_item(probe(("user", bv), ("project", "0.0.1")))
        self.assertEqual(dup.state, "FAIL")
        self.assertIn("more than one scope", dup.detail)
        self.assertIn("uninstall", dup.detail)

        non_canonical = evidence._plugin_scope_item(probe(("project", bv)))
        self.assertEqual(non_canonical.state, "FAIL")
        self.assertIn("canonical install is user scope", non_canonical.detail)

        def unreadable(cmd):
            raise FileNotFoundError("claude")

        self.assertEqual(evidence._plugin_scope_item(unreadable).state, "NOT_VERIFIED")
        self.assertEqual(evidence._plugin_scope_item(lambda cmd: _Result(0, "[]")).state, "NOT_VERIFIED")

    def test_plugin_build_mismatch_is_fail(self):
        _make_correct_repo(self.repo_root)
        probe = lambda cmd: _Result(0, '[{"id":"rice@rice-bootstrap","version":"0.0.1","scope":"user","enabled":true}]')
        items = _rubric(self.repo_root, rtk_probe=_ok_rtk_probe, plugin_probe=probe)
        st = {i.name: i.state for i in items}
        self.assertEqual(st["plugin:installed"], "PASS")
        self.assertEqual(st["plugin:build"], "FAIL")
        # RICE-FINAL-UX-REVIEW-001: /reload-plugins switches a running session
        # to the installed build as well as a new session does (real session)
        detail = {i.name: i.detail for i in items}["plugin:build"]
        self.assertIn("/reload-plugins", detail)


class TestTheSplitHolds(unittest.TestCase):
    """RICE-BOOTSTRAP-046: two collectors, one verdict. Neither collector may
    grow into the other, and neither may grow a verdict."""

    def test_reconcile_touches_no_environment_at_all(self):
        raw = (REPO / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        src = "".join(raw.split('"""')[::2])  # code only: the docstring names what moved out
        for forbidden in ("import subprocess", "_probe", "claude plugin", "hooks.json",
                          "transcript", "rtk_", "installPath", "output_styles", "archive_hook"):
            self.assertNotIn(forbidden, src, f"reconcile still reaches into the environment: {forbidden}")
        self.assertNotIn("rtk_probe", str(inspect.signature(reconcile.check_owned)))
        self.assertEqual(list(inspect.signature(reconcile.check_owned).parameters), ["repo_root"])

    def test_evidence_collects_facts_and_never_decides_or_writes(self):
        src = (REPO / "scripts" / "evidence.py").read_text(encoding="utf-8")
        for forbidden in ("RICE-VERIFIED", "FINAL-VERIFIED", "GATE_A", "GATE_B", "gate_passes",
                          "rice-version", "write_text", "unlink", "mkdir", "import verify"):
            self.assertNotIn(forbidden, src, f"evidence is doing more than collecting: {forbidden}")

    def test_the_rubric_is_the_two_collectors_plus_the_verifiers_version_item(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}):
                owned = reconcile.check_owned(root)
                env = evidence.collect(root, rtk_probe=_ok_rtk_probe, plugin_probe=_installed_plugin_probe)
                report = verify.run(root, rtk_probe=_ok_rtk_probe, plugin_probe=_installed_plugin_probe)
        # the third source is the verifier's own version comparison
        # (RICE-SPEC-001 I17): only verify may touch the version file
        self.assertEqual([i.name for i in report.items],
                         [i.name for i in owned + env] + ["rice-version:not-newer"])
        self.assertFalse(report.item("rice-version:not-newer").owned)
        # only reconcile produces owned artifacts; evidence produces facts.
        # (reconcile also reports the OWNER-defined keys it must never write:
        # produced there, but not owned by rice either.)
        self.assertTrue(any(i.owned for i in owned))
        self.assertFalse(any(i.owned for i in env))

    def test_every_evidence_item_is_required_by_a_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}):
                names = {i.name for i in evidence.collect(root, rtk_probe=_ok_rtk_probe,
                                                          plugin_probe=_installed_plugin_probe)}
        self.assertEqual(names - set(verify.GATE_B), set(), "an evidence item no gate requires")

    def test_a_probe_that_cannot_answer_is_never_a_pass(self):
        def unreadable(cmd):
            raise FileNotFoundError("claude")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}):
                items = evidence.collect(root, rtk_probe=unreadable, plugin_probe=unreadable)
        for i in items:
            if i.name in ("plugin:installed", "plugin:build", "plugin:scope", "rtk"):
                self.assertNotEqual(i.state, "PASS", i.name)


if __name__ == "__main__":
    unittest.main()
