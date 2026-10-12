"""Tests for scripts/bootstrap_continue.py (RICE-BOOTSTRAP-028): the Stop
hook that continues bootstrap after the OWNER's Matt setup, so a fresh
bootstrap costs exactly one `/rice:bootstrap`.

The hook decides nothing (RICE-BOOTSTRAP-021): it only decides *when* to ask
the verifier. Every guard below must be silent -- an ordinary turn, in any
repo, must not notice this hook exists.

Run: python3 -m unittest tests.test_bootstrap_continue -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bootstrap_continue  # noqa: E402
import evidence  # noqa: E402
import reconcile  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _missing_rtk(cmd):
    raise FileNotFoundError("rtk")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": evidence.build_version(),
                              "scope": "user", "enabled": True}]))


def _make_correct_repo(root: Path, *, matt: bool):
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    s = settings_template.desired_settings(True)
    (root / ".claude" / "settings.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    (root / ".claude" / "rice.md").write_text(reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    claude_md = f"# proj\n\n{reconcile.IMPORT_LINE}\n"
    if matt:
        claude_md += "\n## Agent skills\n\n### Issue tracker\n\nsee docs/agents\n"
        (root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "agents" / "issue-tracker.md").write_text("x", encoding="utf-8")
        (root / "docs" / "agents" / "domain.md").write_text("x", encoding="utf-8")
    (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    (root / "AGENTS.md").write_text(reconcile.AGENTS_POINTER, encoding="utf-8")


def _write_transcript(root: Path, ts="2099-01-01T00:00:00.000Z"):
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


def _payload(repo_root: Path, *, stop_hook_active=False) -> dict:
    return {"session_id": "s1", "transcript_path": "/tmp/x.jsonl", "cwd": str(repo_root),
            "permission_mode": "default", "hook_event_name": "Stop",
            "stop_hook_active": stop_hook_active, "last_assistant_message": "done"}


def _run_hook(payload) -> tuple[int, str, str]:
    stdin = io.StringIO(json.dumps(payload) if isinstance(payload, dict) else payload)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("sys.stdin", stdin), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = bootstrap_continue.main()
    return rc, out.getvalue(), err.getvalue()


class ContinuationBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")})
        self._env.start()
        # the hook calls verify/reconcile without probes, so the module-level
        # defaults are what it actually uses
        self._real_plugin, self._real_rtk = evidence._default_plugin_probe, evidence._default_rtk_probe
        evidence._default_plugin_probe = _installed
        evidence._default_rtk_probe = _ok_rtk

    def tearDown(self):
        evidence._default_plugin_probe = self._real_plugin
        evidence._default_rtk_probe = self._real_rtk
        self._env.stop()
        self._tmp.cleanup()

    def _ready_repo(self):
        """Everything Gate B needs: Gate A clean, Matt artifacts present, RTK
        present, and bootstrap still waiting for Matt."""
        _make_correct_repo(self.root, matt=True)
        _write_transcript(self.root)
        reconcile.enter_waiting_for_matt(self.root)

    def assertSilent(self, rc, out, err):
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    @property
    def version_file(self) -> Path:
        return self.root / verify.VERSION_FILE


class TestGuardsAreSilent(ContinuationBase):
    def test_marker_absent_is_silent(self):
        _make_correct_repo(self.root, matt=True)
        _write_transcript(self.root)
        rc, out, err = _run_hook(_payload(self.root))
        self.assertSilent(rc, out, err)
        self.assertFalse(self.version_file.exists())

    def test_matt_not_yet_run_is_silent(self):
        """Nothing of Matt's exists: the OWNER has not run the setup, there is
        nothing to continue and nothing to repair."""
        _make_correct_repo(self.root, matt=False)
        _write_transcript(self.root)
        reconcile.enter_waiting_for_matt(self.root)
        rc, out, err = _run_hook(_payload(self.root))
        self.assertSilent(rc, out, err)
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_rtk_missing_is_reported_not_silent(self):
        """RICE-BOOTSTRAP-042: once Matt's setup has run, nothing the
        environment lacks may leave the bootstrap silently pending."""
        evidence._default_rtk_probe = _missing_rtk
        self._ready_repo()
        rc, out, err = _run_hook(_payload(self.root))
        self.assertEqual(rc, 2)
        self.assertIn("rtk", err)
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_stop_hook_active_is_silent(self):
        self._ready_repo()
        rc, out, err = _run_hook(_payload(self.root, stop_hook_active=True))
        self.assertSilent(rc, out, err)
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_missing_or_malformed_payload_is_silent(self):
        for payload in ("", "not json", "[]", json.dumps({"hook_event_name": "Stop"})):
            rc, out, err = _run_hook(payload)
            self.assertSilent(rc, out, err)

    def test_internal_exception_never_breaks_the_turn(self):
        self._ready_repo()
        with mock.patch.object(verify, "run", side_effect=RuntimeError("boom")):
            rc, out, err = _run_hook(_payload(self.root))
        self.assertSilent(rc, out, err)


class TestContinuation(ContinuationBase):
    def test_ready_runs_the_real_final_verifier_and_hands_off(self):
        self._ready_repo()
        rc, out, err = _run_hook(_payload(self.root))
        self.assertEqual(rc, 2, err)  # exit 2 -> stderr reaches the model
        self.assertIn(f"FINAL-VERIFIED {evidence.build_version()}", err)
        self.assertIn("Commit and push", err)
        # the verifier -- not the hook -- did the writing
        self.assertEqual(self.version_file.read_text().strip(), evidence.build_version())
        self.assertFalse(reconcile.is_waiting_for_matt(self.root))

    def test_idempotent_second_run_is_silent(self):
        self._ready_repo()
        self.assertEqual(_run_hook(_payload(self.root))[0], 2)
        before = self.version_file.read_bytes()
        rc, out, err = _run_hook(_payload(self.root))
        self.assertSilent(rc, out, err)  # marker gone: nothing left to continue
        self.assertEqual(self.version_file.read_bytes(), before)

    def test_final_fail_reports_blockers_only(self):
        self._ready_repo()
        shutil.rmtree(evidence.transcript_dir(self.root))  # git-deny:behaves: no evidence, nothing repairs that
        rc, out, err = _run_hook(_payload(self.root))
        self.assertEqual(rc, 2)
        self.assertIn("did not pass", err)
        self.assertIn("git-deny:behaves", err)
        self.assertNotIn("FINAL-VERIFIED", err)
        # never tell the OWNER to run rice again (the verifier's own "re-run
        # verify" detail is the model's instruction, not the OWNER's)
        for forbidden in ("/rice:bootstrap", "bootstrap this repo with rice", "rice again", "re-run rice", "rerun rice"):
            self.assertNotIn(forbidden, err)
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_hook_never_writes_version_or_clears_marker_itself(self):
        """Sole authority (RICE-BOOTSTRAP-021): stamping the version and
        clearing the marker are the verifier's, even on the PASS path. Proven
        by handing the hook a verifier that reports PASS but writes nothing:
        if the hook did any of it itself, these would still change."""
        self._ready_repo()
        passing_but_inert = verify.Report(build="9.9.9", items=[], gate_a=True, gate_b=True,
                                          version_written=None, lines=["FINAL-VERIFIED 9.9.9"],
                                          required=verify.GATE_B, verdict="FINAL-VERIFIED 9.9.9")
        with mock.patch.object(verify, "run", return_value=passing_but_inert):
            rc, _, err = _run_hook(_payload(self.root))
        self.assertEqual(rc, 2)
        self.assertIn("FINAL-VERIFIED 9.9.9", err)
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_hook_runs_no_git_command(self):
        source = (REPO / "scripts" / "bootstrap_continue.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn('"git"', source)

    def test_matt_artifacts_are_never_rewritten(self):
        self._ready_repo()
        docs = self.root / "docs" / "agents"
        before = {p.name: p.read_bytes() for p in docs.iterdir()}
        claude_md_before = (self.root / "CLAUDE.md").read_bytes()
        _run_hook(_payload(self.root))
        self.assertEqual({p.name: p.read_bytes() for p in docs.iterdir()}, before)
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), claude_md_before)


class TestHandoffIsNeverLostOrDuplicated(ContinuationBase):
    """Two failure modes found in review of the first implementation."""

    def test_relay_failure_after_a_pass_still_extends_the_turn(self):
        """Once verify.run has stamped the version and cleared the marker, the
        hook will never see this bootstrap again. If writing the hand-off
        fails, falling back to silence would strand a finished bootstrap
        uncommitted -- so the exit status must still be the hand-off."""
        self._ready_repo()

        class _Boom(io.StringIO):
            def write(self, _):
                raise BrokenPipeError("stderr gone")

        stdin = io.StringIO(json.dumps(_payload(self.root)))
        with mock.patch("sys.stdin", stdin), contextlib.redirect_stderr(_Boom()):
            rc = bootstrap_continue.main()
        self.assertEqual(rc, 2)
        self.assertEqual(self.version_file.read_text().strip(), evidence.build_version())

    def test_each_external_probe_runs_at_most_once_per_hook_run(self):
        """The readiness check and the verifier ask the same two questions;
        this hook runs at the end of every turn, so duplicate spawns are pure
        latency."""
        calls = []

        def counting_plugin(cmd):
            calls.append(tuple(cmd))
            return _installed(cmd)

        def counting_rtk(cmd):
            calls.append(tuple(cmd))
            return _ok_rtk(cmd)

        evidence._default_plugin_probe = counting_plugin
        evidence._default_rtk_probe = counting_rtk
        self._ready_repo()
        self.assertEqual(_run_hook(_payload(self.root))[0], 2)
        duplicated = [c for c in set(calls) if calls.count(c) > 1]
        self.assertEqual(duplicated, [], f"probe spawned more than once: {calls}")
        self.assertIn(("rtk", "--version"), calls)

    def test_fail_message_tells_the_model_to_finish_in_the_same_turn(self):
        """`stop_hook_active` mutes this hook for the rest of the turn it
        spoke in, so a blocker the model clears inside that turn would never
        be re-verified unless the message says to run the verifier itself."""
        self._ready_repo()
        shutil.rmtree(evidence.transcript_dir(self.root))
        _, _, err = _run_hook(_payload(self.root))
        self.assertIn("verify.py", err)
        self.assertIn("--final", err)
        self.assertIn("this same turn", err)


class TestIncompleteMattIsNeverSilent(ContinuationBase):
    """RICE-BOOTSTRAP-042: the moment Matt's setup has left anything behind,
    an incomplete artifact set goes through the pipeline and is reported --
    never left silently pending."""

    def test_partial_matt_output_is_reported_with_what_repair_refused(self):
        _make_correct_repo(self.root, matt=True)
        (self.root / "docs" / "agents" / "domain.md").unlink()  # never committed: not reconstructible
        _write_transcript(self.root)
        reconcile.enter_waiting_for_matt(self.root)
        rc, out, err = _run_hook(_payload(self.root))
        self.assertEqual(rc, 2)
        self.assertIn("did not pass", err)
        self.assertIn("Deterministic repair ran first", err)
        self.assertIn("docs/agents/domain.md", err)
        self.assertIn("only the official Matt setup", err)
        self.assertIn("last fallback", err)
        self.assertFalse((self.root / "docs" / "agents" / "domain.md").exists())  # nothing invented
        self.assertFalse(self.version_file.exists())
        self.assertTrue(reconcile.is_waiting_for_matt(self.root))

    def test_rice_owned_drift_is_repaired_before_the_model_hears_of_it(self):
        self._ready_repo()
        (self.root / ".claude" / "rice.md").write_text("hand-edited drift\n", encoding="utf-8")
        (self.root / "AGENTS.md").unlink()
        rc, _, err = _run_hook(_payload(self.root))
        self.assertEqual(rc, 2)
        self.assertIn(f"FINAL-VERIFIED {evidence.build_version()}", err)
        self.assertIn("Deterministic repair restored", err)
        self.assertIn("rice.md", err)
        self.assertIn("AGENTS.md", err)
        self.assertEqual(self.version_file.read_text().strip(), evidence.build_version())


class TestSingleInvocationContract(unittest.TestCase):
    """RICE-BOOTSTRAP-028: a fresh bootstrap costs the OWNER exactly one
    `/rice:bootstrap`; nothing may ask for a second one."""

    def test_bootstrap_skill_promises_automatic_continuation(self):
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("bootstrap_continue.py", skill)
        self.assertIn("Stop", skill)
        # the superseded "ask the OWNER to invoke rice again" contract is gone
        self.assertNotIn('saying "bootstrap this repo with rice" again', skill)
        self.assertNotIn("again and I'll run the final verifier", skill)

    def test_the_skill_presents_the_engine_directive_rather_than_branching(self):
        """Which reply the OWNER gets -- a Matt hand-off, a verdict to quote,
        an install, blockers -- is the engine's decision (RICE-BOOTSTRAP-044B).
        The skill's contract is to pass it on verbatim and add nothing."""
        skill = (REPO / "skills" / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("present this to the OWNER verbatim", skill)
        self.assertIn("nothing outside it", skill)   # one code block, nothing before or after it

    def test_readme_describes_no_second_rice_invocation(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("Run it once per repo", readme)
        self.assertIn("finishes on its own", readme)
        self.assertNotIn("After the OWNER's Matt setup: `verify.py <repo> --final`", readme)
        self.assertNotIn("run `/rice:bootstrap` again", readme)


if __name__ == "__main__":
    unittest.main()
