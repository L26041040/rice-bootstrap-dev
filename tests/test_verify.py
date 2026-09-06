"""Tests for scripts/verify.py (RICE-BOOTSTRAP-021): the verifier is the only
authority, and no required non-PASS can ever yield a VERIFIED verdict.

Run: python3 -m unittest tests.test_verify -v
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reconcile  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402
from bootstrap_state import enter_waiting_for_matt, is_waiting_for_matt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _all_pass_items():
    """One PASS item per required name of Gate B (which includes Gate A)."""
    return [reconcile.ItemResult(n, "PASS", kind="x") for n in verify.GATE_B]


class TestGateInvariant(unittest.TestCase):
    """The invariant, exhaustively: for every required item and every
    non-PASS state, the gate fails."""

    def test_all_pass_passes(self):
        ok, reasons = verify.gate_passes(_all_pass_items(), verify.GATE_B)
        self.assertTrue(ok, reasons)

    def test_every_required_item_every_non_pass_state_fails_gate(self):
        for name in verify.GATE_B:
            for bad in ("FAIL", "NOT_VERIFIED", "ERROR", "UNKNOWN", "pass", "PASS ", "", "created", "noop"):
                items = [i if i.name != name else reconcile.ItemResult(name, bad, kind="x") for i in _all_pass_items()]
                gate = verify.GATE_A if name in verify.GATE_A else verify.GATE_B
                ok, reasons = verify.gate_passes(items, gate)
                self.assertFalse(ok, f"{name}={bad!r} must fail the gate")
                self.assertTrue(any(r.startswith(name + ":") for r in reasons), reasons)

    def test_missing_required_item_fails_gate(self):
        for name in verify.GATE_B:
            items = [i for i in _all_pass_items() if i.name != name]
            gate = verify.GATE_A if name in verify.GATE_A else verify.GATE_B
            ok, reasons = verify.gate_passes(items, gate)
            self.assertFalse(ok, name)
            self.assertTrue(any("MISSING" in r and r.startswith(name) for r in reasons), reasons)

    def test_extra_unknown_items_cannot_make_a_gate_pass(self):
        items = [reconcile.ItemResult("something-else", "PASS", kind="x")]
        ok, _ = verify.gate_passes(items, verify.GATE_A)
        self.assertFalse(ok)

    def test_gate_b_is_a_superset_of_gate_a(self):
        self.assertTrue(set(verify.GATE_A) <= set(verify.GATE_B))
        self.assertEqual(set(verify.GATE_B) - set(verify.GATE_A), set(verify.GATE_B_EXTRA))

    def test_every_item_the_rubric_produces_is_required_somewhere(self):
        """No item may exist outside the gates -- that would be an
        'informational' escape hatch the agent could rationalize."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_correct_repo(root)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}):
                items = reconcile.check(root, rtk_probe=_ok_rtk, plugin_probe=_installed)
        names = {i.name for i in items}
        self.assertEqual(names - set(verify.GATE_B), set(), "unrequired items exist")
        self.assertEqual(set(verify.GATE_B) - names, set(), "required items the rubric never produces")


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(),
                              "scope": "user", "enabled": True}]))


def _make_correct_repo(root: Path, *, matt=False):
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    s = settings_template.desired_settings("v9.9.9", True)
    (root / ".claude" / "settings.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    (root / ".claude" / "rice.md").write_text(reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    claude_md = f"# proj\n\n{reconcile.IMPORT_LINE}\n"
    if matt:
        claude_md += "\n## Agent skills\n\nsee docs/agents\n"
        (root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "agents" / "issue-tracker.md").write_text("x", encoding="utf-8")
    (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    (root / "PROJECT_STATE.md").write_text("# PROJECT_STATE\n", encoding="utf-8")


def _write_transcript(root: Path, *, git_denied=True, opus_declined=True, ts="2099-01-01T00:00:00.000Z"):
    tdir = reconcile.transcript_dir(root)
    tdir.mkdir(parents=True, exist_ok=True)
    recs = []
    if git_denied:
        recs += [
            {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git branch --show-current"}}]}},
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "Permission to use Bash with command git branch --show-current has been denied."}]}},
        ]
    if opus_declined:
        recs += [
            {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"model": "opus", "prompt": "hi"}}]}},
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
                 "content": "The user doesn't want to proceed with this tool use."}]}},
        ]
    (tdir / "session.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _write_archive_evidence(root: Path):
    sh = root / "session-history"
    sh.mkdir(exist_ok=True)
    (sh / "2026-01-01_000000+0000_abc.md").write_text("# x\n", encoding="utf-8")
    (sh / "INDEX.md").write_text("# Session Archive Index\n\n| Date | Title | Excerpt | Turns | File |\n|---|---|---|---|---|\n"
                                 "| 2026-01-01 | t | e | 1 | 2026-01-01_000000+0000_abc.md |\n", encoding="utf-8")


class VerifierRunBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def run_verify(self, final=False):
        return verify.run(self.root, final=final, rtk_probe=_ok_rtk, plugin_probe=_installed)


class TestGateA(VerifierRunBase):
    def test_full_gate_a_passes_only_with_git_deny_evidence(self):
        _make_correct_repo(self.root)
        rep = self.run_verify()
        self.assertFalse(rep.gate_a)
        self.assertTrue(any(l.startswith("RICE-GATE-A FAIL") for l in rep.lines))
        self.assertTrue(any("git-deny:behaves: NOT_VERIFIED" in l for l in rep.lines))
        _write_transcript(self.root, opus_declined=False)
        rep = self.run_verify()
        self.assertTrue(rep.gate_a, rep.lines)
        self.assertIn(f"RICE-VERIFIED {reconcile.build_version()}", rep.lines)

    def test_gate_a_never_writes_version(self):
        _make_correct_repo(self.root)
        _write_transcript(self.root)
        rep = self.run_verify()
        self.assertTrue(rep.gate_a)
        self.assertIsNone(rep.version_written)
        self.assertFalse((self.root / verify.VERSION_FILE).exists())

    def test_stale_denial_older_than_settings_does_not_count(self):
        _make_correct_repo(self.root)
        _write_transcript(self.root, ts="2000-01-01T00:00:00.000Z")  # older than settings.json mtime
        rep = self.run_verify()
        self.assertFalse(rep.gate_a)

    def test_wrong_probe_command_does_not_count(self):
        _make_correct_repo(self.root)
        tdir = reconcile.transcript_dir(self.root); tdir.mkdir(parents=True)
        recs = [
            {"type": "assistant", "timestamp": "2099-01-01T00:00:00Z", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git branch probe"}}]}},
            {"type": "user", "timestamp": "2099-01-01T00:00:00Z", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "denied"}]}},
        ]
        (tdir / "s.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
        self.assertFalse(self.run_verify().gate_a)


class TestGateBAndVersionAuthority(VerifierRunBase):
    def test_final_pass_writes_version_and_clears_marker(self):
        _make_correct_repo(self.root, matt=True)
        _write_transcript(self.root)
        _write_archive_evidence(self.root)
        enter_waiting_for_matt(self.root)
        rep = self.run_verify(final=True)
        self.assertTrue(rep.gate_b, rep.lines)
        self.assertEqual(rep.version_written, reconcile.build_version())
        self.assertEqual((self.root / verify.VERSION_FILE).read_text().strip(), reconcile.build_version())
        self.assertIn(f"FINAL-VERIFIED {reconcile.build_version()}", rep.lines)
        self.assertFalse(is_waiting_for_matt(self.root))

    def test_final_fail_leaves_existing_version_byte_identical_and_marker(self):
        _make_correct_repo(self.root, matt=False)  # matt:artifacts FAIL
        _write_transcript(self.root)
        _write_archive_evidence(self.root)
        enter_waiting_for_matt(self.root)
        vf = self.root / verify.VERSION_FILE
        vf.write_text("0.0.1\n", encoding="utf-8")
        before = vf.read_bytes()
        rep = self.run_verify(final=True)
        self.assertFalse(rep.gate_b)
        self.assertIsNone(rep.version_written)
        self.assertEqual(vf.read_bytes(), before)
        self.assertTrue(is_waiting_for_matt(self.root))
        self.assertTrue(any(l.startswith("FINAL FAIL") for l in rep.lines))

    def test_each_gate_b_extra_item_alone_blocks_final(self):
        # archive evidence missing
        _make_correct_repo(self.root, matt=True); _write_transcript(self.root)
        self.assertFalse(self.run_verify(final=True).gate_b)
        # opus decline missing
        _write_archive_evidence(self.root); _write_transcript(self.root, opus_declined=False)
        self.assertFalse(self.run_verify(final=True).gate_b)
        # rtk missing
        _write_transcript(self.root)
        rep = verify.run(self.root, final=True, rtk_probe=lambda c: (_ for _ in ()).throw(FileNotFoundError()), plugin_probe=_installed)
        self.assertFalse(rep.gate_b)
        self.assertFalse((self.root / verify.VERSION_FILE).exists())

    def test_collection_exception_fails_closed(self):
        _make_correct_repo(self.root, matt=True)
        with mock.patch.object(reconcile, "check", side_effect=RuntimeError("boom")):
            rep = verify.run(self.root, final=True)
        self.assertFalse(rep.gate_a); self.assertFalse(rep.gate_b)
        self.assertEqual(rep.items[0].state, "ERROR")
        self.assertFalse((self.root / verify.VERSION_FILE).exists())


class TestSoleAuthority(unittest.TestCase):
    """Nothing but verify.py may emit the verdict strings, and reconcile has
    no verdict at all."""

    def test_reconcile_check_returns_items_only_without_verdict_words(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); _make_correct_repo(root)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}):
                out = reconcile.check(root, rtk_probe=_ok_rtk, plugin_probe=_installed)
        self.assertIsInstance(out, list)
        blob = " ".join(f"{i.name} {i.state} {i.detail}" for i in out)
        self.assertNotRegex(blob, r"RICE-VERIFIED|FINAL-VERIFIED|MECHANICAL-VERIFIED")

    def test_only_verify_py_emits_verdict_strings(self):
        pattern = re.compile(r'"(RICE-VERIFIED|FINAL-VERIFIED)|f"(RICE-VERIFIED|FINAL-VERIFIED)')
        for py in (REPO / "scripts").glob("*.py"):
            if py.name == "verify.py":
                continue
            self.assertIsNone(pattern.search(py.read_text(encoding="utf-8")), f"{py.name} emits a verdict string")

    def test_only_verify_writes_rice_version(self):
        for py in (REPO / "scripts").glob("*.py"):
            if py.name == "verify.py":
                continue
            self.assertNotIn("rice-version", py.read_text(encoding="utf-8"), f"{py.name} touches rice-version")

    def test_cli_exit_code_follows_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); _make_correct_repo(root)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}), \
                 mock.patch.object(reconcile, "_default_plugin_probe", _installed), \
                 mock.patch.object(reconcile, "_default_rtk_probe", _ok_rtk):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = verify.main([str(root)])
                self.assertEqual(rc, 1)
                self.assertIn("RICE-GATE-A FAIL", buf.getvalue())
                _write_transcript(root)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = verify.main([str(root)])
                self.assertEqual(rc, 0)
                self.assertIn("RICE-VERIFIED", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
