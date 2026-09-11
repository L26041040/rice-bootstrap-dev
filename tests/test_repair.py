"""Tests for scripts/repair.py (RICE-BOOTSTRAP-042): the one deterministic
repair boundary and the pipeline every lifecycle runs through --

    normal path -> verify -> (FAIL) -> repair -> verify again -> (FAIL) -> model

The verifier decides what is broken and whether it is fixed; repair only
acts on the failure identities it names, restores losslessly or refuses,
and never has a verdict of its own.

Run: python3 -m unittest tests.test_repair -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reconcile  # noqa: E402
import repair  # noqa: E402
import rice  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

MATT_SECTION = ("## Agent skills\n\n### Issue tracker\n\nIssues live in GitHub Issues. See `docs/agents/issue-tracker.md`.\n\n"
                "### Domain docs\n\nSingle-context. See `docs/agents/domain.md`.\n")
ISSUE_DOC = "# Issue tracker: GitHub\n\nthe OWNER's answers, as Matt wrote them\n"
DOMAIN_DOC = "# Domain Docs\n\nsingle-context, as Matt wrote it\n"


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(),
                              "scope": "user", "enabled": True}]))


PROBES = {"rtk_probe": _ok_rtk, "plugin_probe": _installed}


def _correct_rice_state(root: Path):
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    s = settings_template.desired_settings("v9.9.9", True)
    (root / ".claude" / "settings.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    (root / ".claude" / "rice.md").write_text(reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "CLAUDE.md").write_text(f"# proj\n\n{reconcile.IMPORT_LINE}\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(reconcile.AGENTS_POINTER, encoding="utf-8")


def _matt_state(root: Path, *, section=True, issue=True, domain=True):
    if section:
        c = root / "CLAUDE.md"
        c.write_text(c.read_text(encoding="utf-8") + "\n" + MATT_SECTION, encoding="utf-8")
    (root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
    if issue:
        (root / "docs" / "agents" / "issue-tracker.md").write_text(ISSUE_DOC, encoding="utf-8")
    if domain:
        (root / "docs" / "agents" / "domain.md").write_text(DOMAIN_DOC, encoding="utf-8")


def _git_denied_transcript(root: Path, ts="2099-01-01T00:00:00.000Z"):
    tdir = reconcile.transcript_dir(root)
    tdir.mkdir(parents=True, exist_ok=True)
    recs = [
        {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git branch --show-current"}}]}},
        {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Permission to use Bash with command git branch --show-current has been denied."}]}},
    ]
    (tdir / "session.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _git(root: Path, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def _commit_all(root: Path, msg="init"):
    if not (root / ".git").exists():
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


class RepairBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    @property
    def version_file(self) -> Path:
        return self.root / verify.VERSION_FILE

    def verify(self, *, final=False):
        return verify.run(self.root, final=final, **PROBES)

    def repair_once(self, *, final=False):
        """What rice's flow does, spelled out: verify, repair what it named,
        verify again. Returns (repair result, the verifier's second word)."""
        done = repair.repair(self.root, self.verify(final=final), **PROBES)
        return done, self.verify(final=final)


class TestRepairActsOnlyOnNamedFailures(RepairBase):
    def test_rice_owned_failures_are_restored_from_canonical_state(self):
        _correct_rice_state(self.root)
        _git_denied_transcript(self.root)
        # three rice-owned failures of three kinds: drift, missing file, missing leaf
        (self.root / ".claude" / "rice.md").write_text("hand-edited\n", encoding="utf-8")
        (self.root / "AGENTS.md").unlink()
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        del s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"]
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")

        done, after = self.repair_once()
        self.assertTrue(after.ok, after.lines)
        self.assertTrue(any(a.startswith("rice.md") for a in done.applied), done.applied)
        self.assertTrue(any(a.startswith("AGENTS.md") for a in done.applied), done.applied)
        self.assertTrue(any(a.startswith("settings:env.CLAUDE_CODE_SUBAGENT_MODEL") for a in done.applied))
        self.assertEqual(done.unresolved, [])
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"),
                         reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))

    def test_nothing_named_nothing_touched(self):
        """Repair re-derives nothing: handed a report with no failures it
        touches no file, however 'improvable' the tree looks."""
        _correct_rice_state(self.root)
        before = _tree(self.root)
        clean = verify.Report(build="x", items=[], gate_a=True, gate_b=None,
                              version_written=None, required=verify.GATE_A)
        result = repair.repair(self.root, clean, **PROBES)
        self.assertEqual((result.applied, result.unresolved), ([], []))
        self.assertEqual(_tree(self.root), before)

    def test_only_the_gate_that_was_evaluated_is_repaired(self):
        """A Gate A report never drags Matt's Gate B artifacts into repair."""
        _correct_rice_state(self.root)
        _git_denied_transcript(self.root)
        done = repair.repair(self.root, self.verify(final=False), **PROBES)
        self.assertEqual(done.applied, [])
        self.assertFalse(any(u.startswith("matt:artifacts") for u in done.unresolved), done.unresolved)

    def test_unrepairable_failures_pass_through_in_the_verifiers_words(self):
        _correct_rice_state(self.root)  # no git-deny evidence: nothing repairs a BEHAVES item
        done, after = self.repair_once()
        self.assertFalse(after.ok)
        self.assertEqual(done.applied, [])
        self.assertTrue(any(u.startswith("git-deny:behaves") for u in done.unresolved))


class TestRepairHasNoVerdict(RepairBase):
    def test_source_carries_no_verdict_and_no_version_write(self):
        src = (REPO / "scripts" / "repair.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"RICE-VERIFIED|FINAL-VERIFIED")
        self.assertNotIn("rice-version", src)
        self.assertNotIn("VERSION_FILE", src)
        self.assertNotIn("MARKER_NAME", src)

    def test_repair_never_evaluates_a_gate(self):
        """RICE-BOOTSTRAP-044: the gate lives in the report it is handed;
        repair neither imports verify nor names a gate."""
        src = (REPO / "scripts" / "repair.py").read_text(encoding="utf-8")
        self.assertNotIn("import verify", src)
        self.assertNotIn("GATE_A", src)
        self.assertNotIn("GATE_B", src)
        self.assertNotIn("gate_passes", src)


class TestMattRepair(RepairBase):
    """Matt's artifacts encode the OWNER's answers to the official setup.
    The only lossless source is this repo's own history; anything else is a
    guess and is refused."""

    def _committed_matt_repo(self):
        _correct_rice_state(self.root)
        _matt_state(self.root)
        _commit_all(self.root)
        _git_denied_transcript(self.root)

    def test_tracked_artifacts_missing_from_the_working_tree_are_restored_byte_for_byte(self):
        self._committed_matt_repo()
        committed = _tree(self.root)
        (self.root / "docs" / "agents" / "domain.md").unlink()
        c = self.root / "CLAUDE.md"
        c.write_text(c.read_text(encoding="utf-8").split("\n## Agent skills")[0], encoding="utf-8")
        from reconcile import enter_waiting_for_matt, is_waiting_for_matt
        enter_waiting_for_matt(self.root)

        done, after = self.repair_once(final=True)
        self.assertTrue(after.ok, after.lines)
        self.assertEqual(len(done.applied), 2, done.applied)
        self.assertTrue(all("from HEAD" in a for a in done.applied))
        now = _tree(self.root)
        self.assertEqual(now["docs/agents/domain.md"], committed["docs/agents/domain.md"])
        self.assertEqual(now["CLAUDE.md"], committed["CLAUDE.md"])
        # and the verifier -- not repair -- finished the job
        self.assertEqual(self.version_file.read_text().strip(), reconcile.build_version())
        self.assertFalse(is_waiting_for_matt(self.root))

    def test_never_committed_artifacts_are_unresolved_never_invented(self):
        _correct_rice_state(self.root)
        _commit_all(self.root)  # history has rice, no Matt
        _git_denied_transcript(self.root)
        _matt_state(self.root, section=False, domain=False)  # Matt ran, left only issue-tracker.md
        before = _tree(self.root)
        done, after = self.repair_once(final=True)
        self.assertFalse(after.ok)
        self.assertEqual(done.applied, [])
        # one explicit line per artifact, by name, and no other unresolved item
        self.assertEqual(len(done.unresolved), 2, done.unresolved)
        joined = "\n".join(done.unresolved)
        self.assertTrue(all(u.startswith("matt:artifacts") and "only the official Matt setup" in u
                            for u in done.unresolved))
        self.assertIn(reconcile.MATT_HEADING_LABEL, joined)
        self.assertIn("docs/agents/domain.md", joined)
        self.assertNotIn("issue-tracker.md", joined)  # the one Matt did write is not "missing"
        self.assertEqual(_tree(self.root), before)  # nothing written, nothing guessed
        self.assertFalse(self.version_file.exists())

    def test_no_git_repo_at_all_is_unresolved_not_an_error(self):
        _correct_rice_state(self.root)
        _git_denied_transcript(self.root)
        _matt_state(self.root, section=False)
        done, after = self.repair_once(final=True)
        self.assertFalse(after.ok)
        self.assertTrue(any(reconcile.MATT_HEADING_LABEL in u for u in done.unresolved))

    def test_restored_section_stops_at_the_next_top_level_heading(self):
        text = "# proj\n\n@.claude/rice.md\n\n## Agent skills\n\n### Issue tracker\n\nx\n\n## Something else\n\ny\n"
        self.assertEqual(repair._section_from(text, "## Agent skills"), "## Agent skills\n\n### Issue tracker\n\nx\n\n")
        self.assertIsNone(repair._section_from("# proj\n", "## Agent skills"))


class TestKnownMattFailuresAreClassified(unittest.TestCase):
    """The classification the ticket asked for, pinned so it cannot drift:
    every Matt artifact has exactly one lossless source (HEAD) and no
    template, no plugin content and no generated text is ever a fallback."""

    def test_no_matt_template_or_plugin_content_is_read(self):
        src = (REPO / "scripts" / "repair.py").read_text(encoding="utf-8")
        for forbidden in ("mattpocock", "setup-matt-pocock-skills", "issue-tracker-github", "installPath", "RICE_MD_RESOURCE"):
            self.assertNotIn(forbidden, src, forbidden)
        # only HEAD is ever consulted
        self.assertIn('"show", f"HEAD:{rel}"', src)
        self.assertIn('"checkout", "HEAD", "--"', src)


if __name__ == "__main__":
    unittest.main()
