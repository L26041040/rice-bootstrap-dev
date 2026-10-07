"""Tests for scripts/upgrade_matrix.py and the release gate built on it
(RICE-UPGRADE-MATRIX-001): every release published on the public repository,
upgraded to the candidate through the real lifecycle, must converge to a
fresh install, and uninstall must leave only the project.

Each "public repository" here is a local bare repo whose `release/<semver>`
branches are exports of this very build -- some doctored to behave like an
older rice that left something behind -- so the matrix runs real builds,
end to end, without the network.

Run: python3 -m unittest tests.test_upgrade_matrix -v
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_public  # noqa: E402
import release  # noqa: E402
import upgrade_matrix  # noqa: E402


def _bare(d: Path) -> str:
    bare = d / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return str(bare)


def _publish(d: Path, public: str, version: str, doctor=None) -> None:
    """`release/<version>` on `public`: this build's export at that version,
    optionally doctored into an older rice that behaves differently."""
    tree = d / f"tree-{version}"
    export_public.export(tree)
    manifest = tree / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if doctor:
        doctor(tree)
    ws = d / f"ws-{version}"
    ws.mkdir()
    ok, msg = release._publish_branch(ws, tree, f"release/{version}", version, public, "")
    assert ok, msg


def _doctor_apply(tree: Path, line: str) -> None:
    """An older rice whose apply also did `line` (in reconcile.apply's body)."""
    p = tree / "scripts" / "reconcile.py"
    src = p.read_text(encoding="utf-8")
    anchor = "    items.append(_agents_md_item(repo_root, write=True, converge_drift=converge_drift))\n    return items\n"
    assert src.count(anchor) == 1
    p.write_text(src.replace(anchor, anchor.replace("    return items\n", f"    {line}\n    return items\n")),
                 encoding="utf-8")


def _verdicts(rows) -> dict:
    return {r.source: r.verdict for r in rows}


class TestEnumeration(unittest.TestCase):
    def test_only_release_semver_branches_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            public = _bare(d)
            seed = d / "seed"
            subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
            (seed / "x").write_text("x\n")
            subprocess.run(["git", "-C", str(seed), "add", "x"], check=True)
            subprocess.run(["git", "-C", str(seed), "-c", "user.email=t@t", "-c", "user.name=t",
                            "commit", "-q", "-m", "x"], check=True)
            for ref in ("main", "release/0.1.10", "release/0.1.9", "release/0.2.0-rc1", "release/notes"):
                subprocess.run(["git", "-C", str(seed), "push", "-q", public, f"HEAD:refs/heads/{ref}"], check=True)
            versions, err = upgrade_matrix.releases(public)
        self.assertIsNone(err)
        self.assertEqual(versions, ["0.1.9", "0.1.10"])

    def test_an_unreachable_public_repository_is_an_error_not_an_empty_matrix(self):
        versions, err = upgrade_matrix.releases("/definitely/not/a/repo")
        self.assertEqual(versions, [])
        self.assertIn("cannot list release branches", err)


class TestMatrix(unittest.TestCase):
    """The real lifecycle, end to end: each historical build bootstraps with
    its own CLI and Stop hook, the candidate re-runs bootstrap, uninstall."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.public = _bare(self.d)
        self.candidate = self.d / "candidate"
        export_public.export(self.candidate)

    def tearDown(self):
        self._tmp.cleanup()

    def run_matrix(self):
        rows, err = upgrade_matrix.run(self.candidate, self.public, self.d / "matrix")
        self.assertIsNone(err)
        return rows

    def test_a_faithful_older_release_and_a_fresh_install_are_clean(self):
        _publish(self.d, self.public, "0.0.1")
        rows = self.run_matrix()
        self.assertEqual(_verdicts(rows), {"fresh": "CLEAN", "release/0.0.1": "CLEAN"},
                         upgrade_matrix.render(rows))
        self.assertIn("UPGRADE-MATRIX CLEAN (2 rows)", upgrade_matrix.render(rows))

    def test_state_an_older_release_wrote_and_the_candidate_keeps_is_legacy_residue(self):
        _publish(self.d, self.public, "0.0.1", doctor=lambda t: _doctor_apply(
            t, '_set_path(settings := _current_settings(repo_root), "env.RICE_OLD_FLAG", "1"); '
               '(repo_root / ".claude" / "settings.json").write_text(json.dumps(settings))'))
        rows = self.run_matrix()
        row = {r.source: r for r in rows}["release/0.0.1"]
        self.assertEqual(row.verdict, "LEGACY RESIDUE", upgrade_matrix.render(rows))
        texts = " | ".join(f.text for f in row.findings)
        self.assertIn("env.RICE_OLD_FLAG='1' left behind", texts)
        self.assertIn("verifier/migration defect", texts)  # the candidate still verified final
        self.assertIn("after uninstall", texts)              # and uninstall cannot take it back either
        self.assertEqual({r.source: r.verdict for r in rows}["fresh"], "CLEAN")
        self.assertIn("UPGRADE-MATRIX NOT CLEAN (1 of 2 rows)", upgrade_matrix.render(rows))

    def test_an_older_release_that_touched_project_bytes_is_project_data_changed(self):
        _publish(self.d, self.public, "0.0.1", doctor=lambda t: _doctor_apply(
            t, '(repo_root / "README.md").write_text("rewritten by rice\\n")'))
        rows = self.run_matrix()
        row = {r.source: r for r in rows}["release/0.0.1"]
        self.assertEqual(row.verdict, "PROJECT DATA CHANGED", upgrade_matrix.render(rows))
        self.assertIn("project file README.md changed", " | ".join(f.text for f in row.findings))

    def test_a_release_that_cannot_finish_its_own_bootstrap_is_blocked(self):
        _publish(self.d, self.public, "0.0.1", doctor=lambda t: (t / "scripts" / "bootstrap_continue.py").write_text(
            "raise SystemExit(0)\n", encoding="utf-8"))
        rows = self.run_matrix()
        self.assertEqual(_verdicts(rows)["release/0.0.1"], "BLOCKED", upgrade_matrix.render(rows))

    def test_the_matrix_never_writes_into_the_candidate(self):
        before = {str(p.relative_to(self.candidate)): p.read_bytes()
                  for p in self.candidate.rglob("*") if p.is_file()}
        self.run_matrix()
        after = {str(p.relative_to(self.candidate)): p.read_bytes()
                 for p in self.candidate.rglob("*") if p.is_file()}
        self.assertEqual(after, before)


class TestReleaseGate(unittest.TestCase):
    def test_the_gate_passes_only_when_every_row_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            public = _bare(d)
            dest = d / "export"
            export_public.export(dest)
            _publish(d, public, "0.0.1")
            self.assertEqual(release.gate_upgrade_matrix(dest, public).state, "PASS")
            _publish(d, public, "0.0.2", doctor=lambda t: _doctor_apply(
                t, '(repo_root / ".claude" / "OLD_RICE_STATE").write_text("x")'))
            gate = release.gate_upgrade_matrix(dest, public)
        self.assertEqual(gate.state, "FAIL")
        self.assertIn("release/0.0.2 LEGACY RESIDUE", gate.detail)

    def test_an_unreachable_public_repository_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "export"
            export_public.export(dest)
            gate = release.gate_upgrade_matrix(dest, "/definitely/not/a/repo")
        self.assertEqual(gate.state, "FAIL")

    def test_check_runs_the_gate_against_the_export_and_the_named_public_repo(self):
        seen = {}

        def fake(dest, public_repo):
            seen["dest"], seen["public"] = Path(dest), public_repo
            return release.Gate("upgrade:matrix", "PASS")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(release, "gate_tests", lambda root, run: [release.Gate("tests:all", "PASS")]), \
                mock.patch.object(release, "gate_standalone", lambda dest: release.Gate("export:standalone", "PASS")), \
                mock.patch.object(release, "gate_upgrade_matrix", fake):
            gates, verdict, dest = release.check(dest=Path(d) / "exp", public_repo="/some/public.git")
        self.assertIn("upgrade:matrix", [g.name for g in gates])
        self.assertEqual(seen, {"dest": Path(d) / "exp", "public": "/some/public.git"})
        self.assertTrue(verdict.startswith("RELEASE-CHECKS PASS"), release.render(gates, verdict))

    def test_the_default_public_repository_is_the_marketplace_repo(self):
        self.assertEqual(upgrade_matrix.PUBLIC_REPO, f"https://github.com/{export_public.MARKETPLACE_REPO}.git")

    def test_the_harness_is_private_release_tooling(self):
        self.assertNotIn("scripts/upgrade_matrix.py", export_public.ALLOWLIST)
        self.assertTrue(any(re.match(p, "scripts/upgrade_matrix.py") for p in export_public.FORBIDDEN_PATTERNS))


if __name__ == "__main__":
    unittest.main()
