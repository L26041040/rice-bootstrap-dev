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
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _missing_rtk(cmd):
    raise FileNotFoundError("rtk")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(),
                              "scope": "user", "enabled": True}]))


PROBES = {"rtk_probe": _ok_rtk, "plugin_probe": _installed}

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


class TestCanonicalFlow(LifecycleBase):
    def _verify_spy(self):
        """Every verifier verdict this flow produced, in order."""
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
            outcome = rice.update(self.root, **PROBES)
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
        self.assertEqual(seen[:2], [False, True], seen)  # verify -> repair -> verify
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
        self.assertEqual(seen, [False, False], seen)  # verified twice, still failing
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
    """Success criterion: the outer adapters no longer know bootstrap's
    detailed steps."""

    ENGINE_ONLY = ("reconcile.apply", "reconcile.write_owner_choices", "verify.run",
                   "repair.repair", "enter_waiting_for_matt", "verify.py <repo_root> --final")

    def test_the_stop_hook_only_parses_guards_and_calls_the_engine(self):
        src = (REPO / "scripts" / "bootstrap_continue.py").read_text(encoding="utf-8")
        for step in self.ENGINE_ONLY:
            self.assertNotIn(step, src, f"the hook still performs: {step}")
        self.assertIn("rice.continue_after_matt(", src)
        self.assertNotIn("subprocess", src)
        self.assertNotIn('"git"', src)

    def test_the_bootstrap_skill_delegates_the_lifecycle(self):
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("rice.py", skill)
        self.assertIn("bootstrap", skill)
        for step in ("reconcile.apply", "bootstrap_state.enter_waiting_for_matt", "repair.py"):
            self.assertNotIn(step, skill, f"the skill still performs: {step}")
        # the hand-off contract survives the rewrite
        self.assertIn("/mattpocock-skills:setup-matt-pocock-skills", skill)
        self.assertNotIn('saying "bootstrap this repo with rice" again', skill)

    def test_the_update_skill_delegates_the_lifecycle(self):
        skill = (REPO / "skills" / "rice-update" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("rice.py", skill)
        for step in ("reconcile.apply", "repair.py"):
            self.assertNotIn(step, skill, f"the skill still performs: {step}")


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

    def test_bootstrap_verb_runs_the_whole_flow_from_one_command(self):
        """The real CLI, with real probes: rice is not installed in a test
        container, so this ends at the environment items -- which is exactly
        the flow having run end to end and stopped where only the OWNER can
        continue."""
        r = self._run("bootstrap", str(self.root), "--ref", REF, "--archive", "1", "--output-style", "none")
        self.assertIn("repair:", r.stdout, r.stdout + r.stderr)
        self.assertIn("rice.md", r.stdout)
        self.assertIn("AGENTS.md:rice-pointer", r.stdout)
        s = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(reconcile._get_path(s, settings_template.REF_PATH), REF)
        self.assertNotIn("outputStyle", s)

    def test_continue_verb_is_quiet_when_there_is_nothing_to_do(self):
        r = self._run("continue", str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to continue", r.stdout)


if __name__ == "__main__":
    unittest.main()
