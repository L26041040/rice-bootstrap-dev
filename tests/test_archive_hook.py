"""Tests for scripts/archive_hook.py (ticket #22).

Run: python3 -m unittest tests.test_archive_hook -v

Uses tests/fixtures/sample_transcript.jsonl -- a synthetic transcript built
for this ticket (no raw JSONL transcript exists in this repo; the only
session-history/ fixture on disk is a *rendered* .md output, not an input
transcript). It exercises every acceptance criterion: a GitHub-token-shaped
credential, a thinking block, a tool_use/tool_result pair, an oversized
message, and a trailing /clear marker.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_hook  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_transcript.jsonl"


def _git(root, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check
    )


def _init_repo(root: Path, bare_remote: Path) -> None:
    _git(bare_remote, "init", "-q", "--bare", "-b", "main")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", str(bare_remote))
    (root / "README.md").write_text("init\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "push", "-q", "-u", "origin", "main")


def _payload(repo_root: Path) -> dict:
    return {
        "session_id": "abcd1234-0000-0000-0000-000000000000",
        "transcript_path": str(FIXTURE),
        "cwd": str(repo_root),
        "permission_mode": "default",
        "hook_event_name": "SessionEnd",
        "reason": "clear",
    }


def _run_main(payload: dict, archive_enabled: bool) -> tuple[int, str]:
    env = {"RICE_SESSION_ARCHIVE": "1"} if archive_enabled else {}
    stdin = io.StringIO(json.dumps(payload))
    stderr = io.StringIO()
    with mock.patch.dict("os.environ", env, clear=False), \
         mock.patch("sys.stdin", stdin), \
         contextlib.redirect_stderr(stderr):
        rc = archive_hook.main()
    return rc, stderr.getvalue()


class TestRedact(unittest.TestCase):
    def test_github_token(self):
        s = "token: ghp_1234567890abcdefghijklmnopqrstuvwxyz12 end"
        out = archive_hook.redact(s)
        self.assertIn("[REDACTED:github-token]", out)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz12", out)

    def test_openai_key(self):
        out = archive_hook.redact("key=sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn("[REDACTED:openai-key]", out)

    def test_aws_key(self):
        out = archive_hook.redact("AKIAABCDEFGHIJKLMNOP")
        self.assertIn("[REDACTED:aws-key]", out)

    def test_slack_token(self):
        out = archive_hook.redact("xoxb-1234567890-abcdefghijklmn")
        self.assertIn("[REDACTED:slack-token]", out)

    def test_jwt(self):
        out = archive_hook.redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123XYZ")
        self.assertIn("[REDACTED:jwt]", out)

    def test_bearer_swallows_whatever_follows(self):
        out = archive_hook.redact("Authorization: Bearer sometoken1234567890")
        self.assertIn("[REDACTED:bearer-token]", out)
        self.assertNotIn("sometoken1234567890", out)

    def test_private_key_block(self):
        block = "-----BEGIN PRIVATE KEY-----\nMIIBVQ==\n-----END PRIVATE KEY-----"
        out = archive_hook.redact(f"here: {block}")
        self.assertIn("[REDACTED:private-key]", out)
        self.assertNotIn("MIIBVQ==", out)

    def test_plain_text_untouched(self):
        s = "just an ordinary sentence with no secrets"
        self.assertEqual(archive_hook.redact(s), s)


class TestTruncate(unittest.TestCase):
    def test_short_message_untouched(self):
        s = "short"
        self.assertEqual(archive_hook.truncate_message(s), s)

    def test_oversized_message_truncated_with_marker(self):
        s = "z" * 5000
        out = archive_hook.truncate_message(s, limit=100)
        self.assertTrue(out.startswith("z" * 100))
        self.assertIn("[TRUNCATED:", out)
        self.assertLess(len(out), len(s))


class TestIndexRow(unittest.TestCase):
    def test_append_creates_header_then_row(self):
        with tempfile.TemporaryDirectory() as d:
            index_path = Path(d) / "INDEX.md"
            row = archive_hook.render_index_row("2026-09-05", "title", "excerpt", 4, "foo.md")
            appended = archive_hook.append_index_row(index_path, row, "foo.md")
            self.assertTrue(appended)
            content = index_path.read_text()
            self.assertIn("foo.md", content)
            self.assertIn("Session Archive Index", content)

    def test_append_is_idempotent_per_filename(self):
        with tempfile.TemporaryDirectory() as d:
            index_path = Path(d) / "INDEX.md"
            row = archive_hook.render_index_row("2026-09-05", "title", "excerpt", 4, "foo.md")
            archive_hook.append_index_row(index_path, row, "foo.md")
            appended_again = archive_hook.append_index_row(index_path, row, "foo.md")
            self.assertFalse(appended_again)
            self.assertEqual(index_path.read_text().count("foo.md"), 1)


class TestArchiveHookMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._remote_tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        _init_repo(self.repo_root, Path(self._remote_tmp.name))

    def tearDown(self):
        self._tmp.cleanup()
        self._remote_tmp.cleanup()

    def test_disabled_no_op_no_file_changes(self):
        rc, _ = _run_main(_payload(self.repo_root), archive_enabled=False)
        self.assertEqual(rc, 0)
        self.assertFalse((self.repo_root / "session-history").exists())

    def test_enabled_success_archive_and_index_committed_together(self):
        rc, err = _run_main(_payload(self.repo_root), archive_enabled=True)
        self.assertEqual(rc, 0, err)
        archive_dir = self.repo_root / "session-history"
        md_files = list(archive_dir.glob("*.md"))
        md_files = [p for p in md_files if p.name != "INDEX.md"]
        self.assertEqual(len(md_files), 1)
        content = md_files[0].read_text()
        self.assertNotIn("internal reasoning", content)  # thinking excluded
        self.assertNotIn("echo hi", content)  # tool_use excluded
        index_content = (archive_dir / "INDEX.md").read_text()
        self.assertIn(md_files[0].name, index_content)
        # both files landed in the same commit
        committed = _git(self.repo_root, "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertIn(f"session-history/{md_files[0].name}", committed)
        self.assertIn("session-history/INDEX.md", committed)

    def test_credential_redacted_in_archive_and_index_excerpt(self):
        _run_main(_payload(self.repo_root), archive_enabled=True)
        archive_dir = self.repo_root / "session-history"
        md_files = [p for p in archive_dir.glob("*.md") if p.name != "INDEX.md"]
        content = md_files[0].read_text()
        self.assertIn("[REDACTED:github-token]", content)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz12", content)
        index_content = (archive_dir / "INDEX.md").read_text()
        self.assertIn("[REDACTED:github-token]", index_content)
        self.assertNotIn("ghp_1234567890abcdefghijklmnopqrstuvwxyz12", index_content)

    def test_oversized_message_truncated_with_marker(self):
        _run_main(_payload(self.repo_root), archive_enabled=True)
        archive_dir = self.repo_root / "session-history"
        md_files = [p for p in archive_dir.glob("*.md") if p.name != "INDEX.md"]
        content = md_files[0].read_text()
        self.assertIn("[TRUNCATED:", content)
        self.assertNotIn("y" * 5000, content)

    def test_commit_failure_writes_no_files(self):
        hook = self.repo_root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        rc, err = _run_main(_payload(self.repo_root), archive_enabled=True)
        self.assertNotEqual(rc, 0)
        self.assertIn("FAIL", err)
        md_files = [p for p in (self.repo_root / "session-history").glob("*.md")
                    if p.exists() and p.name != "INDEX.md"] if (self.repo_root / "session-history").exists() else []
        self.assertEqual(md_files, [])
        index_path = self.repo_root / "session-history" / "INDEX.md"
        self.assertFalse(index_path.exists())
        log = _git(self.repo_root, "log", "--oneline").stdout
        self.assertEqual(len(log.strip().splitlines()), 1)  # only the init commit

    def test_push_failure_keeps_local_commit_and_does_not_block(self):
        _git(self.repo_root, "remote", "set-url", "origin", "/nonexistent/does-not-exist.git")
        rc, err = _run_main(_payload(self.repo_root), archive_enabled=True)
        self.assertNotEqual(rc, 0)
        self.assertIn("FAIL", err)
        self.assertIn("local commit remains", err)
        archive_dir = self.repo_root / "session-history"
        md_files = [p for p in archive_dir.glob("*.md") if p.name != "INDEX.md"]
        self.assertEqual(len(md_files), 1)
        log = _git(self.repo_root, "log", "--oneline").stdout
        self.assertEqual(len(log.strip().splitlines()), 2)  # init + archive commit

    def test_running_twice_does_not_duplicate(self):
        _run_main(_payload(self.repo_root), archive_enabled=True)
        rc2, _ = _run_main(_payload(self.repo_root), archive_enabled=True)
        self.assertEqual(rc2, 0)
        archive_dir = self.repo_root / "session-history"
        md_files = [p for p in archive_dir.glob("*.md") if p.name != "INDEX.md"]
        self.assertEqual(len(md_files), 1)
        index_content = (archive_dir / "INDEX.md").read_text()
        self.assertEqual(index_content.count(md_files[0].name), 1)
        log = _git(self.repo_root, "log", "--oneline").stdout
        self.assertEqual(len(log.strip().splitlines()), 2)  # no second archive commit


if __name__ == "__main__":
    unittest.main()
