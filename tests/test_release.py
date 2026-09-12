"""Tests for scripts/export_public.py and scripts/release.py
(RICE-BOOTSTRAP-023, simplified per RICE-BOOTSTRAP-027, tagless per
RICE-BOOTSTRAP-030): allowlist export, forbidden-path/secret audit, and the
release gates -- any FAIL blocks; only the script emits the verdicts.

Since RICE-BOOTSTRAP-027 the public distribution never ships tests/ or its
fixtures, there is no credential-scan exemption, and standalone validation
is proven from the private side via a runtime import smoke test instead of
running shipped tests inside the export.

Since RICE-BOOTSTRAP-030 no git tag or GitHub release exists anywhere in
the lifecycle, private or public. Release identity is plugin.json version +
CHANGELOG top date + the exact commit SHA (private HEAD for a release cut,
public main's HEAD for what is live); the private SHA is stamped into every
public commit message as provenance. publish() treats public `main`
already equaling the new export as a safe already-published success, never
a "nothing to commit" FAIL.

The gates that shell out (tests, `claude plugin validate`) are exercised
through the real dry-run in CI/manual use; here they are stubbed so the
suite doesn't recurse into itself.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_public  # noqa: E402
import release  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _tree_sha(bare: Path, ref: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), "rev-parse", f"{ref}^{{tree}}"],
                          capture_output=True, text=True).stdout.strip()


def _rev_parse(git_dir: Path, ref: str) -> str:
    return subprocess.run(["git", "--git-dir", str(git_dir), "rev-parse", ref],
                           capture_output=True, text=True, check=True).stdout.strip()


def _ls_tree(git_dir: Path, ref: str) -> list[str]:
    return subprocess.run(["git", "--git-dir", str(git_dir), "ls-tree", "-r", "--name-only", ref],
                           capture_output=True, text=True, check=True).stdout.split()


def _rev_count(git_dir: Path, ref: str) -> str:
    return subprocess.run(["git", "--git-dir", str(git_dir), "rev-list", "--count", ref],
                           capture_output=True, text=True, check=True).stdout.strip()


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

    def test_public_export_excludes_all_tests_and_fixtures(self):
        self.assertFalse(any(rel.startswith("tests/") for rel in export_public.ALLOWLIST))
        export_public.export(self.dest)
        self.assertFalse((self.dest / "tests").exists())

    def test_forbidden_paths_never_in_allowlist(self):
        for rel in export_public.ALLOWLIST:
            self.assertIsNone(export_public.is_forbidden(rel), rel)
        for bad in ("session-history/x.md", ".claude/settings.json", "CLAUDE.md",
                    "docs/agents/domain.md", "scripts/release.py", "scripts/export_public.py",
                    "tests/test_release.py", "tests/fixtures/synthetic_secrets.json",
                    "a/__pycache__/b.pyc", ".claude/skills/rice-release/SKILL.md"):
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

    def test_no_secret_scan_exemption_remains(self):
        self.assertFalse(hasattr(export_public, "SECRET_SCAN_EXCLUDED_PREFIX"))

    def test_secret_scan_has_no_path_exemption(self):
        export_public.export(self.dest)
        (self.dest / "README.md").write_text("slack token xoxb-1234567890-abcdefghijklmn\n")
        problems = export_public.audit(self.dest)
        self.assertTrue(any(p.startswith("credential-shaped string (slack-token)") for p in problems))


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
            # ... and a README that names a release branch other than this
            # build's: a reader copying its install command would get the
            # wrong version (RICE-FINAL-001)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1.2.3"}))
            # a README naming a release branch other than its own: a reader
            # could pin the wrong version from it (RICE-FINAL-002)
            complete = "".join(f"{x}\n" for x in release.README_REQUIRED)
            (root / "README.md").write_text(complete + "release/9.9.9\n")
            self.assertIn("release/9.9.9", release.gate_readme(root).detail)
            # the default install command carries no version at all
            (root / "README.md").write_text(complete.replace(
                f"claude plugin marketplace add {export_public.MARKETPLACE_REPO}\n",
                f"claude plugin marketplace add {export_public.MARKETPLACE_REPO}@release/1.2.3\n"))
            self.assertIn("marketplace add", release.gate_readme(root).detail)
            # ... and may still name its own release, for pinning
            (root / "README.md").write_text(complete + "or pin release/1.2.3\n")
            self.assertEqual(release.gate_readme(root).state, "PASS")
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

    def test_the_default_install_in_the_readme_names_no_version(self):
        """RICE-FINAL-002: an Environment's line must never need editing, so
        the canonical install command carries no release branch."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        default = f"claude plugin marketplace add {export_public.MARKETPLACE_REPO}\n"
        self.assertIn(default, readme)
        block = readme.split("## Installation")[1].split("## Claude Code Web")[0]
        self.assertIn(default, block)
        # ... and the pinned form is still documented, for this version only
        self.assertIn(f"claude plugin marketplace add {export_public.MARKETPLACE_REPO}"
                      f"@{release.release_branch(release.plugin_version())}", block)
        self.assertEqual(sorted(set(re.findall(r"release/\d+\.\d+\.\d+", readme))),
                         [release.release_branch(release.plugin_version())])

    def test_promote_moves_main_onto_an_already_published_release(self):
        """The recovery path, and the only way main moves without a new
        release: it reads the tree from the immutable branch itself."""
        gates, verdict, dest = self._check()
        version = release.plugin_version()
        branch = release.release_branch(version)
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            # a release branch exists, main does not carry it yet
            with tempfile.TemporaryDirectory() as tmp:
                ok, msg = release._publish_branch(Path(tmp), dest, branch, version, str(bare), "a" * 40)
            self.assertTrue(ok, msg)
            released = _rev_parse(bare, branch)

            ok, msg = release.advance_default_branch(version, str(bare), source_sha="a" * 40)
            self.assertTrue(ok, msg)
            self.assertEqual(_tree_sha(bare, "main"), _tree_sha(bare, branch))
            self.assertEqual(_rev_parse(bare, branch), released, "promote touched the release branch")

            # idempotent: main already carries it, nothing to publish
            ok, msg = release.advance_default_branch(version, str(bare), source_sha="a" * 40)
            self.assertTrue(ok, msg)
            self.assertIn("already carries", msg)

    def test_promote_refuses_a_version_that_was_never_released(self):
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            ok, msg = release.advance_default_branch("9.9.9", str(bare))
            self.assertFalse(ok)
            self.assertIn("does not exist", msg)
            self.assertEqual(subprocess.run(["git", "--git-dir", str(bare), "branch", "--list"],
                                            capture_output=True, text=True).stdout.strip(), "")

    def test_main_only_ever_moves_forward(self):
        """RICE-FINAL-002 review: main is the default install source, so
        promoting an older release would silently downgrade everyone."""
        gates, verdict, dest = self._check()
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            old = self._export_as(dest, Path(d) / "e090", "0.9.0")
            new = self._export_as(dest, Path(d) / "e091", "0.9.1")
            self.assertTrue(release.publish(old, "0.9.0", str(bare), source_sha="a" * 40)[0])
            self.assertTrue(release.publish(new, "0.9.1", str(bare), source_sha="b" * 40)[0])
            forward = _rev_parse(bare, "main")

            ok, msg = release.advance_default_branch("0.9.0", str(bare), source_sha="a" * 40)
            self.assertFalse(ok)
            self.assertIn("RELEASE BLOCKED", msg)
            self.assertIn("only", msg)
            self.assertEqual(_rev_parse(bare, "main"), forward, "main was moved backwards")
            self.assertEqual(_tree_sha(bare, "main"), _tree_sha(bare, "release/0.9.1"))

    def test_publish_without_public_repo_waits(self):
        ok, msg = release.publish(self.dest, "0.1.1", None)
        self.assertFalse(ok); self.assertTrue(msg.startswith("WAITING_FOR_PUBLIC_REPO"))

    def _export_as(self, dest: Path, target: Path, version: str) -> Path:
        """A copy of the canonical export whose manifest declares `version` --
        publishing is allowed to assume plugin.json matches (gate_version
        proves it), so a test that publishes under another version must say
        so in the snapshot too."""
        shutil.copytree(dest, target)
        manifest = target / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["version"] = version
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return target

    def _bare(self, d: Path) -> Path:
        bare = Path(d) / "public.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        return bare

    def test_publish_creates_the_release_branch_for_this_version(self):
        gates, verdict, dest = self._check()
        self.assertTrue(verdict.startswith("RELEASE-CHECKS PASS"))
        version = release.plugin_version()
        branch = f"release/{version}"
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            ok, msg = release.publish(dest, version, str(bare))
            self.assertTrue(ok, msg)
            self.assertTrue(msg.startswith(f"RELEASE-PUBLISHED {version}"), msg)
            self.assertIn(branch, msg)
            self.assertEqual(sorted(_ls_tree(bare, branch)), sorted(export_public.ALLOWLIST))
            self.assertEqual(_rev_count(bare, branch), "1")  # fresh history, never private history
            self.assertEqual(subprocess.run(["git", "--git-dir", str(bare), "tag"],
                                            capture_output=True, text=True).stdout.split(), [],
                             "no git tag is ever created (RICE-BOOTSTRAP-030)")
            # ... and main now carries exactly that release (RICE-FINAL-002)
            self.assertEqual(_tree_sha(bare, "main"), _tree_sha(bare, branch))
            self.assertEqual(sorted(_ls_tree(bare, "main")), sorted(export_public.ALLOWLIST))
            self.assertEqual(_rev_count(bare, "main"), "1")  # a first release: main starts here

    def test_publish_embeds_source_provenance_in_commit_message_only(self):
        gates, verdict, dest = self._check()
        sha = "deadbeef" * 5
        version = release.plugin_version()
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            ok, msg = release.publish(dest, version, str(bare), source_sha=sha)
            self.assertTrue(ok, msg)
            commit_msg = subprocess.run(
                ["git", "--git-dir", str(bare), "log", "-1", "--format=%s", f"release/{version}"],
                capture_output=True, text=True).stdout.strip()
            self.assertIn(f"source {sha}", commit_msg)

    def test_an_existing_release_branch_is_never_moved_or_overwritten(self):
        """RICE-FINAL-001: a release is immutable. The same semver can never
        name two different builds, and rice's tooling never force-pushes or
        fast-forwards a branch someone may already be installing from."""
        gates, verdict, dest = self._check()
        version = release.plugin_version()
        branch = f"release/{version}"
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            ok1, msg1 = release.publish(dest, version, str(bare), source_sha="a" * 40)
            self.assertTrue(ok1, msg1)
            published = _rev_parse(bare, branch)

            # same version, different content: refused, and the branch is untouched
            dest2 = self._export_as(dest, Path(d) / "export2", version)
            (dest2 / "LICENSE").write_text("tampered\n", encoding="utf-8")
            ok2, msg2 = release.publish(dest2, version, str(bare), source_sha="b" * 40)
            self.assertFalse(ok2)
            self.assertIn("RELEASE BLOCKED", msg2)
            self.assertIn(branch, msg2)
            self.assertEqual(_rev_parse(bare, branch), published)

            # identical content is refused too: a release happens once
            ok3, msg3 = release.publish(dest, version, str(bare), source_sha="a" * 40)
            self.assertFalse(ok3)
            self.assertIn("RELEASE BLOCKED", msg3)
            self.assertEqual(_rev_parse(bare, branch), published)

    def test_the_next_version_is_a_new_branch_beside_the_old_one(self):
        gates, verdict, dest = self._check()
        with tempfile.TemporaryDirectory() as d:
            bare = self._bare(Path(d))
            first_export = self._export_as(dest, Path(d) / "export090", "0.9.0")
            self.assertTrue(release.publish(first_export, "0.9.0", str(bare), source_sha="a" * 40)[0])
            first = _rev_parse(bare, "release/0.9.0")
            first_main = _rev_parse(bare, "main")

            dest2 = self._export_as(dest, Path(d) / "export091", "0.9.1")
            (dest2 / "LICENSE").unlink()  # this release dropped a file
            ok, msg = release.publish(dest2, "0.9.1", str(bare), source_sha="b" * 40)
            self.assertTrue(ok, msg)

            self.assertEqual(_rev_parse(bare, "release/0.9.0"), first, "an old release moved")
            self.assertIn("LICENSE", _ls_tree(bare, "release/0.9.0"))
            self.assertNotIn("LICENSE", _ls_tree(bare, "release/0.9.1"))
            self.assertEqual(_rev_count(bare, "release/0.9.1"), "1")  # never a child of another release

            # main moved forward onto the new release, keeping its own history
            self.assertEqual(_tree_sha(bare, "main"), _tree_sha(bare, "release/0.9.1"))
            self.assertNotIn("LICENSE", _ls_tree(bare, "main"))
            self.assertEqual(_rev_count(bare, "main"), "2")
            parent = subprocess.run(["git", "--git-dir", str(bare), "log", "--pretty=%P", "-1", "main"],
                                    capture_output=True, text=True).stdout.split()
            self.assertEqual(parent, [first_main], "main was rewritten instead of moved forward")

    def test_verdict_strings_only_from_release_script(self):
        for py in (ROOT / "scripts").glob("*.py"):
            if py.name == "release.py":
                continue
            self.assertNotIn("RELEASED rice", py.read_text(encoding="utf-8"), py.name)
            self.assertNotIn("RELEASE-CHECKS PASS", py.read_text(encoding="utf-8"), py.name)


class TestStandaloneGate(unittest.TestCase):
    """export:standalone must prove the export works on its own without
    shipping tests/ to do it (RICE-BOOTSTRAP-027)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.dest = Path(self._tmp.name) / "standalone"
        export_public.export(self.dest)

    def tearDown(self):
        self._tmp.cleanup()

    def test_standalone_gate_passes_without_shipping_tests(self):
        self.assertFalse((self.dest / "tests").exists())
        gate = release.gate_standalone(self.dest)
        self.assertEqual(gate.state, "PASS", gate.detail)

    def test_standalone_gate_runs_runtime_import_smoke_test(self):
        broken = self.dest / "scripts" / "verify.py"
        broken.write_text("raise RuntimeError('boom')\n")
        gate = release.gate_standalone(self.dest)
        self.assertEqual(gate.state, "FAIL")
        self.assertIn("smoke", gate.detail.lower())


class TestSourceIdentity(unittest.TestCase):
    def test_source_identity_reports_version_changelog_date_and_head_sha_no_tag_involved(self):
        identity = release.source_identity(ROOT)
        self.assertEqual(identity["version"], release.plugin_version(ROOT))
        self.assertRegex(identity["changelog_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(identity["head_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(identity["head_sha"], release._git_head_sha(ROOT))


class TestPublishWorkspaceIsCleanedUp(unittest.TestCase):
    def test_the_scratch_clone_never_outlives_the_call(self):
        """It used to leak a `rice-publish-*` directory on every run."""
        before = set(Path(tempfile.gettempdir()).glob("rice-publish-*"))
        ok, detail = release.publish(Path(tempfile.gettempdir()) / "nonexistent-export",
                                     "0.0.0", public_repo=None)
        self.assertFalse(ok)
        with mock.patch.object(release, "_run", return_value=(1, "no remote")):
            release.publish(Path(tempfile.gettempdir()) / "nonexistent-export", "0.0.0",
                            public_repo="/tmp/definitely-not-a-repo")
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("rice-publish-*")), before)


if __name__ == "__main__":
    unittest.main()
