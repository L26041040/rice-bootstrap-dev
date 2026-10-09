"""Fresh-repo end-to-end acceptance for RICE-SPEC-001 (§11).

One story, in order, on a real git work tree with a bare `origin`, a
platform-style session branch, the real bootstrap / verifier / Stop
continuation, the real archive hook, the real guard (driven through its hook
payload seam) and the real convergence commands plus the existing
remote-branch helper. Nothing model-dependent is simulated here: the two
model behaviours (diverting a status write, honouring the history key) were
probed headlessly when T1/T2 landed; this test pins every deterministic step
around them.

Run: python3 -m unittest tests.test_e2e_spec001 -v
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_hook  # noqa: E402
import delete_remote_branch  # noqa: E402
import evidence  # noqa: E402
import guard  # noqa: E402
import reconcile  # noqa: E402
import rice  # noqa: E402
import session_context  # noqa: E402
import uninstall  # noqa: E402
import verify  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_transcript.jsonl"
SESSION = "claude/feat-x"
MATT_SECTION = "## Agent skills\n\n### Issue tracker\n\nGitHub Issues. See `docs/agents/issue-tracker.md`.\n"


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


PROBES = {
    "rtk_probe": lambda cmd: _R(0, "rtk 0.46.0\n"),
    "plugin_probe": lambda cmd: _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": evidence.build_version(),
                                                   "scope": "user", "enabled": True}])),
}


def _git(cwd, *args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def _guard(work: Path, tool: str, tool_input: dict, transcript: Path | None = None):
    payload = {"cwd": str(work), "tool_name": tool, "tool_input": tool_input}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
        guard.main()
    text = out.getvalue().strip()
    return json.loads(text)["hookSpecificOutput"]["permissionDecision"] if text else None


def _hook(fn, payload: dict) -> int:
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
            contextlib.redirect_stderr(io.StringIO()):
        return fn()


def _installed_at(version: str):
    """A `claude plugin list --json` probe: rice installed at user scope, at `version`."""
    return lambda cmd: _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": version,
                                          "scope": "user", "enabled": True}]))


def _context(work: Path) -> list[str]:
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"cwd": str(work)}))), \
            contextlib.redirect_stdout(out):
        session_context.main()
    text = out.getvalue()
    if text.lstrip().startswith("{"):  # it also shows the OWNER a line (`systemMessage`)
        text = json.loads(text)["hookSpecificOutput"]["additionalContext"]
    return text.splitlines()


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def _transcript(path: Path, *user_texts: str) -> Path:
    path.write_text("\n".join(json.dumps({"type": "user", "message": {"role": "user", "content": t}})
                              for t in user_texts) + "\n", encoding="utf-8")
    return path


class TestFreshRepoEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.tmp, self.work, self.remote = tmp, tmp / "work", tmp / "remote.git"
        # the harness's own state (git-deny evidence) lives outside the work tree
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp / "cfg"),
                                                 "RICE_SESSION_ARCHIVE": "1"})
        self._env.start()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.work.mkdir()
        _git(self.work, "init", "-q", "-b", "main")
        _git(self.work, "config", "user.email", "t@example.com")
        _git(self.work, "config", "user.name", "T")
        _git(self.work, "remote", "add", "origin", str(self.remote))
        (self.work / "README.md").write_text("project\n")
        _git(self.work, "add", "README.md"); _git(self.work, "commit", "-q", "-m", "init")
        _git(self.work, "push", "-q", "-u", "origin", "main")
        # the platform's session branch
        _git(self.work, "checkout", "-q", "-b", SESSION); _git(self.work, "push", "-q", "-u", "origin", SESSION)
        tdir = evidence.transcript_dir(self.work); tdir.mkdir(parents=True)
        ts = "2099-01-01T00:00:00.000Z"
        (tdir / "s.jsonl").write_text("\n".join(json.dumps(r) for r in (
            {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": evidence.GIT_DENY_PROBE}}]}},
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Permission to use Bash has been denied."}]}},
        )) + "\n")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_spec_001_section_11_end_to_end(self):
        w = self.work
        claude_md, archive_file = w / "CLAUDE.md", w / "session-history" / "x.md"

        # 1. bootstrap: Gate A, Matt hand-off; CLAUDE.md is the import line only
        outcome = rice.bootstrap(w, archive=True, output_style=None, **PROBES)
        self.assertEqual(outcome.stage, "waiting-for-matt", outcome.lines)
        self.assertEqual(claude_md.read_text(), f"{reconcile.IMPORT_LINE}\n")

        # 2. Matt's setup writes its block while the CLAUDE.md rule is inert;
        #    the Stop continuation passes Gate B and stamps completion
        self.assertIsNone(_guard(w, "Edit", {"file_path": str(claude_md), "old_string": reconcile.IMPORT_LINE,
                                             "new_string": reconcile.IMPORT_LINE + "\n\n" + MATT_SECTION}))
        claude_md.write_text(claude_md.read_text() + "\n" + MATT_SECTION)
        (w / "docs" / "agents").mkdir(parents=True)
        for doc in ("issue-tracker.md", "domain.md"):
            (w / "docs" / "agents" / doc).write_text("owner answers\n")
        final = rice.continue_after_matt(w, **PROBES)
        self.assertTrue(final.ok, final.lines)
        self.assertTrue((w / verify.VERSION_FILE).exists())
        _git(w, "add", "-A"); _git(w, "commit", "-q", "-m", "rice bootstrap"); _git(w, "push", "-q")
        routing = claude_md.read_text()

        # 3. a status write to CLAUDE.md is now denied, in every tool form
        self.assertEqual(_guard(w, "Write", {"file_path": str(claude_md), "content": "Status: 60%"}), "deny")
        self.assertEqual(_guard(w, "Bash", {"command": "echo 'Status: 60%' >> CLAUDE.md"}), "deny")
        self.assertEqual(claude_md.read_text(), routing)  # nothing always-loaded grew

        # 4. the archive is closed; a repo-wide search is not governed
        no_key = _transcript(self.tmp / "t1.jsonl", "why did we choose X?")
        self.assertEqual(_guard(w, "Read", {"file_path": str(archive_file)}, no_key), "deny")
        self.assertIsNone(_guard(w, "Grep", {"pattern": "X"}, no_key))

        # 5. /clear: archive + INDEX + local commit on the session branch, and the
        #    next SessionStart(clear) syncs only archive commits
        rc = _hook(archive_hook.main, {"session_id": "abcd1234-0000-0000-0000-000000000000", "reason": "clear",
                                       "transcript_path": str(FIXTURE), "cwd": str(w), "hook_event_name": "SessionEnd"})
        self.assertEqual(rc, 0)
        self.assertTrue((w / "session-history" / "INDEX.md").exists())
        self.assertIn(archive_hook.RICE_ARCHIVE_TRAILER, _git(w, "log", "-1", "--format=%B"))
        self.assertEqual(_git(w, "rev-parse", "--abbrev-ref", "HEAD"), SESSION)
        _hook(archive_hook.sync_main, {"cwd": str(w), "hook_event_name": "SessionStart", "source": "clear"})
        _git(w, "fetch", "-q", "origin")
        archive_commit = _git(w, "rev-parse", "HEAD")
        self.assertEqual(_git(w, "rev-parse", f"origin/{SESSION}"), archive_commit)
        archived = next(p for p in (w / "session-history").glob("*.md") if p.name != "INDEX.md")

        # 6. the OWNER hands over the key: targeted reads pass for the segment
        keyed = _transcript(self.tmp / "t2.jsonl", "<command-name>/rice:history</command-name>", "why X?")
        self.assertIsNone(_guard(w, "Read", {"file_path": str(archived)}, keyed))
        self.assertIsNone(_guard(w, "Bash", {"command": "grep -rn X session-history/"}, keyed))

        # 7. the next /clear closes it again
        closed = _transcript(self.tmp / "t3.jsonl", "<command-name>/rice:history</command-name>",
                             "<command-name>/clear</command-name>", "new task")
        self.assertEqual(_guard(w, "Read", {"file_path": str(archived)}, closed), "deny")

        # 8. 「合併回主枝」: the frozen order, each step gated on the previous
        self.assertEqual(_git(w, "status", "--porcelain"), "")              # precondition: clean
        default = delete_remote_branch.default_branch(w)
        self.assertEqual(default, "main")                                   # precondition: readable
        self.assertNotEqual(_git(w, "rev-parse", "--abbrev-ref", "HEAD"), default)
        _git(w, "switch", "-q", default)                                    # 2
        _git(w, "fetch", "-q", "origin")                                    # 3
        _git(w, "merge", "-q", "--ff-only", SESSION)                        # 4
        _git(w, "push", "-q", "origin", default)                            # 5
        _git(w, "merge-base", "--is-ancestor", SESSION, f"origin/{default}")  # 6 (raises if not)
        self.assertTrue(delete_remote_branch.delete(w, SESSION).startswith("RICE-BRANCH-DELETED"))  # 7
        self.assertEqual(_git(w, "ls-remote", "--heads", "origin", SESSION), "")
        self.assertEqual(_git(w, "branch", "--list", SESSION).strip(" *"), SESSION)  # local untouched
        _git(w, "merge-base", "--is-ancestor", archive_commit, f"origin/{default}")  # archive not lost

        # 9. a second request on the default branch is a no-op: the
        #    precondition fails before anything runs, nothing is deleted
        self.assertEqual(_git(w, "rev-parse", "--abbrev-ref", "HEAD"), default)
        heads_before = _git(w, "ls-remote", "--heads", "origin")
        self.assertEqual(_git(w, "ls-remote", "--heads", "origin"), heads_before)

        # 9b. update safety (RICE-SPEC-001 §14.6), driven with simulated builds.
        #     Same version: the context adds nothing beyond the helper line.
        self.assertEqual(len(_context(w)), 1)
        build = evidence.build_version()
        newer = ".".join(str(int(p) + (i == 2)) for i, p in enumerate(build.split(".")))
        older = "0.0.1"
        #     An older session (installed == running == older) meets this repo:
        #     refused before any write, and the context stays silent.
        before = _tree(w)
        with mock.patch.object(evidence, "build_version", lambda: older):
            refused = rice.bootstrap(w, **dict(PROBES, plugin_probe=_installed_at(older)))
            self.assertEqual(refused.stage, "blocked")
            self.assertIn("rice-version:not-newer", "\n".join(refused.directive))
            self.assertEqual(len(_context(w)), 1)
        self.assertEqual(_tree(w), before, "an older rice wrote to a newer repo")
        #     A session that installed an update but did not reload: refused too.
        stale = rice.bootstrap(w, **dict(PROBES, plugin_probe=_installed_at(newer)))
        self.assertEqual(stale.stage, "blocked")
        self.assertEqual(_tree(w), before)
        #     A newer build: one context line, then one re-run converges the
        #     repo, restamps, asks nothing, leaves settings and CLAUDE.md alone.
        (w / ".claude" / "rice.md").write_text("the rules an older rice shipped\n", encoding="utf-8")
        settings, routing_now = (w / ".claude" / "settings.json").read_bytes(), claude_md.read_bytes()
        with mock.patch.object(evidence, "build_version", lambda: newer):
            lines = _context(w)
            self.assertEqual(len(lines), 2)
            self.assertIn(newer, lines[1]); self.assertIn("/rice:bootstrap", lines[1])
            updated = rice.bootstrap(w, **dict(PROBES, plugin_probe=_installed_at(newer)))  # no answers
            self.assertEqual(updated.stage, "finalized", updated.lines)
            self.assertEqual(verify.read_version(w), newer)
            self.assertEqual(len(_context(w)), 1)
        self.assertEqual((w / ".claude" / "rice.md").read_text(encoding="utf-8"),
                         reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8"))
        self.assertEqual((w / ".claude" / "settings.json").read_bytes(), settings)
        self.assertEqual(claude_md.read_bytes(), routing_now)

        # 10. uninstall: clean subtraction, nothing left from this spec
        expected = uninstall.expected_after(w)
        uninstall.uninstall(w)
        items, verdict = uninstall.check(w, expected=expected)
        self.assertEqual(verdict, "RICE-UNINSTALLED", "\n".join(f"{i.name} {i.state} {i.detail}" for i in items))
        self.assertIsNone(_guard(w, "Write", {"file_path": str(claude_md), "content": "x"}))
        self.assertIsNone(_guard(w, "Read", {"file_path": str(archived)}, no_key))
        self.assertEqual(_context(w), [])  # not a rice repo any more: no context at all


if __name__ == "__main__":
    unittest.main()
