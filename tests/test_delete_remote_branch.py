"""Tests for the privileged remote-branch deletion (RICE-BRANCH-001):
scripts/delete_remote_branch.py, and the SessionStart context that tells an
agent in a rice-bootstrapped repo where it lives.

Run: python3 -m unittest tests.test_delete_remote_branch -v
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import delete_remote_branch as drb  # noqa: E402
import evidence  # noqa: E402
import export_public  # noqa: E402
import reconcile  # noqa: E402
import session_context  # noqa: E402
import settings_template  # noqa: E402
import uninstall  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def _make(tmp: Path, default: str = "main") -> Path:
    """A work tree with an `origin` bare remote whose default branch is
    `default`, one commit pushed."""
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", default, str(remote)], check=True)
    work = tmp / "work"; work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", default, str(work)], check=True)
    _git(work, "config", "user.email", "t@example.com"); _git(work, "config", "user.name", "T")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README.md").write_text("x\n"); _git(work, "add", "README.md"); _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "-u", "origin", default)
    return work


def _push_branch(work: Path, name: str) -> None:
    _git(work, "branch", name); _git(work, "push", "-q", "origin", name)


def _remote_heads(work: Path) -> set:
    out = _git(work, "ls-remote", "--heads", "origin")
    return {l.split("\t")[1].removeprefix("refs/heads/") for l in out.splitlines() if l.strip()}


def _run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = drb.main([str(a) for a in argv])
    return rc, out.getvalue(), err.getvalue()


class TestDeleteRemoteBranch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_ordinary_remote_branch_is_deleted_and_only_on_origin(self):
        work = _make(self.tmp); _push_branch(work, "feature")
        rc, out, err = _run(work, "feature")
        self.assertEqual((rc, err), (0, ""), err)
        self.assertIn("RICE-BRANCH-DELETED origin/feature", out)
        self.assertNotIn("feature", _remote_heads(work))
        self.assertEqual(_git(work, "branch", "--list", "feature").strip(), "feature", "local branch untouched")

    def test_an_absent_remote_branch_is_an_idempotent_success(self):
        work = _make(self.tmp); _push_branch(work, "feature")
        _run(work, "feature")
        rc, out, err = _run(work, "feature")
        self.assertEqual(rc, 0, err)
        self.assertIn("RICE-BRANCH-ABSENT origin/feature", out)
        rc, out, _ = _run(work, "never-existed")
        self.assertEqual(rc, 0); self.assertIn("RICE-BRANCH-ABSENT", out)

    def test_origins_default_branch_is_refused(self):
        work = _make(self.tmp, default="trunk")
        rc, out, err = _run(work, "trunk")
        self.assertEqual(rc, 1); self.assertIn("default branch", err); self.assertEqual(out, "")
        self.assertIn("trunk", _remote_heads(work))

    def test_main_and_master_are_refused_even_when_not_the_default(self):
        work = _make(self.tmp, default="trunk")
        for name in ("main", "master"):
            _push_branch(work, name)
            rc, out, err = _run(work, name)
            self.assertEqual(rc, 1, name); self.assertIn("protected", err); self.assertEqual(out, "")
            self.assertIn(name, _remote_heads(work))

    def test_an_invalid_branch_name_is_refused_before_any_git_write(self):
        work = _make(self.tmp)
        before = _remote_heads(work)
        for bad in ("bad..name", "-x", "", "@{-1}", "a b", "x.lock", "HEAD"):
            rc, out, err = _run(work, bad)
            self.assertEqual(rc, 1, bad); self.assertIn("not a valid branch name", err, bad); self.assertEqual(out, "")
        self.assertEqual(_remote_heads(work), before)

    def test_the_current_local_branchs_remote_ref_can_still_go(self):
        work = _make(self.tmp)
        _git(work, "checkout", "-q", "-b", "feature"); _git(work, "push", "-q", "-u", "origin", "feature")
        rc, out, err = _run(work, "feature")
        self.assertEqual(rc, 0, err); self.assertIn("RICE-BRANCH-DELETED origin/feature", out)
        self.assertNotIn("feature", _remote_heads(work))
        self.assertEqual(_git(work, "rev-parse", "--abbrev-ref", "HEAD").strip(), "feature", "still checked out")

    def test_not_a_git_repo_and_unreadable_origin_are_refusals(self):
        rc, out, err = _run(self.tmp, "feature")
        self.assertEqual(rc, 1); self.assertIn("not a git work tree", err)
        work = _make(self.tmp)
        _git(work, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        rc, out, err = _run(work, "feature")
        self.assertEqual(rc, 1); self.assertIn("deleting nothing", err); self.assertEqual(out, "")

    def test_never_forces_and_the_deny_rules_stay_exactly_as_they_were(self):
        src = (REPO / "scripts" / "delete_remote_branch.py").read_text(encoding="utf-8")
        self.assertNotIn("--force", src); self.assertNotIn('"-f"', src); self.assertNotIn("+refs", src)
        for rule in ("Bash(git branch *)", "Bash(git push --delete *)", "Bash(git push * --delete *)",
                     "Bash(git push -d *)", "Bash(git push * -d *)"):
            self.assertIn(rule, settings_template.RICE_DENY_ENTRIES)
        self.assertEqual(len(settings_template.RICE_DENY_ENTRIES), 28)


class TestSessionContext(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, stdin: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), redirect_stdout(out):
            rc = session_context.main()
        return rc, out.getvalue()

    def test_a_rice_repo_gets_exactly_one_line_naming_the_real_helper(self):
        (self.root / ".claude").mkdir(); (self.root / ".claude" / "rice.md").write_text("rules\n")
        self.assertTrue(reconcile.rice_installed_here(self.root))
        rc, out = self._run(json.dumps({"cwd": str(self.root), "hook_event_name": "SessionStart", "source": "resume"}))
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, out)
        self.assertIn(str(session_context.HELPER), lines[0])
        self.assertTrue(session_context.HELPER.exists())
        self.assertIn(str(self.root.resolve()), lines[0])
        self.assertIn("<branch>", lines[0])
        self.assertIn("only the branch the OWNER named", lines[0])
        self.assertNotIn("python3 \"git", lines[0])

    def test_a_repo_without_rice_gets_nothing(self):
        rc, out = self._run(json.dumps({"cwd": str(self.root)}))
        self.assertEqual((rc, out), (0, ""))

    def test_a_malformed_payload_is_silent_never_an_error(self):
        for raw in ("", "not json", "[]", json.dumps({"cwd": str(self.root / "nowhere")})):
            rc, out = self._run(raw)
            self.assertEqual((rc, out), (0, ""), raw)

    def test_the_hook_is_wired_for_every_session_start_and_both_scripts_ship(self):
        item = evidence._hook_wired_item("session-context-hook", "SessionStart", None, "session_context.py")
        self.assertEqual(item.state, "PASS", item.detail)
        # ... without displacing the archive sync on `clear`
        self.assertEqual(evidence._archive_sync_hook_item().state, "PASS")
        for rel in ("scripts/delete_remote_branch.py", "scripts/session_context.py"):
            self.assertIn(rel, export_public.ALLOWLIST)


class TestStalenessLine(unittest.TestCase):
    """RICE-SPEC-001 I18 / §14.4: one more line only when the running rice is
    newer than the version this repo was finalized at; otherwise nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.root = Path(self._tmp.name)
        (self.root / ".claude").mkdir(); (self.root / ".claude" / "rice.md").write_text("rules\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _lines(self, recorded=None, build="0.1.7") -> list[str]:
        if recorded is not None:
            (self.root / verify.VERSION_FILE).write_text(recorded + "\n", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(evidence, "build_version", lambda: build), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"cwd": str(self.root)}))), \
                redirect_stdout(out):
            self.assertEqual(session_context.main(), 0)
        return out.getvalue().splitlines()

    def test_an_older_repo_gets_the_helper_line_then_one_actionable_line(self):
        lines = self._lines("0.1.6")
        self.assertEqual(len(lines), 2, lines)
        self.assertIn(str(session_context.HELPER), lines[0])
        for word in ("0.1.6", "0.1.7", "/rice:bootstrap"):
            self.assertIn(word, lines[1])
        self.assertEqual(len(self._lines("0.1.9", build="0.1.10")), 2)  # numeric, not lexical

    def test_every_other_case_adds_nothing(self):
        cases = {"equal": ("0.1.7", "0.1.7"), "repo newer": ("0.1.8", "0.1.7"),
                 "no version": (None, "0.1.7"), "malformed": ("0.1", "0.1.7"),
                 "prerelease": ("0.1.6-rc1", "0.1.7"), "build unreadable": ("0.1.6", "?")}
        for label, (recorded, build) in cases.items():
            with self.subTest(label):
                vf = self.root / verify.VERSION_FILE
                if vf.exists():
                    vf.unlink()
                self.assertEqual(len(self._lines(recorded, build)), 1, label)

    def test_silent_while_a_bootstrap_waits_for_matt(self):
        reconcile.enter_waiting_for_matt(self.root)
        self.assertEqual(len(self._lines("0.1.6")), 1)

    def test_silent_outside_rice_and_after_uninstall(self):
        (self.root / verify.VERSION_FILE).write_text("0.1.6\n", encoding="utf-8")
        uninstall.uninstall(self.root)
        self.assertEqual(self._lines(), [])
        with tempfile.TemporaryDirectory() as other:
            (Path(other) / "x").write_text("")
            out = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"cwd": other}))), redirect_stdout(out):
                session_context.main()
            self.assertEqual(out.getvalue(), "")

    def test_an_error_in_the_comparison_never_costs_the_helper_line(self):
        with mock.patch.object(verify, "version_order", side_effect=RuntimeError("boom")):
            lines = self._lines("0.1.6")
        self.assertEqual(len(lines), 1)
        self.assertIn(str(session_context.HELPER), lines[0])

    def test_no_hook_wiring_was_added(self):
        hooks = json.loads((Path(__file__).resolve().parents[1] / "hooks" / "hooks.json").read_text())
        self.assertEqual(sum(len(e["hooks"]) for e in hooks["hooks"]["SessionStart"]), 2)


if __name__ == "__main__":
    unittest.main()
