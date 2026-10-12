"""Tests for scripts/rice.py (RICE-BOOTSTRAP-044): the lifecycle engine.

One module owns the ORDER of a rice lifecycle:

    normal path -> verify -> PASS: done
                          -> FAIL: deterministic repair -> verify again
                                -> still FAIL: the model, last

and the one ordering decision that is genuinely lifecycle: whether this repo
must wait for the OWNER's Matt setup, or can finish now. Correctness belongs
to verify.py, restores to repair.py, desired state to reconcile.py -- the
tests below pin that this module keeps none of it.

Run: python3 -m unittest tests.test_rice -v
"""
import contextlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evidence  # noqa: E402
import export_public  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import rice  # noqa: E402
import session_context  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF = settings_template.release_ref()


class _R:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _missing_rtk(cmd):
    raise FileNotFoundError("rtk")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": evidence.build_version(),
                              "scope": "user", "enabled": True}]))


PROBES = {"rtk_probe": _ok_rtk, "plugin_probe": _installed}


MATT_SECTION = "## Agent skills\n\n### Issue tracker\n\nGitHub Issues. See `docs/agents/issue-tracker.md`.\n"


def _git_denied_transcript(root: Path, ts="2099-01-01T00:00:00.000Z"):
    """The harness-recorded denial Gate A's git-deny:behaves item reads."""
    tdir = evidence.transcript_dir(root)
    tdir.mkdir(parents=True, exist_ok=True)
    recs = [
        {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git branch --show-current"}}]}},
        {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "Permission to use Bash with command git branch --show-current has been denied."}]}},
    ]
    (tdir / "session.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _matt_artifacts(root: Path):
    c = root / "CLAUDE.md"
    c.write_text((c.read_text(encoding="utf-8") if c.exists() else "") + "\n" + MATT_SECTION, encoding="utf-8")
    (root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "agents" / "issue-tracker.md").write_text("owner answers\n", encoding="utf-8")
    (root / "docs" / "agents" / "domain.md").write_text("owner answers\n", encoding="utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


class LifecycleBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")})
        self._env.start()
        _git_denied_transcript(self.root)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    @property
    def version_file(self) -> Path:
        return self.root / verify.VERSION_FILE

    def bootstrap(self, **kw):
        kw.setdefault("archive", True)
        kw.setdefault("output_style", None)
        return rice.bootstrap(self.root, **kw, **PROBES)


class TestCanonicalFlow(LifecycleBase):
    def _verify_spy(self):
        """Every Gate verdict this flow produced, in order. `bootstrap` opens
        with a read-only "is rice loaded here" run, which is not part of the
        canonical flow -- callers drop it with [1:]."""
        seen, real_run = [], verify.run

        def counting(*a, **kw):
            report = real_run(*a, **kw)
            seen.append(report.ok)
            return report

        return seen, mock.patch.object(verify, "run", side_effect=counting)

    def test_normal_path_pass_never_reaches_repair(self):
        _matt_artifacts(self.root)
        self.bootstrap()  # leaves a fully correct, finalized repo behind
        with mock.patch.object(repair, "repair", side_effect=AssertionError("repair must not run")) as spy:
            outcome = self.bootstrap()
        spy.assert_not_called()
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertIsNone(outcome.repaired)
        self.assertIn("not needed", "\n".join(outcome.lines))

    def test_fail_goes_through_repair_and_is_re_verified(self):
        self.bootstrap()
        # drift an owned artifact: the normal path (apply, never overwriting
        # outside an update) reports a conflict, so only repair converges it
        (self.root / ".claude" / "rice.md").write_text("hand-edited drift\n", encoding="utf-8")
        (self.root / "AGENTS.md").unlink()
        seen, spy = self._verify_spy()
        with spy:
            outcome = self.bootstrap()
        self.assertEqual(seen[1:3], [False, True], seen)  # verify -> repair -> verify
        self.assertTrue(outcome.ok)
        self.assertIsNotNone(outcome.repaired)
        self.assertTrue(any("rice.md" in a for a in outcome.repaired.applied), outcome.repaired.applied)
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"),
                         reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))

    def test_still_failing_after_repair_is_left_for_the_model(self):
        self.bootstrap()
        shutil.rmtree(evidence.transcript_dir(self.root))  # git-deny evidence: nothing repairs it
        seen, spy = self._verify_spy()
        with spy:
            outcome = self.bootstrap()
        self.assertEqual(seen[1:], [False, False], seen)  # verified twice, still failing
        self.assertFalse(outcome.ok)
        self.assertIsNotNone(outcome.repaired)
        self.assertTrue(any(r.startswith("git-deny:behaves") for r in outcome.report.reasons), outcome.report.reasons)

    def test_the_flow_exists_as_code_in_exactly_one_place(self):
        """RICE-BOOTSTRAP-044: no adapter re-implements verify -> repair ->
        verify, and repair is never called from outside the engine."""
        callers = [p for p in (REPO / "scripts").glob("*.py") if p.name not in ("rice.py", "repair.py")]
        for py in callers:
            self.assertNotIn("repair.repair(", py.read_text(encoding="utf-8"), f"{py.name} calls repair directly")


class TestSingleInvocation(LifecycleBase):
    """The OWNER invokes /rice:bootstrap exactly once (RICE-BOOTSTRAP-028)."""

    def test_fresh_bootstrap_reaches_gate_a_and_parks_for_matt(self):
        outcome = self.bootstrap()
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertTrue(outcome.waiting_for_matt)
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))
        self.assertFalse(self.version_file.exists())  # Gate A never stamps

    def test_the_owners_matt_setup_is_continued_without_a_second_invocation(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertEqual(self.version_file.read_text().strip(), evidence.build_version())
        self.assertFalse(reconcile.is_waiting_for_matt(self.root))

    def test_matt_already_complete_finishes_inside_the_one_invocation(self):
        _matt_artifacts(self.root)
        outcome = self.bootstrap()
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertFalse(outcome.waiting_for_matt)  # never ask for a setup already done
        self.assertEqual(self.version_file.read_text().strip(), evidence.build_version())
        self.assertFalse(reconcile.is_waiting_for_matt(self.root))

    def test_gate_a_failure_does_not_park_the_repo(self):
        shutil.rmtree(evidence.transcript_dir(self.root))
        outcome = self.bootstrap()
        self.assertFalse(outcome.ok)
        self.assertFalse(reconcile.is_waiting_for_matt(self.root))

    def test_continue_is_silent_until_there_is_something_to_continue(self):
        self.assertIsNone(rice.continue_after_matt(self.root, **PROBES))  # no marker
        self.bootstrap()
        self.assertIsNone(rice.continue_after_matt(self.root, **PROBES))  # Matt has not started
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_incomplete_matt_output_is_reported_not_silently_pending(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        (self.root / "docs" / "agents" / "domain.md").unlink()  # never committed: not reconstructible
        outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertIsNotNone(outcome)
        self.assertFalse(outcome.ok)
        msg = rice.continuation_message(self.root, outcome)
        self.assertIn("docs/agents/domain.md", msg)
        self.assertIn("only the official Matt setup", msg)
        self.assertFalse((self.root / "docs" / "agents" / "domain.md").exists())  # nothing invented


class TestUnknownIsNeverAFact(LifecycleBase):
    """A probe that could not answer is not an answer. Every one of these was
    a real defect in the first draft of the stage machine."""

    def _cli_unavailable(self, cmd):
        raise FileNotFoundError("claude")

    def test_an_undeterminable_install_is_not_reported_as_rice_missing(self):
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, archive=True, output_style=None,
                                 rtk_probe=_ok_rtk, plugin_probe=self._cli_unavailable)
        self.assertTrue(outcome.blocked)
        directive = "\n".join(outcome.directive)
        self.assertIn("could not be determined", directive)
        self.assertNotIn("claude plugin marketplace add", directive)  # never tell them to install
        self.assertEqual(_tree(self.root), before)

    def test_a_stage_that_never_verified_claims_no_repair(self):
        def not_installed(cmd):
            return _R(0, "[]")

        outcome = rice.bootstrap(self.root, archive=True, output_style=None,
                                 rtk_probe=_ok_rtk, plugin_probe=not_installed)
        self.assertNotIn("repair: not needed", "\n".join(outcome.lines))


def _entries(*pairs):
    """A `claude plugin list --json` probe for the given (scope, version)s."""
    def probe(cmd):
        return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": v, "scope": sc,
                                  "enabled": True} for sc, v in pairs]))
    return probe


class GitLifecycleBase(LifecycleBase):
    """A real git work tree with a bare `origin` it tracks; the harness's own
    state (git-deny evidence) lives outside the tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.root, self.origin = tmp / "work", tmp / "origin.git"
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp / "cfg")})
        self._env.start()
        _git_denied_transcript(self.root)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.root.mkdir(exist_ok=True)
        for args in (("init", "-q", "-b", "main"), ("config", "user.email", "o@example.com"),
                     ("config", "user.name", "OWNER"), ("remote", "add", "origin", str(self.origin))):
            self.git(*args)
        (self.root / "README.md").write_text("project\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        self.git("add", "-A"); self.git("commit", "-q", "-m", "init"); self.git("push", "-q", "-u", "origin", "main")

    def git(self, *args) -> str:
        r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"git {args}: {r.stderr}")
        return r.stdout.strip()

    def origin_head(self, branch="main") -> str:
        r = subprocess.run(["git", "-C", str(self.origin), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                           capture_output=True, text=True)
        return r.stdout.strip()

    def finalized_by_an_older_rice(self):
        """Bootstrapped, finalized, committed and pushed -- then stamped as an
        older build's, the way a repo looks when a new rice arrives."""
        _matt_artifacts(self.root)
        self.assertTrue(self.bootstrap().ok)
        (self.root / ".claude" / "rice.md").write_text("an older build's rules\n", encoding="utf-8")
        self.version_file.write_text("0.0.1\n", encoding="utf-8")
        self.git("add", "-A"); self.git("commit", "-q", "-m", "rice 0.0.1"); self.git("push", "-q")

    def owner_wip(self):
        (self.root / "README.md").write_text("project\nhalf-done edit\n", encoding="utf-8")
        (self.root / "notes.md").write_text("new file\n", encoding="utf-8")
        (self.root / "debug.log").write_text("ignored\n", encoding="utf-8")
        (self.root / "logo.bin").write_bytes(b"\x89PNG\x00\xff\xfe")   # not text: never decoded


class TestAnUpdateSavesTheWorkFirst(GitLifecycleBase):
    """RICE-UPDATE-PRESAVE-001: the OWNER running /rice:bootstrap to update is
    their authorization to commit and push everything first -- only on a real
    update (older -> newer), only after this session may change the repo at
    all, and a failure stops the update with nothing of rice's written."""

    def test_a_dirty_repo_is_committed_and_pushed_before_rice_writes(self):
        self.finalized_by_an_older_rice()
        before = self.git("rev-parse", "HEAD")
        self.owner_wip()
        outcome = rice.bootstrap(self.root, **PROBES)  # no answers: an update asks nothing
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        saved = self.git("rev-parse", "HEAD")  # rice's own changes are the agent's to commit, after
        self.assertEqual(self.git("rev-parse", f"{saved}^"), before)
        files = set(self.git("show", "--name-only", "--format=", saved).split())
        self.assertEqual(files, {"README.md", "notes.md", "logo.bin"})     # .gitignore respected
        self.assertEqual(self.git("show", f"{saved}:.claude/rice.md"), "an older build's rules")  # before rice
        self.assertEqual(self.origin_head(), saved)
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"),
                         reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))
        self.assertIn(saved[:7], "\n".join(outcome.directive))
        self.assertIn("debug.log", self.git("status", "--short", "--ignored"))  # never staged

    def test_an_older_finalized_repo_is_brought_up_to_date_asking_nothing(self):
        self.finalized_by_an_older_rice()
        outcome = rice.bootstrap(self.root, **PROBES)  # no OWNER answers passed
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertEqual(outcome.stage, "finalized")
        self.assertEqual(self.version_file.read_text().strip(), evidence.build_version())
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"),
                         reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))

    def test_a_clean_repo_in_sync_gets_no_commit(self):
        self.finalized_by_an_older_rice()
        before = self.git("rev-parse", "HEAD")
        outcome = rice.bootstrap(self.root, **PROBES)
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.origin_head(), before)

    def test_unpushed_commits_are_pushed(self):
        self.finalized_by_an_older_rice()
        (self.root / "feature.py").write_text("x = 1\n", encoding="utf-8")
        self.git("add", "feature.py"); self.git("commit", "-q", "-m", "local work")
        local = self.git("rev-parse", "HEAD")
        outcome = rice.bootstrap(self.root, **PROBES)
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self.assertEqual(self.git("rev-parse", "HEAD"), local)   # nothing new to commit
        self.assertEqual(self.origin_head(), local)

    def test_a_branch_without_an_upstream_is_published_under_its_own_name(self):
        self.finalized_by_an_older_rice()
        self.git("checkout", "-q", "-b", "claude/session-x")   # a platform session branch, never pushed
        outcome = rice.bootstrap(self.root, **PROBES)
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self.assertEqual(self.origin_head("claude/session-x"), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "@{upstream}"), "origin/claude/session-x")

    def test_a_branch_tracking_another_name_is_never_pushed_onto_it(self):
        """git's own default (push.default=simple) refuses a push whose
        upstream has another name; so does the save -- a `feature` tracking
        `origin/main` must not land the OWNER's work-in-progress on main."""
        self.finalized_by_an_older_rice()
        self.git("checkout", "-q", "-b", "feature", "--track", "origin/main")
        main_before = self.origin_head()
        self.owner_wip()
        outcome = rice.bootstrap(self.root, **PROBES)
        self._refused_with_nothing_of_rices_written(outcome)
        self.assertIn("origin/main", "\n".join(outcome.directive))
        self.assertEqual(self.origin_head(), main_before)
        self.assertEqual(self.origin_head("feature"), "")
        self.assertIn("?? notes.md", self.git("status", "--short"))   # nothing committed either

    def test_a_later_run_of_the_same_update_saves_no_rice_write_as_the_owners(self):
        """In a new session the first run saves, writes, then stops for the
        git-deny probe; the model runs it and re-runs. That second run must not
        commit rice's own half-written update as "your changes" (a real
        session did), and it still tells the OWNER their work was saved."""
        self.finalized_by_an_older_rice()
        self.owner_wip()
        stop, saved = rice._save_before_update(self.root)       # the first run's save ...
        self.assertIsNone(stop)
        first = self.git("rev-parse", "--short", "HEAD")
        reconcile.apply(self.root)                               # ... and its writes, before Gate A stopped
        outcome = rice.bootstrap(self.root, **PROBES)            # the re-run
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self.assertEqual(self.git("rev-parse", "--short", "HEAD"), first)   # no second save commit
        self.assertIn(f"committed as {first}", "\n".join(outcome.directive))
        self.assertEqual(set(self.git("diff", "--name-only", "HEAD").split()),
                         {".claude/rice.md", verify.VERSION_FILE})          # left for rice's own commit

    def test_an_owners_edit_to_a_shared_file_is_saved_with_rices_entries_left_alone(self):
        self.finalized_by_an_older_rice()
        p = self.root / ".claude" / "settings.json"
        s = json.loads(p.read_text(encoding="utf-8")); s.setdefault("permissions", {})["allow"] = ["Bash(make test)"]
        p.write_text(json.dumps(s), encoding="utf-8")
        (self.root / ".claude" / "rice.md").write_text("the OWNER's hand edit\n", encoding="utf-8")
        before = self.git("rev-parse", "HEAD")
        self.assertEqual(rice.bootstrap(self.root, **PROBES).stage, "finalized")
        saved = self.git("rev-list", f"{before}..HEAD").split()
        self.assertEqual(len(saved), 1)
        self.assertEqual(set(self.git("show", "--name-only", "--format=", saved[0]).split()),
                         {".claude/settings.json", ".claude/rice.md"})      # both are the OWNER's edits
        self.assertEqual(self.git("show", f"{saved[0]}:.claude/rice.md"), "the OWNER's hand edit")

    def test_a_merge_in_progress_is_the_owners_to_finish(self):
        self.finalized_by_an_older_rice()
        self.git("checkout", "-q", "-b", "side"); (self.root / "README.md").write_text("side\n", encoding="utf-8")
        self.git("commit", "-qam", "side"); self.git("checkout", "-q", "main")
        (self.root / "README.md").write_text("main\n", encoding="utf-8"); self.git("commit", "-qam", "main")
        subprocess.run(["git", "-C", str(self.root), "merge", "-q", "side"], capture_output=True)  # conflicts
        before = self.git("rev-parse", "HEAD")
        outcome = rice.bootstrap(self.root, **PROBES)
        self._refused_with_nothing_of_rices_written(outcome)
        self.assertIn("merge", "\n".join(outcome.directive))
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertTrue((self.root / ".git" / "MERGE_HEAD").exists())          # the merge is still theirs

    def test_a_failed_commit_gives_back_the_owners_own_staging(self):
        self.finalized_by_an_older_rice()
        (self.root / "README.md").write_text("staged version\n", encoding="utf-8"); self.git("add", "README.md")
        (self.root / "README.md").write_text("working version\n", encoding="utf-8")
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n"); hook.chmod(0o755)
        self._refused_with_nothing_of_rices_written(rice.bootstrap(self.root, **PROBES))
        self.assertEqual(self.git("show", ":README.md"), "staged version")      # the index as they left it
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "working version\n")

    def test_a_rejected_push_says_why(self):
        self.finalized_by_an_older_rice()
        other = Path(self._tmp.name) / "other"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(other)], check=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.email=x@y", "-c", "user.name=x", "commit", "-q",
                        "--allow-empty", "-m", "someone else's"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
        self.owner_wip()
        outcome = rice.bootstrap(self.root, **PROBES)
        self._refused_with_nothing_of_rices_written(outcome)
        text = "\n".join(outcome.directive)
        self.assertIn("rejected", text)
        self.assertNotIn("hint:", text)

    def test_no_remote_to_push_to_says_what_to_do(self):
        self.finalized_by_an_older_rice()
        self.git("remote", "remove", "origin")
        self.owner_wip()
        outcome = rice.bootstrap(self.root, **PROBES)
        self._refused_with_nothing_of_rices_written(outcome)
        self.assertIn("git remote add", "\n".join(outcome.directive))

    def test_the_push_goes_where_git_itself_would_push(self):
        self.finalized_by_an_older_rice()
        fork = Path(self._tmp.name) / "fork.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(fork)], check=True)
        self.git("remote", "add", "fork", str(fork)); self.git("config", "remote.pushDefault", "fork")
        before = self.origin_head()
        self.owner_wip()
        self.assertEqual(rice.bootstrap(self.root, **PROBES).stage, "finalized")
        self.assertEqual(self.origin_head(), before)                             # not origin ...
        r = subprocess.run(["git", "-C", str(fork), "rev-parse", "main"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), self.git("rev-parse", "HEAD"))         # ... the fork

    def test_the_stop_continuation_saves_nothing(self):
        self.finalized_by_an_older_rice()
        reconcile.enter_waiting_for_matt(self.root)
        self.owner_wip()
        before = self.git("rev-parse", "HEAD")
        rice.continue_after_matt(self.root, **PROBES)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("?? notes.md", self.git("status", "--short"))

    def test_the_saved_line_leads_a_matt_hand_off_too(self):
        self.finalized_by_an_older_rice()
        for rel in ("docs/agents/issue-tracker.md", "docs/agents/domain.md"):
            (self.root / rel).unlink()
        self.git("commit", "-qam", "matt docs gone"); self.git("push", "-q")
        self.owner_wip()
        outcome = rice.bootstrap(self.root, **PROBES)
        self.assertEqual(outcome.stage, "waiting-for-matt", outcome.lines)
        self.assertTrue(outcome.directive[0].startswith("Before updating rice"), outcome.directive)

    def _refused_with_nothing_of_rices_written(self, outcome):
        self.assertEqual(outcome.stage, "blocked")
        self.assertTrue(outcome.blocked)
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"), "an older build's rules\n")
        self.assertEqual(self.version_file.read_text(encoding="utf-8"), "0.0.1\n")
        self.assertIn("nothing of rice's was written", "\n".join(outcome.directive))

    def test_a_failed_push_stops_the_update_and_keeps_the_work(self):
        self.finalized_by_an_older_rice()
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho rejected by origin >&2\nexit 1\n"); hook.chmod(0o755)
        self.owner_wip()
        outcome = rice.bootstrap(self.root, **PROBES)
        self._refused_with_nothing_of_rices_written(outcome)
        self.assertIn("rejected by origin", "\n".join(outcome.directive))
        self.assertEqual(self.git("show", "HEAD:notes.md"), "new file")  # kept, in a local commit
        self.assertEqual(self.git("status", "--short"), "")
        hook.unlink()                                       # the OWNER fixes origin and re-runs
        again = rice.bootstrap(self.root, **PROBES)
        self.assertEqual(again.stage, "finalized", again.lines)
        self.assertEqual(self.origin_head(), self.git("rev-parse", "HEAD"))

    def test_a_failed_commit_stops_the_update_and_leaves_the_tree_as_it_was(self):
        self.finalized_by_an_older_rice()
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho lint failed >&2\nexit 1\n"); hook.chmod(0o755)
        self.owner_wip()
        before = self.git("rev-parse", "HEAD")
        outcome = rice.bootstrap(self.root, **PROBES)
        self._refused_with_nothing_of_rices_written(outcome)
        self.assertIn("lint failed", "\n".join(outcome.directive))
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")   # nothing left staged
        self.assertEqual(self.git("status", "--porcelain", "README.md", "notes.md").splitlines(),
                         ["M README.md", "?? notes.md"])                       # first line's space is stripped

    def test_a_detached_head_stops_the_update(self):
        self.finalized_by_an_older_rice()
        self.git("checkout", "-q", "--detach")
        self._refused_with_nothing_of_rices_written(rice.bootstrap(self.root, **PROBES))

    def test_a_first_install_saves_nothing(self):
        self.owner_wip()
        before = self.git("rev-parse", "HEAD")
        self.bootstrap()
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("?? notes.md", self.git("status", "--short"))

    def test_a_same_version_re_run_saves_nothing(self):
        _matt_artifacts(self.root)
        self.assertTrue(self.bootstrap().ok)
        self.git("add", "-A"); self.git("commit", "-q", "-m", "rice"); self.git("push", "-q")
        self.owner_wip()
        before = self.git("rev-parse", "HEAD")
        self.assertEqual(rice.bootstrap(self.root, **PROBES).stage, "finalized")
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("?? notes.md", self.git("status", "--short"))

    def _refused_before_any_save(self, outcome, before):
        self.assertTrue(outcome.blocked)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual(self.origin_head(), before)
        self.assertIn("?? notes.md", self.git("status", "--short"))

    def test_a_newer_repo_is_refused_before_any_save(self):
        self.finalized_by_an_older_rice()
        self.version_file.write_text("9.9.9\n", encoding="utf-8")
        self.git("commit", "-q", "-am", "rice 9.9.9"); self.git("push", "-q")
        self.owner_wip()
        self._refused_before_any_save(rice.bootstrap(self.root, **PROBES), self.git("rev-parse", "HEAD"))

    def test_a_session_not_running_the_installed_build_is_refused_before_any_save(self):
        self.finalized_by_an_older_rice()
        self.owner_wip()
        before = self.git("rev-parse", "HEAD")
        outcome = rice.bootstrap(self.root, rtk_probe=_ok_rtk, plugin_probe=_entries(("user", "9.9.9")))
        self._refused_before_any_save(outcome, before)

    def test_the_save_never_forces_or_rewrites(self):
        src = "".join(inspect.getsource(f) for f in (rice._save_before_update, rice._push_destination,
                                                     rice._give_back_rices_writes, rice._git))
        for flag in ("--force", '"-f"', "--amend", '"--hard"', '"rebase"', '"--delete"', "stash", "pull"):
            self.assertNotIn(flag, src)
        self.assertIn('f"HEAD:refs/heads/{branch}"', src)   # the one refspec: never a `+` one


class TestBuildPreconditions(LifecycleBase):
    """RICE-SPEC-001 I16: neither entry point writes anything to a repo
    unless this session runs the installed build and is not older than the
    build that finalized the repo."""

    def _finalized(self):
        _matt_artifacts(self.root)
        self.assertTrue(self.bootstrap().ok)

    def _blocked_without_a_write(self, outcome, before, item):
        self.assertEqual(outcome.stage, "blocked")
        self.assertFalse(outcome.ok)
        self.assertIsNone(outcome.repaired)
        self.assertTrue(any(r.startswith(item) for r in outcome.report.reasons), outcome.report.reasons)
        directive = "\n".join(outcome.directive)
        self.assertIn("nothing was written", directive)
        self.assertIn(outcome.report.item(item).detail, directive)
        self.assertEqual(_tree(self.root), before, "a blocked entry point wrote to the repo")

    def test_an_updated_but_not_reloaded_session_writes_nothing_to_a_fresh_repo(self):
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                                 plugin_probe=_entries(("user", "9.9.9")))
        self._blocked_without_a_write(outcome, before, "plugin:build")

    def test_an_updated_but_not_reloaded_session_writes_nothing_to_a_finalized_repo(self):
        self._finalized()
        (self.root / ".claude" / "rice.md").write_text("an older build's rules\n", encoding="utf-8")
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, rtk_probe=_ok_rtk, plugin_probe=_entries(("user", "9.9.9")))
        self._blocked_without_a_write(outcome, before, "plugin:build")

    def test_an_older_build_never_touches_a_repo_a_newer_build_finalized(self):
        self._finalized()
        self.version_file.write_text("9.9.9\n", encoding="utf-8")
        (self.root / ".claude" / "rice.md").write_text("a newer build's rules\n", encoding="utf-8")
        before = _tree(self.root)
        outcome = self.bootstrap()
        self._blocked_without_a_write(outcome, before, "rice-version:not-newer")
        self.assertIn("9.9.9", "\n".join(outcome.directive))

    def _continuation_blocked(self, probe, item, recorded=None):
        self.bootstrap()
        _matt_artifacts(self.root)
        (self.root / ".claude" / "rice.md").write_text("drifted\n", encoding="utf-8")
        if recorded:
            self.version_file.write_text(recorded + "\n", encoding="utf-8")
        before = _tree(self.root)
        outcome = rice.continue_after_matt(self.root, rtk_probe=_ok_rtk, plugin_probe=probe)
        self._blocked_without_a_write(outcome, before, item)
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))
        msg = rice.continuation_message(self.root, outcome)
        self.assertIn("nothing was written", msg)
        self.assertNotIn("repair ran", msg)

    def test_the_stop_continuation_never_repairs_with_the_wrong_build(self):
        self._continuation_blocked(_entries(("user", "9.9.9")), "plugin:build")

    def test_the_stop_continuation_never_touches_a_newer_repo(self):
        self._continuation_blocked(_installed, "rice-version:not-newer", recorded="9.9.9")

    def test_a_malformed_recorded_version_blocks_both_entry_points_without_a_write(self):
        self._finalized()
        self.version_file.write_text("v0.1\n", encoding="utf-8")
        before = _tree(self.root)
        self._blocked_without_a_write(self.bootstrap(), before, "rice-version:not-newer")
        reconcile.enter_waiting_for_matt(self.root)
        before = _tree(self.root)
        self._blocked_without_a_write(rice.continue_after_matt(self.root, **PROBES), before,
                                      "rice-version:not-newer")

    def test_a_verifier_that_could_not_collect_still_blocks_the_continuation_cleanly(self):
        """Missing precondition items are a block with the verifier's words,
        never a crash that silences the Stop hook."""
        self.bootstrap()
        _matt_artifacts(self.root)
        before = _tree(self.root)
        with mock.patch.object(evidence, "collect", side_effect=RuntimeError("probe exploded")):
            outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertEqual(outcome.stage, "blocked")
        self.assertIsNone(outcome.repaired)
        msg = rice.continuation_message(self.root, outcome)
        self.assertIn("plugin:build: MISSING", msg)
        self.assertIn("nothing was written", msg)
        self.assertEqual(_tree(self.root), before)


class TestTheOwnerLearnsWhenTheRulesLoad(LifecycleBase):
    """RICE-FINAL-UX-REVIEW-001: CLAUDE.md's import of `.claude/rice.md` is
    read when a session starts. In a real session the bootstrapping session
    did not have the rules it had just written, and nothing said so; after
    `/clear` it did. Both ways a bootstrap finishes say it."""

    def _says_the_boundary(self, text):
        for word in (".claude/rice.md", "/clear", "new session"):
            self.assertIn(word, text)

    def test_finishing_inside_the_one_invocation_says_it(self):
        _matt_artifacts(self.root)
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self._says_the_boundary("\n".join(outcome.directive))

    def test_the_stop_continuation_says_it(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertTrue(outcome.ok, outcome.lines)
        self._says_the_boundary(rice.continuation_message(self.root, outcome))


class TestOnlyWhatRiceAndMattWroteIsCommitted(LifecycleBase):
    """RICE-FINAL-UX-REVIEW-001: told to commit "the changes in the working
    tree", the agent in a real session ran `git add -A` and pushed the
    OWNER's unrelated work-in-progress with rice's. Every commit instruction
    names the paths a bootstrap (or uninstall) writes and forbids the rest."""

    def _commits_only_rices(self, text):
        self.assertNotIn("changes in the working tree", text)
        self.assertIn("never `git add -A`", text)
        for path in (".claude/", "CLAUDE.md", "AGENTS.md", "docs/agents/"):
            self.assertIn(f"`{path}`", text)

    def test_finishing_inside_the_one_invocation(self):
        _matt_artifacts(self.root)
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self._commits_only_rices("\n".join(outcome.directive))

    def test_the_stop_continuation(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        outcome = rice.continue_after_matt(self.root, **PROBES)
        self._commits_only_rices(rice.continuation_message(self.root, outcome))

    def test_uninstall(self):
        skill = (REPO / "skills" / "rice-uninstall" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("never `git add -A`", skill)


class TestFormalReportsFollowRicesOutputRules(LifecycleBase):
    """RICE-BOOTSTRAP-UX-FIX-001: rice.md's output rules (a conversation
    number, one code block per formal report) live in a file the
    bootstrapping session never loaded. Rice hands them, from rice.md itself,
    to the agent wherever it speaks during a bootstrap -- the command's own
    output, the Stop continuation -- and the skill asks for the block."""

    def _section(self, heading):
        text = reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8")
        body = text.split(heading + "\n", 1)[1].split("\n## ", 1)[0]
        return body.strip()

    def test_the_rules_are_rice_mds_own_words(self):
        for heading in ("## Conversation numbering", "## Single-block output"):
            self.assertIn(self._section(heading), rice.output_rules())

    def test_the_bootstrap_command_hands_them_to_the_agent_not_the_owner(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rice.main(["bootstrap", str(self.root), "--archive", "1", "--output-style", "none"])
        agent, _, owner = out.getvalue().partition("--- present this to the OWNER verbatim ---")
        self.assertIn(self._section("## Single-block output"), agent)
        self.assertNotIn(self._section("## Single-block output"), owner)
        self.assertIn("the summary after the OWNER's Matt setup", agent)  # that report too, before rice.md loads

    def test_the_stop_continuation_asks_for_one_code_block(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        msg = rice.continuation_message(self.root, rice.continue_after_matt(self.root, **PROBES))
        self.assertIn("one fenced code block", msg)
        self.assertIn(self._section("## Conversation numbering"), msg)

    def _prompt_context(self, root):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"cwd": str(root)}))), \
                contextlib.redirect_stdout(out):
            self.assertEqual(session_context.main(["--prompt"]), 0)
        return out.getvalue()

    def test_every_prompt_of_the_matt_window_carries_them_and_no_other_prompt_does(self):
        """In a real session the agent's summary of Matt's setup ignored the
        rules it was handed once, at the hand-off; while the bootstrap waits
        for Matt, each of the OWNER's prompts carries them again."""
        self.assertEqual(self._prompt_context(self.root), "")             # before rice
        self.bootstrap()
        self.assertIn(self._section("## Single-block output"), self._prompt_context(self.root))
        _matt_artifacts(self.root)
        self.assertTrue(rice.continue_after_matt(self.root, **PROBES).ok)
        self.assertEqual(self._prompt_context(self.root), "")             # finalized: ordinary chat

    def test_the_prompt_hook_is_wired(self):
        item = evidence._hook_wired_item("prompt-context", "UserPromptSubmit", None,
                                         "session_context.py", "session_context.py", "--prompt")
        self.assertEqual(item.state, "PASS", item.detail)

    def test_the_skill_presents_the_block_inside_one_code_block(self):
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?is)conversation\s+number.*one\s+fenced\s+code\s+block")
        self.assertRegex(skill, r"(?is)native\s+choice")    # menus stay native


class TestTheOwnerIsAskedOnlyWhatIsUnanswered(LifecycleBase):
    """RICE-FINAL-UX-REVIEW-001: Step 1 asks exactly the choices bootstrap
    would still write. A re-run asks nothing -- in a real session the skill
    re-asked both on an update and silently dropped the new answers, since
    each is written only when its key is absent."""

    def test_a_fresh_repo_is_asked_both(self):
        self.assertEqual(rice.questions(self.root), ["archive", "output-style"])

    def test_a_bootstrapped_repo_is_asked_nothing_even_with_claude_default_and_archive_off(self):
        self.bootstrap(archive=False, output_style=None)
        self.assertEqual(rice.questions(self.root), [])

    def test_a_claude_default_answer_survives_a_re_run_that_passes_a_style(self):
        """One predicate answers "is this answered?" for asking and writing
        alike: the absent key of a Claude-default answer is never filled."""
        self.bootstrap(archive=False, output_style=None)
        self.bootstrap(archive=True, output_style="rice:老弟")
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", s)
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "0")

    def test_a_projects_own_output_style_is_never_asked_about(self):
        (self.root / ".claude").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "settings.json").write_text(json.dumps({"outputStyle": "Explanatory"}),
                                                              encoding="utf-8")
        self.assertEqual(rice.questions(self.root), ["archive"])

    def test_a_lost_archive_answer_is_asked_again(self):
        self.bootstrap(archive=True, output_style=None)
        p = self.root / ".claude" / "settings.json"
        s = json.loads(p.read_text(encoding="utf-8")); del s["env"]["RICE_SESSION_ARCHIVE"]
        p.write_text(json.dumps(s), encoding="utf-8")
        self.assertEqual(rice.questions(self.root), ["archive"])

    def test_the_cli_prints_one_per_line_and_nothing_on_a_re_run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rice.main(["questions", str(self.root)]), 0)
        self.assertEqual(out.getvalue(), "archive\noutput-style\n")
        self.bootstrap()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rice.main(["questions", str(self.root)]), 0)
        self.assertEqual(out.getvalue(), "")

    def test_the_skill_asks_only_what_questions_prints(self):
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('scripts/rice.py" questions <repo_root>', skill)
        self.assertRegex(skill, r"(?is)prints\s+nothing.*ask\s+nothing")


class TestTheSubagentModelIsTheProjects(LifecycleBase):
    """RICE-OWNERSHIP-FIX-001, through the lifecycle engine itself."""

    def test_bootstrap_finalizes_with_it_and_a_second_bootstrap_changes_nothing(self):
        (self.root / ".claude").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "sonnet"}}), encoding="utf-8")
        _matt_artifacts(self.root)
        first = self.bootstrap()
        self.assertEqual(first.stage, "finalized", first.lines)
        settings = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")
        before = _tree(self.root)
        again = rice.bootstrap(self.root, **PROBES)  # a re-run asks nothing
        self.assertEqual(again.stage, "finalized", again.lines)
        self.assertEqual(_tree(self.root), before)


class TestCanonicalInstallPolicy(LifecycleBase):
    """RICE-BOOTSTRAP-044C: rice's canonical install is user scope, and only
    user scope -- like Matt's plugin. Anything else is a definite stop with
    an explicit cleanup, never a restart the OWNER is sent around forever."""

    BUILD = None  # set in setUp, once reconcile is importable

    def setUp(self):
        super().setUp()
        self.BUILD = evidence.build_version()

    def test_a_user_scope_install_is_the_normal_path(self):
        _matt_artifacts(self.root)
        self.bootstrap()
        outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                              plugin_probe=_entries(("user", self.BUILD)))
        self.assertEqual(outcome.stage, "finalized")
        self.assertTrue(outcome.ok, outcome.lines)

    def test_duplicate_scopes_block_deterministically_and_never_loop_restarts(self):
        self.bootstrap()
        before = _tree(self.root)
        for pair in (("user", self.BUILD), ("project", "0.0.1")), (("user", self.BUILD), ("project", self.BUILD)):
            outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk, plugin_probe=_entries(*pair))
            directive = "\n".join(outcome.directive)
            self.assertEqual(outcome.stage, "blocked", directive)
            self.assertTrue(outcome.blocked)
            self.assertFalse(outcome.ok)                       # the CLI exits non-zero
            self.assertNotIn("Start a new session", directive)  # and never asks for a restart
            self.assertIn("more than one scope", directive)
            self.assertIn("user scope:", directive)             # every scope is named
            self.assertIn("project scope:", directive)
            self.assertIn("claude plugin uninstall rice@rice-bootstrap --scope project", directive)
            self.assertNotIn("--scope user", directive)         # the canonical one is kept
            self.assertIn("will not remove either install for you", directive)
            self.assertEqual(_tree(self.root), before)          # nothing written, nothing removed

    def test_a_project_only_install_is_named_as_non_canonical(self):
        self.bootstrap()
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                              plugin_probe=_entries(("project", self.BUILD)))
        directive = "\n".join(outcome.directive)
        self.assertEqual(outcome.stage, "blocked")
        self.assertIn("canonical install is user scope", directive)
        self.assertIn("claude plugin install rice@rice-bootstrap", directive)
        self.assertEqual(_tree(self.root), before)

    def test_an_install_that_is_not_this_build_is_the_verifiers_business(self):
        """With no update system there is no restart flow: a session running
        a different build than the one installed is what `plugin:build`
        reports, and the OWNER decides what to do about it."""
        stale = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                               plugin_probe=_entries(("user", "0.0.1")))
        self.assertFalse(stale.ok)
        self.assertTrue(any(r.startswith("plugin:build") for r in stale.report.reasons), stale.report.reasons)
        self.assertNotIn("restart", "\n".join(stale.directive).lower())
        self.assertFalse(self.version_file.exists())

    def test_the_verifier_not_rice_decides_a_duplicate_install_is_unhealthy(self):
        """No second truth in rice.py: the rubric item is what fails a repo."""
        item = evidence._plugin_scope_item(_entries(("user", self.BUILD), ("project", self.BUILD)))
        self.assertEqual(item.name, "plugin:scope")
        self.assertEqual(item.state, "FAIL")
        self.assertIn("plugin:scope", verify.GATE_A)
        report = verify.run(self.root, rtk_probe=_ok_rtk,
                            plugin_probe=_entries(("user", self.BUILD), ("project", self.BUILD)))
        self.assertFalse(report.gate_a)
        self.assertTrue(any(r.startswith("plugin:scope") for r in report.reasons), report.reasons)

    def test_bootstrap_refuses_a_duplicate_install_before_writing_anything(self):
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                                 plugin_probe=_entries(("user", self.BUILD), ("project", self.BUILD)))
        self.assertFalse(outcome.ok)
        self.assertTrue(any(r.startswith("plugin:scope") for r in outcome.report.reasons))
        self.assertEqual(_tree(self.root), before, "bootstrap wrote to the repo before stopping")
        self.assertIn("claude plugin uninstall rice@rice-bootstrap --scope project",
                      "\n".join(outcome.directive))

    def test_the_same_install_twice_at_the_canonical_scope_is_a_duplicate(self):
        self.bootstrap()
        outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                              plugin_probe=_entries(("user", self.BUILD), ("user", "0.0.1")))
        self.assertEqual(outcome.stage, "blocked")
        self.assertIn("more than once at user scope", "\n".join(outcome.directive))
        self.assertFalse(self.version_file.exists())

    def test_an_install_list_without_a_scope_is_unclassifiable_not_a_failure(self):
        """The only remedy a scope FAIL can offer is a `--scope <name>`
        command; without a scope there is no runnable one, so this is
        NOT_VERIFIED -- the gate still fails, but honestly."""
        def no_scope(cmd):
            return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": self.BUILD}]))

        item = evidence._plugin_scope_item(no_scope)
        self.assertEqual(item.state, "NOT_VERIFIED")
        self.assertNotIn("--scope ?", item.detail)
        outcome = rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk, plugin_probe=no_scope)
        self.assertTrue(outcome.blocked)
        self.assertNotIn("--scope ?", "\n".join(outcome.directive))

    def test_not_installed_and_could_not_ask_are_different_answers(self):
        """Both stop, but only one of them is "install rice"."""
        def nothing(cmd):
            return _R(0, "[]")

        def unreadable(cmd):
            raise FileNotFoundError("claude")

        missing = "\n".join(rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                                           plugin_probe=nothing).directive)
        self.assertIn("claude plugin marketplace add", missing)
        unknown = "\n".join(rice.bootstrap(self.root, archive=True, output_style=None, rtk_probe=_ok_rtk,
                                           plugin_probe=unreadable).directive)
        self.assertIn("could not be determined", unknown)
        self.assertNotIn("claude plugin marketplace add", unknown)

    def test_the_blocker_text_names_real_scopes_and_runnable_commands(self):
        """On the bootstrap path the rubric item's detail is the whole text
        the OWNER sees, so it may not contain a placeholder."""
        item = evidence._plugin_scope_item(_entries(("user", self.BUILD), ("project", "0.0.1")))
        self.assertNotIn("<scope>", item.detail)
        self.assertIn("--scope project", item.detail)


class TestThereIsNoUpdateSystem(unittest.TestCase):
    """RICE-FINAL-001: rice offers two repo lifecycles, bootstrap and
    uninstall. There is no update workflow, no deprecated wrapper, and no
    dead code kept "in case"."""

    SHIPPED = [p for p in (REPO / "scripts").glob("*.py")]

    def test_the_update_skill_does_not_exist(self):
        self.assertFalse((REPO / "skills" / "rice-update").exists())
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        # two lifecycles plus the History Skill key (RICE-SPEC-001); never an update skill
        self.assertEqual(manifest["skills"], ["./skills/bootstrap", "./skills/rice-uninstall", "./skills/history"])
        allowlist = (REPO / "scripts" / "export_public.py").read_text(encoding="utf-8")
        self.assertNotIn("rice-update", allowlist)
        self.assertEqual(sorted(p.name for p in (REPO / "skills").iterdir()),
                         ["bootstrap", "history", "rice-uninstall"])

    def test_the_engine_has_no_update_verb(self):
        self.assertFalse(hasattr(rice, "update"))
        self.assertEqual([v for v in ("bootstrap", "continue_after_matt", "styles")
                          if not hasattr(rice, v)], [])
        with self.assertRaises(SystemExit):
            rice.main(["update", "/tmp/x"])

    def test_no_shipped_script_carries_a_self_update_path(self):
        for py in self.SHIPPED:
            if any(re.match(p, f"scripts/{py.name}") for p in export_public.FORBIDDEN_PATTERNS):
                continue  # private release tooling, never shipped to a repo
            if py.name == "delete_remote_branch.py":
                continue  # reads whether ONE named branch exists on origin; nothing about "latest"
            code = "".join(py.read_text(encoding="utf-8").split('"""')[::2])
            # tokens that can only mean "rice updating itself". (`upstream` on
            # its own is the session archive's git upstream -- unrelated.)
            for forbidden in ("plugin update", "marketplace update", "ls-remote",
                              "upstream main", "rice-update", "restart-required",
                              "latest release", "self-update"):
                self.assertNotIn(forbidden, code.lower(), f"{py.name}: {forbidden}")

    def test_bootstrap_and_uninstall_never_ask_for_a_restart_or_an_update(self):
        for rel in ("skills/bootstrap/SKILL.md", "skills/rice-uninstall/SKILL.md",
                    "scripts/rice.py", "scripts/uninstall.py", "scripts/bootstrap_continue.py"):
            text = (REPO / rel).read_text(encoding="utf-8")
            for forbidden in ("/rice:rice-update", "start a new session and run",
                              "two-session", "restart-required"):
                self.assertNotIn(forbidden, text, f"{rel}: {forbidden}")

    def test_the_pinned_ref_is_a_release_branch_not_a_sha(self):
        self.assertTrue(rice.RELEASE_REF.startswith("release/"))
        self.assertEqual(rice.RELEASE_REF, settings_template.release_ref(evidence.build_version()))
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(rice.RELEASE_REF, f"release/{manifest['version']}")


class TestDirectives(LifecycleBase):
    """Every OWNER-facing reply is decided here, so the adapter can present it
    without branching."""

    def test_rice_not_loaded_hands_the_install_back_and_writes_nothing(self):
        before = _tree(self.root)

        def not_installed(cmd):
            return _R(0, "[]")

        outcome = rice.bootstrap(self.root, archive=True, output_style=None,
                                 rtk_probe=_ok_rtk, plugin_probe=not_installed)
        self.assertEqual(outcome.stage, "install-required")
        self.assertFalse(outcome.ok)
        directive = "\n".join(outcome.directive)
        self.assertIn("claude plugin marketplace add", directive)
        self.assertIn("claude plugin install", directive)
        self.assertEqual(_tree(self.root), before)

    def test_waiting_for_matt_is_the_terse_handoff_with_nothing_after_it(self):
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "waiting-for-matt")
        self.assertEqual(outcome.directive[0], f"`{outcome.verdict}`")
        self.assertEqual(outcome.directive[-1], "/mattpocock-skills:setup-matt-pocock-skills")
        self.assertLessEqual(len(outcome.directive), 6)

    def test_matt_already_present_never_produces_a_handoff(self):
        _matt_artifacts(self.root)
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "finalized")
        directive = "\n".join(outcome.directive)
        self.assertIn(outcome.verdict, directive)
        self.assertNotIn("/mattpocock-skills:setup-matt-pocock-skills", directive)
        self.assertIn("no Matt hand-off", directive)

    def test_blockers_are_the_verifiers_reasons_and_nothing_invented(self):
        shutil.rmtree(evidence.transcript_dir(self.root))
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "blocked")
        for reason in outcome.report.reasons:
            self.assertIn(reason, "\n".join(outcome.directive))


class TestOwnerChoices(LifecycleBase):
    def test_owner_answers_are_written_once_and_never_overwritten(self):
        self.bootstrap(output_style="mine")
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "1")
        self.assertEqual(s["outputStyle"], "mine")
        self.bootstrap(archive=False, output_style="other")
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "1")
        self.assertEqual(s["outputStyle"], "mine")

    def test_bootstrap_never_declares_the_plugin_at_project_scope(self):
        """RICE-FIX-001: rice's own install is user scope only -- a fresh
        bootstrap must never write `extraKnownMarketplaces`/`enabledPlugins`
        into the repo it configures."""
        self.bootstrap()
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("extraKnownMarketplaces", s)
        self.assertNotIn("enabledPlugins", s)

    def test_claude_default_style_stays_absent_never_a_placeholder(self):
        self.bootstrap(output_style=None)
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", s)


class TestLegacyProjectScopeMigration(LifecycleBase):
    """RICE-FIX-001: a repo `/rice:bootstrap` configured under 0.1.3 also
    carries a project-scope plugin declaration that build wrote. Re-running
    bootstrap on it must migrate that away and still reach the same outcome
    a fresh repo would."""

    def _plant_013_leftover(self):
        p = self.root / ".claude" / "settings.json"
        s = json.loads(p.read_text(encoding="utf-8"))
        s.setdefault("extraKnownMarketplaces", {})[settings_template.MARKETPLACE_NAME] = {
            "source": {"source": "github", "repo": settings_template.MARKETPLACE_REPO,
                      "ref": f"release/{evidence.build_version()}"}
        }
        s.setdefault("enabledPlugins", {})[settings_template.PLUGIN_KEY] = True
        p.write_text(json.dumps(s), encoding="utf-8")

    def test_bootstrap_migrates_a_013_leftover_and_still_reaches_matt_handoff(self):
        self.bootstrap()  # a fresh repo, as 0.1.3 would have left it up to the Matt hand-off
        self._plant_013_leftover()
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "waiting-for-matt", outcome.lines)
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn(settings_template.MARKETPLACE_NAME, s.get("extraKnownMarketplaces", {}))
        self.assertNotIn(settings_template.PLUGIN_KEY, s.get("enabledPlugins", {}))

    def test_bootstrap_migrates_a_013_leftover_all_the_way_to_final_verified(self):
        _matt_artifacts(self.root)
        self.bootstrap()  # a fresh repo already past the Matt hand-off (0.1.3-era)
        self._plant_013_leftover()
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "finalized", outcome.lines)
        self.assertTrue(outcome.ok, outcome.lines)
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn(settings_template.MARKETPLACE_NAME, s.get("extraKnownMarketplaces", {}))
        self.assertNotIn(settings_template.PLUGIN_KEY, s.get("enabledPlugins", {}))

    def test_styles_lists_this_builds_bundled_style(self):
        values = [s["value"] for s in rice.styles(self.root, **PROBES)]
        self.assertIn(settings_template.RICE_STYLE, values)
        self.assertIn("default", values)


class TestVerifierSoleAuthority(LifecycleBase):
    """RICE-BOOTSTRAP-021 is unchanged by the engine: only verify.py stamps
    the verified version, clears the pending marker, and produces a verdict."""

    def test_engine_writes_no_version_and_clears_no_marker_itself(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        inert = verify.Report(build="9.9.9", items=[], gate_a=True, gate_b=True, version_written=None,
                              lines=["FINAL-VERIFIED 9.9.9"], required=verify.GATE_B,
                              verdict="FINAL-VERIFIED 9.9.9")
        with mock.patch.object(verify, "run", return_value=inert):
            outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.verdict, "FINAL-VERIFIED 9.9.9")
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_no_module_but_verify_builds_a_verdict_or_re_evaluates_a_gate(self):
        for py in (REPO / "scripts").glob("*.py"):
            if py.name == "verify.py":
                continue
            src = py.read_text(encoding="utf-8")
            self.assertNotIn("gate_passes(", src, f"{py.name} re-evaluates a gate")
            self.assertNotIn('startswith("FINAL-")', src, f"{py.name} scrapes the verdict out of lines")

    def test_callers_read_the_verdict_and_reasons_off_the_report(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertEqual(outcome.verdict, f"FINAL-VERIFIED {evidence.build_version()}")
        self.assertEqual(outcome.report.reasons, [])
        self.assertIn(outcome.verdict, rice.continuation_message(self.root, outcome))


class TestAdaptersCarryNoSteps(unittest.TestCase):
    """The success criterion of RICE-BOOTSTRAP-044/-044B: the three adapters
    -- the bootstrap skill, the update skill, the Stop hook -- collect input,
    run the engine's verbs, and present what comes back. None of them may contain a
    lifecycle implementation detail, because a decision written in two places
    is a decision that will drift."""

    ADAPTERS = ("skills/bootstrap/SKILL.md", "scripts/bootstrap_continue.py")

    # calling into the machinery the engine owns
    FORBIDDEN_CALLS = ("reconcile.", "verify.run", "verify.py", "repair.repair", "repair.py",
                       "bootstrap_state", "output_styles.", "is_rice_update")
    # deciding what the engine decides
    FORBIDDEN_DECISIONS = ("Gate A", "Gate B", "--final", "matt:artifacts", "plugin:installed",
                           "plugin:build", "git-deny:behaves", "git ls-remote",
                           "claude plugin update", "claude plugin marketplace update",
                           "WAITING_FOR_MATT", "gate_passes", "FINAL-VERIFIED", "RICE-VERIFIED")

    def test_no_adapter_carries_a_lifecycle_implementation_detail(self):
        for rel in self.ADAPTERS:
            src = (REPO / rel).read_text(encoding="utf-8")
            for forbidden in self.FORBIDDEN_CALLS + self.FORBIDDEN_DECISIONS:
                self.assertNotIn(forbidden, src, f"{rel} still carries: {forbidden}")

    def test_every_adapter_reaches_the_engine_and_only_the_engine(self):
        hook = (REPO / "scripts" / "bootstrap_continue.py").read_text(encoding="utf-8")
        self.assertIn("rice.continue_after_matt(", hook)
        self.assertNotIn("subprocess", hook)
        self.assertNotIn('"git"', hook)
        self.assertIn("rice.py\" bootstrap", (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("uninstall.py", (REPO / "skills" / "rice-uninstall" / "SKILL.md").read_text(encoding="utf-8"))

    def test_the_bootstrap_skill_leaves_the_update_save_to_the_command(self):
        """RICE-UPDATE-PRESAVE-001: the engine saves; the adapter only keeps
        its hands off git until then."""
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?is)do\s+not\s+stage,\s+commit,\s+stash\s+or\s+push")
        self.assertNotIn("git add", skill)

    def test_the_bootstrap_skill_presents_the_engine_directive_verbatim(self):
        src = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("present this to the OWNER verbatim", src)

    def test_the_bootstrap_skill_keeps_its_two_owner_contracts(self):
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        # the hand-off command, so nothing else may be appended after it
        self.assertIn("/mattpocock-skills:setup-matt-pocock-skills", skill)
        # and the single-invocation promise
        self.assertIn("exactly one invocation", skill)
        self.assertNotIn('saying "bootstrap this repo with rice" again', skill)


class TestRepairIsNeverUnderreported(LifecycleBase):
    def test_a_gate_a_repair_survives_into_the_final_report(self):
        """A repair done on the way to Gate A must still be reported when
        Gate B then passes cleanly -- otherwise the output claims repair was
        never needed after it rewrote files."""
        _matt_artifacts(self.root)
        self.bootstrap()
        (self.root / ".claude" / "rice.md").write_text("hand-edited drift\n", encoding="utf-8")
        outcome = self.bootstrap()
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertIsNotNone(outcome.repaired)
        self.assertTrue(any("rice.md" in a for a in outcome.repaired.applied), outcome.repaired.applied)
        self.assertNotIn("not needed", "\n".join(outcome.lines))


class TestCliTranslation(unittest.TestCase):
    """`main` is the whole interface the skills use: its flag translation is
    behaviour, not plumbing."""

    def _bootstrap_kwargs(self, *argv) -> dict:
        with mock.patch.object(rice, "bootstrap") as spy:
            spy.return_value = rice.Outcome(report=verify.Report(
                build="x", items=[], gate_a=True, gate_b=None, version_written=None, verdict="v"))
            rice.main(["bootstrap", "/tmp/x", *argv])
        return spy.call_args.kwargs

    def test_output_style_none_means_claude_default_not_unasked(self):
        self.assertIs(self._bootstrap_kwargs("--output-style", "none")["output_style"], None)

    def test_output_style_omitted_means_the_owner_was_not_asked(self):
        self.assertIs(self._bootstrap_kwargs()["output_style"], rice.UNSET)

    def test_output_style_name_is_passed_through(self):
        self.assertEqual(self._bootstrap_kwargs("--output-style", "rice:老弟")["output_style"], "rice:老弟")

    def test_archive_flag_becomes_a_boolean_and_omission_stays_unasked(self):
        self.assertIs(self._bootstrap_kwargs("--archive", "1")["archive"], True)
        self.assertIs(self._bootstrap_kwargs("--archive", "0")["archive"], False)
        self.assertIsNone(self._bootstrap_kwargs()["archive"])

    def test_exit_code_follows_the_verifier(self):
        for ok in (True, False):
            report = verify.Report(build="x", items=[], gate_a=ok, gate_b=None,
                                   version_written=None, verdict="v")
            with mock.patch.object(rice, "bootstrap", return_value=rice.Outcome(report=report)):
                self.assertEqual(rice.main(["bootstrap", "/tmp/x"]), 0 if ok else 1)

    def test_continue_with_nothing_to_do_says_so_and_succeeds(self):
        with mock.patch.object(rice, "continue_after_matt", return_value=None):
            self.assertEqual(rice.main(["continue", "/tmp/x"]), 0)

    def test_every_verb_asks_each_external_command_at_most_once(self):
        calls = []

        def counting(cmd):
            calls.append(tuple(cmd))
            return _installed(cmd) if "plugin" in cmd else _ok_rtk(cmd)

        with mock.patch.object(evidence, "_default_plugin_probe", counting), \
             mock.patch.object(evidence, "_default_rtk_probe", counting), \
             tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d + "/cfg"}):
            rice.main(["bootstrap", d, "--archive", "1", "--output-style", "none"])
        duplicated = [c for c in set(calls) if calls.count(c) > 1]
        self.assertEqual(duplicated, [], f"probe spawned more than once: {calls}")


class TestCli(LifecycleBase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(REPO / "scripts" / "rice.py"), *args],
                              capture_output=True, text=True, env={**os.environ,
                                                                   "CLAUDE_CONFIG_DIR": str(self.root / "cfg")})

    def test_bootstrap_verb_stops_before_writing_when_rice_is_not_loaded(self):
        """The real CLI with real probes: rice is not installed in a test
        container, so the engine hands the install back to the OWNER and
        writes nothing -- the decision the skill used to make in Step 0."""
        r = self._run("bootstrap", str(self.root), "--archive", "1", "--output-style", "none")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("present this to the OWNER verbatim", r.stdout)
        self.assertIn("claude plugin marketplace add", r.stdout)
        self.assertFalse((self.root / ".claude" / "settings.json").exists())
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_continue_verb_is_quiet_when_there_is_nothing_to_do(self):
        r = self._run("continue", str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to continue", r.stdout)


if __name__ == "__main__":
    unittest.main()
