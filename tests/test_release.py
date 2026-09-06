"""Tests for scripts/export_public.py and scripts/release.py
(RICE-BOOTSTRAP-023): allowlist export, forbidden-path/secret audit, and
the release gates -- any FAIL blocks; only the script emits the verdicts.

The gates that shell out (tests, `claude plugin validate`) are exercised
through the real dry-run in CI/manual use; here they are stubbed so the
suite doesn't recurse into itself.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_public  # noqa: E402
import release  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class TestExport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.dest = Path(self._tmp.name) / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def test_export_is_allowlist_only_and_audit_clean(self):
        copied = export_public.export(self.dest)
        self.assertEqual(sorted(copied), sorted(export_public.ALLOWLIST))
        present = sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*") if p.is_file())
        self.assertEqual(present, sorted(export_public.ALLOWLIST))
        self.assertEqual(export_public.audit(self.dest), [])

    def test_export_starts_from_empty_dir(self):
        self.dest.mkdir(parents=True); (self.dest / "leftover.txt").write_text("x")
        export_public.export(self.dest)
        self.assertFalse((self.dest / "leftover.txt").exists())

    def test_forbidden_paths_never_in_allowlist(self):
        for rel in export_public.ALLOWLIST:
            self.assertIsNone(export_public.is_forbidden(rel), rel)
        for bad in ("session-history/x.md", "PROJECT_STATE.md", ".claude/settings.json", "CLAUDE.md",
                    "docs/agents/domain.md", "scripts/release.py", "scripts/export_public.py",
                    "tests/test_release.py", "a/__pycache__/b.pyc", ".claude/skills/rice-release/SKILL.md"):
            self.assertIsNotNone(export_public.is_forbidden(bad), bad)

    def test_audit_catches_stowaway_and_secrets_and_owner_data(self):
        export_public.export(self.dest)
        (self.dest / "session-history").mkdir(); (self.dest / "session-history" / "a.md").write_text("x")
        (self.dest / "README.md").write_text("token ghp_" + "a" * 36 + " mail someone@gmail.com "
                                             "https://claude.ai/code/session_01ABCDEFGHIJKLMNOPQRSTUV\n")
        problems = export_public.audit(self.dest)
        kinds = {p.split(":")[0] for p in problems}
        self.assertIn("forbidden (^session-history(/|$))", kinds)
        self.assertIn("not allowlisted", kinds)
        self.assertTrue(any(p.startswith("credential-shaped string (github-token)") for p in problems))
        self.assertTrue(any(p.startswith("owner-specific data (email)") for p in problems))
        self.assertTrue(any(p.startswith("owner-specific data (session-url)") for p in problems))

    def test_missing_allowlisted_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as src:
            with self.assertRaises(FileNotFoundError):
                export_public.export(self.dest, source_root=Path(src))

    def test_email_exemption_is_only_attribution_and_rfc2606_reserved(self):
        export_public.export(self.dest)
        (self.dest / "LICENSE").write_text("a@example.com b@example.org noreply@anthropic.com\n")
        self.assertEqual([p for p in export_public.audit(self.dest) if "email" in p], [])
        (self.dest / "LICENSE").write_text("c@gmail.com\n")
        self.assertTrue(any(p.startswith("owner-specific data (email)") for p in export_public.audit(self.dest)))

    def test_fixture_synthetic_token_is_excluded_from_credential_scan_only(self):
        export_public.export(self.dest)
        problems = export_public.audit(self.dest)
        self.assertEqual([p for p in problems if "fixtures" in p], [])


def _pass_tests(root, run_tests):
    return [release.Gate("tests:all", "PASS"), release.Gate("tests:verifier", "PASS")]


def _pass_manifests(root):
    return release.Gate("manifests:valid", "PASS")


def _pass_standalone(dest):
    return release.Gate("export:standalone", "PASS")


class TestReleaseGates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.dest = Path(self._tmp.name) / "exp"

    def tearDown(self):
        self._tmp.cleanup()

    def _check(self, **kw):
        with mock.patch.object(release, "gate_tests", _pass_tests), \
             mock.patch.object(release, "gate_manifests", _pass_manifests), \
             mock.patch.object(release, "gate_standalone", _pass_standalone):
            return release.check(dest=self.dest, **kw)

    def test_current_repo_passes_all_local_gates(self):
        gates, verdict, _ = self._check()
        self.assertTrue(verdict.startswith("RELEASE-CHECKS PASS"), release.render(gates, verdict))

    def test_requested_version_mismatch_blocks(self):
        gates, verdict, _ = self._check(requested_version="9.9.9")
        self.assertTrue(verdict.startswith("RELEASE BLOCKED"))
        self.assertIn("FAIL", {g.state for g in gates if g.name == "version:consistent"})

    def test_any_single_gate_fail_blocks(self):
        with mock.patch.object(release, "gate_tests", _pass_tests), \
             mock.patch.object(release, "gate_manifests", lambda root: release.Gate("manifests:valid", "FAIL", "x")), \
             mock.patch.object(release, "gate_standalone", _pass_standalone):
            gates, verdict, _ = release.check(dest=self.dest)
        self.assertTrue(verdict.startswith("RELEASE BLOCKED"))

    def test_audit_sees_the_tree_after_standalone_ran(self):
        # standalone executes inside the export; anything it leaves behind
        # (e.g. __pycache__) must be caught by the audit, so audit runs after it.
        def leaves_junk(dest):
            (dest / "scripts" / "__pycache__").mkdir()
            (dest / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
            return release.Gate("export:standalone", "PASS")
        with mock.patch.object(release, "gate_tests", _pass_tests), \
             mock.patch.object(release, "gate_manifests", _pass_manifests), \
             mock.patch.object(release, "gate_standalone", leaves_junk):
            gates, verdict, _ = release.check(dest=self.dest)
        self.assertTrue(verdict.startswith("RELEASE BLOCKED"))
        self.assertEqual({g.state for g in gates if g.name == "export:forbidden-absent"}, {"FAIL"})

    def test_skipping_tests_is_itself_a_fail(self):
        gates = release.gate_tests(ROOT, run_tests=False)
        self.assertTrue(all(g.state == "FAIL" for g in gates))

    def test_readme_contract_required_sections(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "README.md").write_text("# x\n\n# FOR AGENTS\n## Installation\n")
            g = release.gate_readme(root)
            self.assertEqual(g.state, "FAIL"); self.assertIn("## Bootstrap", g.detail)
        self.assertEqual(release.gate_readme(ROOT).state, "PASS")

    def test_version_consistency(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1.2.3"}))
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.2] - x\n")
            self.assertEqual(release.gate_version(root, None).state, "FAIL")
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## [1.2.3] - x\n")
            self.assertEqual(release.gate_version(root, None).state, "PASS")
            self.assertEqual(release.gate_version(root, "1.2.3").state, "PASS")
            self.assertEqual(release.gate_version(root, "1.2.4").state, "FAIL")

    def test_publish_without_public_repo_waits(self):
        ok, msg = release.publish(self.dest, "0.1.1", None)
        self.assertFalse(ok); self.assertTrue(msg.startswith("WAITING_FOR_PUBLIC_REPO"))

    def test_publish_to_a_local_bare_repo_end_to_end(self):
        gates, verdict, dest = self._check()
        self.assertTrue(verdict.startswith("RELEASE-CHECKS PASS"))
        with tempfile.TemporaryDirectory() as d:
            bare = Path(d) / "public.git"
            import subprocess
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
            ok, msg = release.publish(dest, release.plugin_version(), str(bare))
            self.assertTrue(ok, msg)
            self.assertEqual(msg, "RELEASED rice " + release.plugin_version())
            tags = subprocess.run(["git", "--git-dir", str(bare), "tag"], capture_output=True, text=True).stdout.split()
            self.assertEqual(tags, ["v" + release.plugin_version()])
            files = subprocess.run(["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main"],
                                   capture_output=True, text=True).stdout.split()
            self.assertEqual(sorted(files), sorted(export_public.ALLOWLIST))
            log = subprocess.run(["git", "--git-dir", str(bare), "rev-list", "--count", "main"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(log, "1")  # fresh single-commit history, no private history

    def test_verdict_strings_only_from_release_script(self):
        for py in (ROOT / "scripts").glob("*.py"):
            if py.name == "release.py":
                continue
            self.assertNotIn("RELEASED rice", py.read_text(encoding="utf-8"), py.name)
            self.assertNotIn("RELEASE-CHECKS PASS", py.read_text(encoding="utf-8"), py.name)


if __name__ == "__main__":
    unittest.main()
