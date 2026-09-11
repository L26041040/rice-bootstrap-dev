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

import bootstrap_state  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import rice  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF = "0123456789abcdef0123456789abcdef01234567"


class _R:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _missing_rtk(cmd):
    raise FileNotFoundError("rtk")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(),
                              "scope": "user", "enabled": True}]))


PROBES = {"rtk_probe": _ok_rtk, "plugin_probe": _installed}


def fake_run(*, sha=REF, ls_remote_rc=0, install_rc=0):
    """A stand-in for the two external things an update touches: reading
    upstream main's commit, and the official install commands."""
    calls = []

    def run(cmd):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "ls-remote"]:
            return _R(ls_remote_rc, f"{sha}\trefs/heads/main\n" if (ls_remote_rc == 0 and sha) else "")
        return _R(install_rc, "", "claude: the real reason it failed" if install_rc else "")

    run.calls = calls
    return run

MATT_SECTION = "## Agent skills\n\n### Issue tracker\n\nGitHub Issues. See `docs/agents/issue-tracker.md`.\n"


def _git_denied_transcript(root: Path, ts="2099-01-01T00:00:00.000Z"):
    """The harness-recorded denial Gate A's git-deny:behaves item reads."""
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
        kw.setdefault("ref", REF)
        kw.setdefault("archive", True)
        kw.setdefault("output_style", None)
        return rice.bootstrap(self.root, **kw, **PROBES)

    def update(self, **kw):
        kw.setdefault("run", fake_run())
        return rice.update(self.root, **kw, **PROBES)


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
            outcome = self.update()
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
        shutil.rmtree(reconcile.transcript_dir(self.root))  # git-deny evidence: nothing repairs it
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
        self.assertTrue(bootstrap_state.is_waiting_for_matt(self.root))
        self.assertFalse(self.version_file.exists())  # Gate A never stamps

    def test_the_owners_matt_setup_is_continued_without_a_second_invocation(self):
        self.bootstrap()
        _matt_artifacts(self.root)
        outcome = rice.continue_after_matt(self.root, **PROBES)
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertEqual(self.version_file.read_text().strip(), reconcile.build_version())
        self.assertFalse(bootstrap_state.is_waiting_for_matt(self.root))

    def test_matt_already_complete_finishes_inside_the_one_invocation(self):
        _matt_artifacts(self.root)
        outcome = self.bootstrap()
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertFalse(outcome.waiting_for_matt)  # never ask for a setup already done
        self.assertEqual(self.version_file.read_text().strip(), reconcile.build_version())
        self.assertFalse(bootstrap_state.is_waiting_for_matt(self.root))

    def test_gate_a_failure_does_not_park_the_repo(self):
        shutil.rmtree(reconcile.transcript_dir(self.root))
        outcome = self.bootstrap()
        self.assertFalse(outcome.ok)
        self.assertFalse(bootstrap_state.is_waiting_for_matt(self.root))

    def test_continue_is_silent_until_there_is_something_to_continue(self):
        self.assertIsNone(rice.continue_after_matt(self.root, **PROBES))  # no marker
        self.bootstrap()
        self.assertIsNone(rice.continue_after_matt(self.root, **PROBES))  # Matt has not started
        self.assertTrue(bootstrap_state.is_waiting_for_matt(self.root))

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


class TestUpdateStages(LifecycleBase):
    """RICE-BOOTSTRAP-044B: an update spans two sessions and several external
    facts. Every stage of that is decided by the engine from mechanical
    facts -- the skill only runs one command."""

    def test_upstream_unreadable_writes_nothing_and_never_guesses_a_target(self):
        before = _tree(self.root)
        outcome = self.update(run=fake_run(ls_remote_rc=2))
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.blocked)
        self.assertIsNone(outcome.report)
        self.assertIn("never guessed", "\n".join(outcome.directive))
        self.assertEqual(_tree(self.root), before)

    def test_a_stale_ref_is_repinned_the_install_runs_and_the_owner_restarts(self):
        self.bootstrap()
        run = fake_run(sha="f" * 40)
        outcome = self.update(run=run)
        self.assertEqual(outcome.stage, "session-1")
        self.assertTrue(outcome.ok)          # this stage did its whole job
        self.assertIsNone(outcome.report)    # ... and deliberately verified nothing
        self.assertEqual(reconcile.current_ref(self.root), "f" * 40)
        self.assertEqual([c[:3] for c in run.calls][1:],
                         [["claude", "plugin", "marketplace"], ["claude", "plugin", "update"]])
        directive = "\n".join(outcome.directive)
        self.assertIn("Commit and push", directive)
        self.assertIn("Start a new session", directive)
        self.assertIn("f" * 40, directive)   # the Cloud install line must move too

    def test_a_project_scope_install_is_updated_in_project_scope(self):
        self.bootstrap()

        def project_scoped(cmd):
            return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(),
                                      "scope": "project", "enabled": True}]))

        run = fake_run(sha="f" * 40)
        rice.update(self.root, run=run, rtk_probe=_ok_rtk, plugin_probe=project_scoped)
        self.assertIn(["--scope", "project"], [c[-2:] for c in run.calls])

    def test_a_failed_install_blocks_and_reports_with_the_ref_already_moved(self):
        self.bootstrap()
        outcome = self.update(run=fake_run(sha="f" * 40, install_rc=1))
        self.assertTrue(outcome.blocked)
        self.assertEqual(reconcile.current_ref(self.root), "f" * 40)
        self.assertIn("did not complete", "\n".join(outcome.directive))

    def test_the_right_ref_with_the_old_build_still_loaded_asks_for_a_restart(self):
        self.bootstrap()

        def old_build(cmd):
            return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": "0.0.1",
                                      "scope": "user", "enabled": True}]))

        before = _tree(self.root)
        outcome = rice.update(self.root, run=fake_run(), rtk_probe=_ok_rtk, plugin_probe=old_build)
        self.assertEqual(outcome.stage, "restart-required")
        self.assertIsNone(outcome.report)
        self.assertIn("Start a new session", "\n".join(outcome.directive))
        self.assertEqual(_tree(self.root), before)  # nothing written

    def test_the_right_ref_and_build_converges_and_verifies_to_the_end(self):
        _matt_artifacts(self.root)
        self.bootstrap()
        (self.root / ".claude" / "rice.md").write_text("drift\n", encoding="utf-8")
        outcome = self.update()
        self.assertEqual(outcome.stage, "finalized")
        self.assertTrue(outcome.ok, outcome.lines)
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"),
                         reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))
        self.assertEqual(self.version_file.read_text().strip(), reconcile.build_version())


class TestUnknownIsNeverAFact(LifecycleBase):
    """A probe that could not answer is not an answer. Every one of these was
    a real defect in the first draft of the stage machine."""

    def _cli_unavailable(self, cmd):
        raise FileNotFoundError("claude")

    def test_an_unreadable_install_blocks_the_update_instead_of_looping_restarts(self):
        self.bootstrap()
        run = fake_run()
        outcome = rice.update(self.root, run=run, rtk_probe=_ok_rtk, plugin_probe=self._cli_unavailable)
        self.assertTrue(outcome.blocked)
        self.assertFalse(outcome.ok)  # and the CLI exits non-zero
        self.assertNotIn("Start a new session", "\n".join(outcome.directive))
        self.assertIn("could not be read", "\n".join(outcome.directive))

    def test_an_unreadable_install_never_runs_the_install_commands(self):
        """Otherwise `--scope project` would silently go missing."""
        self.bootstrap()
        run = fake_run(sha="f" * 40)
        rice.update(self.root, run=run, rtk_probe=_ok_rtk, plugin_probe=self._cli_unavailable)
        self.assertEqual([c for c in run.calls if c[0] == "claude"], [])
        self.assertEqual(reconcile.current_ref(self.root), REF)  # and the ref did not move

    def test_an_unreadable_upstream_never_pins_the_branch_name(self):
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, archive=True, output_style=None,
                                 run=fake_run(ls_remote_rc=128), **PROBES)
        self.assertTrue(outcome.blocked)
        self.assertIn("never guessed", "\n".join(outcome.directive))
        self.assertEqual(_tree(self.root), before)

    def test_an_upstream_with_no_commits_yet_uses_the_documented_stopgap(self):
        outcome = rice.bootstrap(self.root, archive=True, output_style=None,
                                 run=fake_run(sha=""), **PROBES)
        self.assertFalse(outcome.blocked)
        self.assertEqual(reconcile.current_ref(self.root), rice.REF_STOPGAP)

    def test_an_undeterminable_install_is_not_reported_as_rice_missing(self):
        before = _tree(self.root)
        outcome = rice.bootstrap(self.root, ref=REF, archive=True, output_style=None,
                                 rtk_probe=_ok_rtk, plugin_probe=self._cli_unavailable)
        self.assertTrue(outcome.blocked)
        directive = "\n".join(outcome.directive)
        self.assertIn("could not be determined", directive)
        self.assertNotIn("claude plugin marketplace add", directive)  # never tell them to install
        self.assertEqual(_tree(self.root), before)

    def test_a_leftover_old_install_is_not_treated_as_the_running_build(self):
        self.bootstrap()

        def two_installs(cmd):
            return _R(0, json.dumps([
                {"id": "rice@rice-bootstrap", "version": reconcile.build_version(), "scope": "user"},
                {"id": "rice@rice-bootstrap", "version": "0.0.1", "scope": "project"}]))

        outcome = rice.update(self.root, run=fake_run(), rtk_probe=_ok_rtk, plugin_probe=two_installs)
        self.assertEqual(outcome.stage, "restart-required")
        self.assertFalse(self.version_file.exists())  # nothing stamped under a mixed install

    def test_a_failed_install_reports_the_real_reason(self):
        self.bootstrap()
        outcome = self.update(run=fake_run(sha="f" * 40, install_rc=1))
        self.assertIn("the real reason it failed", "\n".join(outcome.directive))

    def test_a_stage_that_never_verified_claims_no_repair(self):
        def not_installed(cmd):
            return _R(0, "[]")

        outcome = rice.bootstrap(self.root, ref=REF, archive=True, output_style=None,
                                 rtk_probe=_ok_rtk, plugin_probe=not_installed)
        self.assertNotIn("repair: not needed", "\n".join(outcome.lines))


class TestDirectives(LifecycleBase):
    """Every OWNER-facing reply is decided here, so the adapter can present it
    without branching."""

    def test_rice_not_loaded_hands_the_install_back_and_writes_nothing(self):
        before = _tree(self.root)

        def not_installed(cmd):
            return _R(0, "[]")

        outcome = rice.bootstrap(self.root, ref=REF, archive=True, output_style=None,
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
        shutil.rmtree(reconcile.transcript_dir(self.root))
        outcome = self.bootstrap()
        self.assertEqual(outcome.stage, "blocked")
        for reason in outcome.report.reasons:
            self.assertIn(reason, "\n".join(outcome.directive))


class TestOwnerChoices(LifecycleBase):
    def test_owner_answers_are_written_once_and_never_overwritten(self):
        self.bootstrap(output_style="mine")
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(reconcile._get_path(s, settings_template.REF_PATH), REF)
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "1")
        self.assertEqual(s["outputStyle"], "mine")
        self.bootstrap(ref="deadbeef", archive=False, output_style="other")
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(reconcile._get_path(s, settings_template.REF_PATH), REF)
        self.assertEqual(s["env"]["RICE_SESSION_ARCHIVE"], "1")
        self.assertEqual(s["outputStyle"], "mine")

    def test_claude_default_style_stays_absent_never_a_placeholder(self):
        self.bootstrap(output_style=None)
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", s)

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
        self.assertTrue(bootstrap_state.is_waiting_for_matt(self.root))

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
        self.assertEqual(outcome.verdict, f"FINAL-VERIFIED {reconcile.build_version()}")
        self.assertEqual(outcome.report.reasons, [])
        self.assertIn(outcome.verdict, rice.continuation_message(self.root, outcome))


class TestAdaptersCarryNoSteps(unittest.TestCase):
    """The success criterion of RICE-BOOTSTRAP-044/-044B: the three adapters
    -- the bootstrap skill, the update skill, the Stop hook -- collect input,
    run one verb, and present what comes back. None of them may contain a
    lifecycle implementation detail, because a decision written in two places
    is a decision that will drift."""

    ADAPTERS = ("skills/bootstrap/SKILL.md", "skills/rice-update/SKILL.md",
                "scripts/bootstrap_continue.py")

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
        for rel, verb in (("skills/bootstrap/SKILL.md", "rice.py\" bootstrap"),
                          ("skills/rice-update/SKILL.md", "rice.py\" update")):
            self.assertIn(verb, (REPO / rel).read_text(encoding="utf-8"), rel)

    def test_both_skills_present_the_engine_directive_verbatim(self):
        for rel in ("skills/bootstrap/SKILL.md", "skills/rice-update/SKILL.md"):
            src = (REPO / rel).read_text(encoding="utf-8")
            self.assertIn("present this to the OWNER verbatim", src, rel)

    def test_the_update_skill_stays_owner_only(self):
        src = (REPO / "skills" / "rice-update" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("disable-model-invocation: true", src)

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
            with mock.patch.object(rice, "update", return_value=rice.Outcome(report=report)):
                self.assertEqual(rice.main(["update", "/tmp/x"]), 0 if ok else 1)

    def test_continue_with_nothing_to_do_says_so_and_succeeds(self):
        with mock.patch.object(rice, "continue_after_matt", return_value=None):
            self.assertEqual(rice.main(["continue", "/tmp/x"]), 0)

    def test_every_verb_asks_each_external_command_at_most_once(self):
        calls = []

        def counting(cmd):
            calls.append(tuple(cmd))
            return _installed(cmd) if "plugin" in cmd else _ok_rtk(cmd)

        with mock.patch.object(reconcile, "_default_plugin_probe", counting), \
             mock.patch.object(reconcile, "_default_rtk_probe", counting), \
             tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d + "/cfg"}):
            rice.main(["bootstrap", d, "--ref", REF, "--archive", "1", "--output-style", "none"])
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
        r = self._run("bootstrap", str(self.root), "--ref", REF, "--archive", "1", "--output-style", "none")
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
