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


if __name__ == "__main__":
    unittest.main()
