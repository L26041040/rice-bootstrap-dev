"""Tests for rice's PreToolUse guard (RICE-SPEC-001 §3, §4, §5): one plugin
hook that keeps CLAUDE.md routing-only once a repo's bootstrap has fully
completed, and keeps session-history/ closed unless the OWNER has handed over
the History Skill key in the current /clear segment.

Seam: a hook payload on stdin -> a deny decision (or silence) on stdout.

Run: python3 -m unittest tests.test_guard -v
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import export_public  # noqa: E402
import guard  # noqa: E402
import reconcile  # noqa: E402
import uninstall  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN_DESTINATIONS = ("CLAUDE.md", "@import", ".claude/rules", "paths:", "MEMORY.md", "AGENTS.md")


def _rice_repo(root: Path, *, finalized: bool) -> None:
    """A repo rice has bootstrapped: its own artifacts, and -- only once Gate B
    has passed -- the verifier-stamped version file."""
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "rice.md").write_text("rules\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text(f"{reconcile.IMPORT_LINE}\n", encoding="utf-8")
    (root / "session-history").mkdir(exist_ok=True)
    (root / "session-history" / "a.md").write_text("old conversation\n", encoding="utf-8")
    if finalized:
        (root / verify.VERSION_FILE).write_text("0.1.7\n", encoding="utf-8")


def _run(payload) -> str:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(raw)), redirect_stdout(out):
        rc = guard.main()
    assert rc == 0
    return out.getvalue()


def _decision(stdout: str):
    if not stdout.strip():
        return None
    d = json.loads(stdout)["hookSpecificOutput"]
    return d["permissionDecision"], d["permissionDecisionReason"]


class GuardBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, tool, tool_input, *, cwd=None, transcript=None):
        payload = {"hook_event_name": "PreToolUse", "cwd": str(cwd or self.root),
                   "tool_name": tool, "tool_input": tool_input}
        if transcript is not None:
            payload["transcript_path"] = str(transcript)
        return _decision(_run(payload))


class TestClaudeMdGuard(GuardBase):
    def test_a_finalized_repo_denies_every_file_tool_write_to_claude_md(self):
        _rice_repo(self.root, finalized=True)
        target = str(self.root / "CLAUDE.md")
        for tool, tool_input in (
                ("Edit", {"file_path": target, "old_string": "x", "new_string": "Status: done"}),
                ("Write", {"file_path": target, "content": "Status: done\n"}),
                ("NotebookEdit", {"notebook_path": target, "new_source": "Status"})):
            decision = self.call(tool, tool_input)
            self.assertIsNotNone(decision, tool)
            self.assertEqual(decision[0], "deny", tool)

    def test_the_reason_diverts_without_naming_a_destination(self):
        _rice_repo(self.root, finalized=True)
        _, reason = self.call("Write", {"file_path": str(self.root / "CLAUDE.md"), "content": "x"})
        self.assertIn("routing only", reason)
        self.assertIn("not load automatically", reason)
        for forbidden in FORBIDDEN_DESTINATIONS:
            self.assertIn(forbidden, reason, forbidden)
        for named in ("docs/status.md", "NOTES.md", "STATUS.md"):
            self.assertNotIn(named, reason)
        self.assertIn("OWNER edits it directly", reason)

    def test_bash_writes_to_claude_md_are_denied_in_every_form(self):
        _rice_repo(self.root, finalized=True)
        for command in ("echo 'Status: done' >> CLAUDE.md",
                        "printf x > ./CLAUDE.md",
                        f"echo x >> {self.root}/CLAUDE.md",
                        "echo x | tee -a CLAUDE.md",
                        "echo x | rtk tee CLAUDE.md",
                        'cat notes.txt >> "CLAUDE.md"'):
            decision = self.call("Bash", {"command": command})
            self.assertIsNotNone(decision, command)
            self.assertEqual(decision[0], "deny", command)

    def test_reading_claude_md_and_writing_other_files_pass(self):
        _rice_repo(self.root, finalized=True)
        for tool, tool_input in (
                ("Read", {"file_path": str(self.root / "CLAUDE.md")}),
                ("Bash", {"command": "cat CLAUDE.md"}),
                ("Bash", {"command": "grep -n Agent CLAUDE.md > /tmp/x.txt"}),
                ("Write", {"file_path": str(self.root / "docs" / "notes.md"), "content": "Status"}),
                ("Edit", {"file_path": str(self.root / "sub" / "CLAUDE.md"), "old_string": "a", "new_string": "b"}),
                ("Bash", {"command": "echo x >> NOTES.md"})):
            self.assertIsNone(self.call(tool, tool_input), (tool, tool_input))

    def test_before_bootstrap_completion_claude_md_writes_pass(self):
        """Matt's setup writes its routing block during bootstrap, before the
        verifier stamps the version file -- so no exception is ever needed."""
        _rice_repo(self.root, finalized=False)
        target = str(self.root / "CLAUDE.md")
        self.assertIsNone(self.call("Edit", {"file_path": target, "old_string": reconcile.IMPORT_LINE,
                                             "new_string": reconcile.IMPORT_LINE + "\n\n## Agent skills\n"}))
        self.assertIsNone(self.call("Bash", {"command": "echo x >> CLAUDE.md"}))

    def test_after_uninstall_the_guard_is_silent_again(self):
        _rice_repo(self.root, finalized=True)
        uninstall.uninstall(self.root)
        self.assertIsNone(self.call("Write", {"file_path": str(self.root / "CLAUDE.md"), "content": "x"}))

    def test_a_repo_without_rice_is_never_governed(self):
        (self.root / "CLAUDE.md").write_text("theirs\n")
        (self.root / ".claude").mkdir()
        (self.root / verify.VERSION_FILE).write_text("0.1.7\n")  # stray file, no rice artifacts
        self.assertIsNone(self.call("Write", {"file_path": str(self.root / "CLAUDE.md"), "content": "x"}))

    def test_a_subdirectory_cwd_still_resolves_the_repo(self):
        _rice_repo(self.root, finalized=True)
        (self.root / ".git").mkdir()
        sub = self.root / "src" / "pkg"; sub.mkdir(parents=True)
        decision = self.call("Bash", {"command": "echo x >> ../../CLAUDE.md"}, cwd=sub)
        self.assertEqual(decision[0], "deny")

    def test_the_guard_never_allows_asks_or_rewrites_and_never_writes_files(self):
        _rice_repo(self.root, finalized=True)
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        out = _run({"cwd": str(self.root), "tool_name": "Write",
                    "tool_input": {"file_path": str(self.root / "CLAUDE.md"), "content": "x"}})
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(set(spec), {"hookEventName", "permissionDecision", "permissionDecisionReason"})
        self.assertEqual(spec["hookEventName"], "PreToolUse")
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob("*")), before)
        src = (REPO / "scripts" / "guard.py").read_text(encoding="utf-8")
        for never in ('"allow"', '"ask"', "updatedInput"):
            self.assertNotIn(never, src)

    def test_malformed_payloads_and_internal_errors_are_silent(self):
        for raw in ("", "not json", "[]", json.dumps({"cwd": str(self.root / "nowhere"), "tool_name": "Write"})):
            self.assertEqual(_run(raw), "", raw)
        _rice_repo(self.root, finalized=True)
        with mock.patch.object(guard, "decide", side_effect=RuntimeError("boom")):
            self.assertEqual(_run({"cwd": str(self.root), "tool_name": "Write",
                                   "tool_input": {"file_path": str(self.root / "CLAUDE.md")}}), "")


class TestWiringAndInvariants(unittest.TestCase):
    def test_the_hook_is_wired_as_one_pretooluse_entry_and_ships(self):
        cfg = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        entries = cfg["PreToolUse"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]["matcher"].split("|")),
                         {"Edit", "Write", "NotebookEdit", "Read", "Grep", "Glob", "Bash"})
        self.assertIn("scripts/guard.py", entries[0]["hooks"][0]["command"])
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", entries[0]["hooks"][0]["command"])
        self.assertIn("scripts/guard.py", export_public.ALLOWLIST)

    def test_the_existing_hooks_are_untouched(self):
        cfg = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual([e.get("matcher") for e in cfg["SessionEnd"]], ["clear"])
        self.assertEqual([e.get("matcher") for e in cfg["SessionStart"]], ["clear", None])
        self.assertEqual([e.get("matcher") for e in cfg["Stop"]], [None])

    def test_the_hooks_json_command_runs_the_guard_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); _rice_repo(root, finalized=True)
            cmd = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            payload = json.dumps({"cwd": str(root), "tool_name": "Write",
                                  "tool_input": {"file_path": str(root / "CLAUDE.md"), "content": "x"}})
            r = subprocess.run(["bash", "-c", cmd], input=payload, capture_output=True, text=True,
                               env={"CLAUDE_PLUGIN_ROOT": str(REPO), "PATH": "/usr/bin:/bin"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_rice_owned_state_and_gates_are_unchanged(self):
        import settings_template
        self.assertEqual(len(settings_template.RICE_DENY_ENTRIES), 28)
        self.assertEqual(set(reconcile.OWNED_FILES), {".claude/rice.md", ".claude/WAITING_FOR_MATT"})
        self.assertNotIn("guard", " ".join(verify.GATE_A + verify.GATE_B))


if __name__ == "__main__":
    unittest.main()
