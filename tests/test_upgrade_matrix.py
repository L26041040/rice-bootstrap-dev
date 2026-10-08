"""Tests for scripts/upgrade_matrix.py and the release gate built on it
(RICE-UPGRADE-MATRIX-001, RICE-UPGRADE-PATH-001): every release published on
the public repository, upgraded to the candidate through the real lifecycle
-- directly, and along its historical path through every later release --
must converge to a fresh install, and uninstall must leave only the project.

Each "public repository" here is a local bare repo whose `release/<semver>`
branches are exports of this very build -- some doctored to behave like an
older rice that left something behind -- so the matrix runs real builds,
end to end, without the network.

Run: python3 -m unittest tests.test_upgrade_matrix -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evidence  # noqa: E402
import export_public  # noqa: E402
import release  # noqa: E402
import settings_template  # noqa: E402
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


def _doctor_settings(tree: Path, mutation: str) -> None:
    """An older rice whose apply also applied `mutation` to the settings dict
    `s` -- written back only when it changed something, as every real build
    writes (a build that rewrote settings.json on every run would send the
    OWNER back to the git probe forever)."""
    _doctor_apply(tree, 's = _current_settings(repo_root); _was = json.dumps(s, sort_keys=True); '
                        f'{mutation}; '
                        'json.dumps(s, sort_keys=True) != _was and '
                        '(repo_root / ".claude" / "settings.json").write_text(json.dumps(s))')


def _doctor_tail(tree: Path, code: str) -> None:
    """A build whose reconcile module ends with `code`: a redefinition there
    replaces the function of that name for every caller."""
    p = tree / "scripts" / "reconcile.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n\n" + code + "\n", encoding="utf-8")


def _declares_project_scope(tree: Path) -> None:
    """Like release/0.1.2 and 0.1.3: writes rice's project-scope declaration
    -- at exactly the shape they wrote -- and does not know it as legacy."""
    _doctor_settings(tree, 's.setdefault("extraKnownMarketplaces", {})[settings_template.MARKETPLACE_NAME] = '
                           '{"source": {"source": "github", "repo": settings_template.MARKETPLACE_REPO, '
                           '"ref": "release/0.0.1"}}; '
                           's.setdefault("enabledPlugins", {})[settings_template.PLUGIN_KEY] = True')
    _doctor_tail(tree, "def _legacy_project_scope_present(d):\n    return False, False")


def _strips_but_leaves_the_containers(tree: Path) -> None:
    """Like release/0.1.4-0.1.6: migrates the declaration away with a bare
    delete, leaving the emptied objects behind."""
    _doctor_tail(tree, "def _take_back(d, dotted):\n    _del_path(d, dotted)")


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

    def run_matrix(self, candidate=None):
        rows, err = upgrade_matrix.run(candidate or self.candidate, self.public, self.d / "matrix")
        self.assertIsNone(err)
        return rows

    def doctored_candidate(self, doctor) -> Path:
        cand = self.d / "doctored-candidate"
        shutil.copytree(self.candidate, cand)
        doctor(cand)
        return cand

    def test_a_faithful_older_release_and_a_fresh_install_are_clean(self):
        _publish(self.d, self.public, "0.0.1")
        rows = self.run_matrix()
        self.assertEqual(_verdicts(rows), {"fresh": "CLEAN", "release/0.0.1": "CLEAN"},
                         upgrade_matrix.render(rows))
        self.assertIn("UPGRADE-MATRIX SAFE (2 rows)", upgrade_matrix.render(rows))

    def test_every_release_but_the_newest_also_walks_its_historical_path(self):
        """RICE-UPGRADE-PATH-001: release -> each later release, in order,
        each hop that release's own lifecycle, then the candidate."""
        for v in ("0.0.1", "0.0.2", "0.0.3"):
            _publish(self.d, self.public, v)
        rows = self.run_matrix()
        self.assertEqual(_verdicts(rows), {
            "fresh": "CLEAN", "release/0.0.1": "CLEAN", "release/0.0.2": "CLEAN", "release/0.0.3": "CLEAN",
            "release/0.0.1 -> 0.0.2 -> 0.0.3": "CLEAN", "release/0.0.2 -> 0.0.3": "CLEAN"},
            upgrade_matrix.render(rows))
        path = {r.source: r for r in rows}["release/0.0.1 -> 0.0.2 -> 0.0.3"]
        candidate = upgrade_matrix.Build.at(self.candidate).version
        self.assertEqual(path.lifecycle["rich"], f"FINAL-VERIFIED 0.0.1 -> 0.0.2 -> 0.0.3 -> {candidate}")

    def test_an_inert_husk_an_intermediate_release_left_is_benign_and_does_not_block(self):
        """The one classified benign (SPEC-001 §15): an empty object at a
        container 0.1.2/0.1.3 created and 0.1.4-0.1.6 emptied. Reported,
        never guessed away, and not a blocker."""
        _publish(self.d, self.public, "0.0.1")
        _publish(self.d, self.public, "0.0.2", doctor=lambda t: _doctor_settings(
            t, 's.setdefault("enabledPlugins", {})'))
        rows = self.run_matrix()
        verdicts = _verdicts(rows)
        self.assertEqual(verdicts["release/0.0.1 -> 0.0.2"], "BENIGN LEGACY RESIDUE", upgrade_matrix.render(rows))
        self.assertEqual(verdicts["release/0.0.2"], "BENIGN LEGACY RESIDUE")
        self.assertEqual(verdicts["release/0.0.1"], "CLEAN")
        self.assertTrue(upgrade_matrix.safe(rows))
        rendered = upgrade_matrix.render(rows)
        self.assertIn("UPGRADE-MATRIX SAFE (4 rows; 2 with benign legacy residue)", rendered)
        self.assertNotIn("verifier/migration defect", rendered)  # benign never counts as the verifier's fault

    def test_a_husk_the_candidate_itself_emptied_is_harmful_not_benign(self):
        """SPEC-001 I23 (1): benign needs provenance no build can prove. An
        object the candidate's own migration emptied is the candidate's to
        remove -- the matrix must catch a candidate that regressed to a
        bare delete, which is exactly the defect RICE-UPGRADE-MATRIX-001
        fixed."""
        _publish(self.d, self.public, "0.0.1", doctor=_declares_project_scope)
        self.assertEqual(_verdicts(self.run_matrix())["release/0.0.1"], "CLEAN")
        shutil.rmtree(self.d / "matrix")
        rows = self.run_matrix(self.doctored_candidate(_strips_but_leaves_the_containers))
        row = {r.source: r for r in rows}["release/0.0.1"]
        self.assertEqual(row.verdict, "HARMFUL RESIDUE", upgrade_matrix.render(rows))
        texts = " | ".join(f.text for f in row.findings)
        self.assertIn("enabledPlugins={} left behind (a fresh install has no such key)", texts)
        self.assertIn("verifier/migration defect", texts)
        self.assertFalse(upgrade_matrix.safe(rows))

    def test_a_husk_an_earlier_release_left_is_benign_only_along_the_path_that_left_it(self):
        """The real population: a 0.1.2/0.1.3 repo migrated by 0.1.4-0.1.6
        reaches the candidate with the emptied objects already there. The
        candidate cannot prove them rice's, so it leaves them; the matrix
        reports benign, and the direct rows of both releases stay clean."""
        _publish(self.d, self.public, "0.0.1", doctor=_declares_project_scope)
        _publish(self.d, self.public, "0.0.2", doctor=_strips_but_leaves_the_containers)
        rows = self.run_matrix()
        self.assertEqual(_verdicts(rows), {
            "fresh": "CLEAN", "release/0.0.1": "CLEAN", "release/0.0.2": "CLEAN",
            "release/0.0.1 -> 0.0.2": "BENIGN LEGACY RESIDUE"}, upgrade_matrix.render(rows))
        texts = " | ".join(f.text for f in {r.source: r for r in rows}["release/0.0.1 -> 0.0.2"].findings)
        self.assertIn("already empty when the candidate arrived", texts)
        self.assertTrue(upgrade_matrix.safe(rows))

    def test_a_candidate_that_did_nothing_is_never_credited_with_an_earlier_hops_stamp(self):
        """A release at the candidate's own version already stamped it; a
        candidate whose run prints no verdict of its own is BLOCKED, not
        passed on that stamp."""
        _publish(self.d, self.public, upgrade_matrix.Build.at(self.candidate).version)
        silent = self.doctored_candidate(lambda c: (c / "scripts" / "rice.py").write_text(
            "raise SystemExit(0)\n", encoding="utf-8"))
        rows = self.run_matrix(silent)
        row = {r.source: r for r in rows}[settings_template.release_ref(upgrade_matrix.Build.at(self.candidate).version)]
        self.assertEqual(row.verdict, "BLOCKED", upgrade_matrix.render(rows))
        self.assertIn("did not reach FINAL-VERIFIED", row.lifecycle["bare"])

    def test_benign_is_exactly_the_known_husks_and_nothing_else(self):
        _publish(self.d, self.public, "0.0.1", doctor=lambda t: _doctor_settings(t, 's.setdefault("hooks", {})'))
        rows = self.run_matrix()
        self.assertEqual(_verdicts(rows)["release/0.0.1"], "HARMFUL RESIDUE", upgrade_matrix.render(rows))
        self.assertFalse(upgrade_matrix.safe(rows))

    def test_an_intermediate_hop_that_cannot_finish_blocks_and_names_the_hop(self):
        _publish(self.d, self.public, "0.0.1")
        _publish(self.d, self.public, "0.0.2", doctor=lambda t: (t / "scripts" / "rice.py").write_text(
            "raise SystemExit(1)\n", encoding="utf-8"))
        rows = self.run_matrix()
        verdicts = _verdicts(rows)
        self.assertEqual(verdicts["release/0.0.1 -> 0.0.2"], "BLOCKED", upgrade_matrix.render(rows))
        self.assertEqual(verdicts["release/0.0.1"], "CLEAN")
        path = {r.source: r for r in rows}["release/0.0.1 -> 0.0.2"]
        self.assertIn("rice 0.0.2 did not reach FINAL-VERIFIED", path.lifecycle["bare"])

    def test_state_an_older_release_wrote_and_the_candidate_keeps_is_harmful_residue(self):
        _publish(self.d, self.public, "0.0.1", doctor=lambda t: _doctor_settings(
            t, '_set_path(s, "env.RICE_OLD_FLAG", "1")'))
        rows = self.run_matrix()
        row = {r.source: r for r in rows}["release/0.0.1"]
        self.assertEqual(row.verdict, "HARMFUL RESIDUE", upgrade_matrix.render(rows))
        texts = " | ".join(f.text for f in row.findings)
        self.assertIn("env.RICE_OLD_FLAG='1' left behind", texts)
        self.assertIn("verifier/migration defect", texts)  # the candidate still verified final
        self.assertIn("after uninstall", texts)              # and uninstall cannot take it back either
        self.assertEqual({r.source: r.verdict for r in rows}["fresh"], "CLEAN")
        self.assertIn("UPGRADE-MATRIX NOT SAFE (1 of 2 rows block)", upgrade_matrix.render(rows))

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


class TestProbeRecord(unittest.TestCase):
    def test_the_denial_record_is_dated_now_so_a_later_settings_write_needs_a_new_one(self):
        """Each hop that rewrites settings.json must ask for the git probe
        again, as a real session would; the record is never ahead of the
        clock."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            sb = upgrade_matrix.Sandbox(d / "sb", upgrade_matrix._tools(d))
            settings = sb.work / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(sb.cfg)}):
                sb.deny_git_probe()
                self.assertEqual(evidence._git_deny_evidence_item(sb.work).state, "PASS")
                time.sleep(0.01)
                settings.write_text('{"a": 1}', encoding="utf-8")
                self.assertEqual(evidence._git_deny_evidence_item(sb.work).state, "NOT_VERIFIED")
                sb.deny_git_probe()
                self.assertEqual(evidence._git_deny_evidence_item(sb.work).state, "PASS")


class TestReleaseGate(unittest.TestCase):
    def test_the_gate_passes_only_while_no_row_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            public = _bare(d)
            dest = d / "export"
            export_public.export(dest)
            _publish(d, public, "0.0.1")
            self.assertEqual(release.gate_upgrade_matrix(dest, public).state, "PASS")
            # benign residue passes, and is named
            _publish(d, public, "0.0.2", doctor=lambda t: _doctor_settings(
                t, 's.setdefault("extraKnownMarketplaces", {})'))
            gate = release.gate_upgrade_matrix(dest, public)
            self.assertEqual(gate.state, "PASS", gate.detail)
            self.assertIn("release/0.0.1 -> 0.0.2 BENIGN LEGACY RESIDUE", gate.detail)
            # harmful residue on any row fails
            _publish(d, public, "0.0.3", doctor=lambda t: _doctor_apply(
                t, '(repo_root / ".claude" / "OLD_RICE_STATE").write_text("x")'))
            gate = release.gate_upgrade_matrix(dest, public)
        self.assertEqual(gate.state, "FAIL")
        self.assertIn("release/0.0.3 HARMFUL RESIDUE", gate.detail)
        self.assertIn("release/0.0.1 -> 0.0.2 -> 0.0.3 HARMFUL RESIDUE", gate.detail)

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
