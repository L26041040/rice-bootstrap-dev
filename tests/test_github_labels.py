"""RICE-BOOTSTRAP-UX-FIX-001: Matt's triage labels exist on GitHub before
rice says FINAL-VERIFIED -- created from Matt's own vocabulary, only the
missing ones, through the REST API (never gh), and a refusal is one clear
authorization instruction, never a pretended pass.

Run: python3 -m unittest tests.test_github_labels -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evidence  # noqa: E402
import github_labels  # noqa: E402
import repair  # noqa: E402
import rice  # noqa: E402
import verify  # noqa: E402

MATT = Path("/root/.claude/plugins/marketplaces/mattpocock/skills/engineering/setup-matt-pocock-skills")
# Matt's own template, as his setup writes it with the default answers
TRIAGE_TEMPLATE = """# Triage Labels

The skills speak in terms of five canonical triage roles.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |
"""
DEFAULTS = ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"]


class FakeGitHub:
    """The REST calls rice makes, against an in-memory repository."""

    def __init__(self, labels=(), refuse=None, refuse_writes=None):
        self.labels, self.refuse, self.refuse_writes, self.calls = list(labels), refuse, refuse_writes, []

    def __call__(self, method, url, body=None):
        self.calls.append((method, url, body))
        if self.refuse or (method == "POST" and self.refuse_writes):
            return self.refuse or self.refuse_writes, {"message": "Resource not accessible by integration"}
        if method == "GET":
            return 200, [{"name": n} for n in self.labels]
        if any(n.lower() == body["name"].lower() for n in self.labels):
            return 422, {"errors": [{"code": "already_exists"}]}
        self.labels.append(body["name"])
        return 201, {"name": body["name"]}

    def created(self):
        return [b["name"] for m, _, b in self.calls if m == "POST"]


class LabelsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin",
                        "https://github.com/owner/project.git"], check=True)
        self.tracker("# Issue tracker: GitHub\n\nIssues live in GitHub Issues.\n")
        self.triage(TRIAGE_TEMPLATE)

    def tearDown(self):
        self._tmp.cleanup()

    def tracker(self, text):
        p = self.root / "docs" / "agents" / "issue-tracker.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def triage(self, text):
        (self.root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "agents" / "triage-labels.md").write_text(text, encoding="utf-8")


class TestTheVocabularyIsMatts(LabelsBase):
    def test_names_and_meanings_come_from_matts_file(self):
        self.assertEqual(github_labels.vocabulary(self.root)[0], ("needs-triage", "Maintainer needs to evaluate this issue"))
        self.assertEqual([n for n, _ in github_labels.vocabulary(self.root)], DEFAULTS)

    def test_matts_shipped_template_parses_to_the_five(self):
        if not (MATT / "triage-labels.md").is_file():
            self.skipTest("Matt's plugin is not installed here")
        self.triage((MATT / "triage-labels.md").read_text(encoding="utf-8"))
        self.assertEqual([n for n, _ in github_labels.vocabulary(self.root)], DEFAULTS)
        first = (MATT / "issue-tracker-github.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, github_labels.GITHUB_TRACKER_HEADING)

    def test_the_owners_own_names_win(self):
        self.triage(TRIAGE_TEMPLATE.replace("| `needs-triage`       |", "| `bug:triage`         |"))
        self.assertIn("bug:triage", [n for n, _ in github_labels.vocabulary(self.root)])
        self.assertNotIn("needs-triage", [n for n, _ in github_labels.vocabulary(self.root)])

    def test_other_trackers_have_none(self):
        for heading in ("# Issue tracker: Local Markdown", "# Issue tracker: GitLab"):
            with self.subTest(heading):
                self.tracker(heading + "\n")
                self.assertIsNone(github_labels.vocabulary(self.root))
                self.assertFalse(github_labels.check(self.root, FakeGitHub()).applies)

    def test_matts_heading_may_vary_in_case_and_words(self):
        for heading in ("# Issue Tracker: GitHub Issues", "# issue tracker: github"):
            with self.subTest(heading):
                self.tracker(heading + "\n")
                self.assertEqual(len(github_labels.vocabulary(self.root)), 5)

    def test_an_unreadable_vocabulary_is_never_a_pass(self):
        self.triage("# Triage Labels\n\nsomething else entirely\n")
        c = github_labels.check(self.root, FakeGitHub())
        self.assertTrue(c.applies)
        self.assertIn("no label could be read", c.error)

    def test_proxied_and_unusual_origins(self):
        cases = {"http://local_proxy@127.0.0.1:4567/git/owner/project": ("https://api.github.com", "owner/project"),
                 "ssh://git@github.com:22/owner/project.git": ("https://api.github.com", "owner/project"),
                 "https://user:p@ss@github.com/owner/project": ("https://api.github.com", "owner/project"),
                 "https://git.example.com/owner/project.git": ("https://git.example.com/api/v3", "owner/project"),
                 "/srv/git/project.git": None, "http://127.0.0.1:4567/owner/project": None}
        for url, want in cases.items():
            with self.subTest(url):
                subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin", url], check=True)
                self.assertEqual(github_labels.repository(self.root), want)

    def test_the_repository_comes_from_origin(self):
        for url in ("https://github.com/owner/project.git", "git@github.com:owner/project.git",
                    "ssh://git@github.com/owner/project", "https://x-access-token@github.com/owner/project"):
            with self.subTest(url):
                subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin", url], check=True)
                self.assertEqual(github_labels.repository(self.root), ("https://api.github.com", "owner/project"))


class TestOnlyTheMissingAreCreated(LabelsBase):
    def test_missing_labels_are_created_once_and_a_re_run_creates_nothing(self):
        gh = FakeGitHub(labels=["ready-for-agent", "WontFix", "bug"])
        created, repo, why = github_labels.create_missing(self.root, gh)
        self.assertEqual((created, repo, why), (["needs-triage", "needs-info", "ready-for-human"], "owner/project", ""))
        self.assertEqual(gh.calls[1][2], {"name": "needs-triage", "description": "Maintainer needs to evaluate this issue"})
        again = FakeGitHub(labels=gh.labels)
        self.assertEqual(github_labels.create_missing(self.root, again), ([], "owner/project", ""))
        self.assertEqual(again.created(), [])

    def test_a_refusal_is_one_authorization_instruction_never_a_pass(self):
        created, repo, why = github_labels.create_missing(self.root, FakeGitHub(refuse=403))
        self.assertEqual(created, [])
        self.assertIn("HTTP 403", why)
        self.assertIn("GH_TOKEN", why)
        self.assertIn("run the same command again", why)

    def test_a_non_github_origin_is_said_plainly(self):
        subprocess.run(["git", "-C", str(self.root), "remote", "set-url", "origin", "/srv/git/project.git"], check=True)
        c = github_labels.check(self.root, FakeGitHub())
        self.assertTrue(c.applies)
        self.assertIn("not a GitHub repository", c.error)

    def test_no_gh_is_needed(self):
        """A token in the environment is used as is -- no subprocess at all;
        without one (and with no git credential) gh is asked only if it is
        installed, and its absence is no error."""
        github_labels._token.cache_clear()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "t0ken"}), \
                mock.patch.object(github_labels.subprocess, "run") as run:
            self.assertEqual(github_labels._token(), "t0ken")
        run.assert_not_called()
        github_labels._token.cache_clear()
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}), \
                mock.patch.object(github_labels.shutil, "which", return_value=None), \
                mock.patch.object(github_labels.subprocess, "run", return_value=failed) as run:
            self.assertIsNone(github_labels._token())
        self.assertEqual([c.args[0][-2:] for c in run.call_args_list], [["credential", "fill"]])
        self.assertEqual(run.call_args.kwargs["env"]["GCM_INTERACTIVE"], "never")   # never a sign-in window
        github_labels._token.cache_clear()

    def test_the_credential_goes_only_to_its_own_host(self):
        seen = []

        def capture(req, timeout):
            seen.append((req.full_url, req.get_header("Authorization")))
            raise github_labels.urllib.error.URLError("offline")
        github_labels._token.cache_clear()
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"GH_TOKEN": "t0ken"}), \
                mock.patch.object(github_labels.shutil, "which", return_value=None), \
                mock.patch.object(github_labels.subprocess, "run", return_value=failed), \
                mock.patch.object(github_labels.urllib.request, "urlopen", side_effect=capture):
            github_labels.default_probe("GET", "https://api.github.com/repos/o/r/labels")
            status, why = github_labels.default_probe("GET", "https://git.example.com/api/v3/repos/o/r/labels")
        github_labels._token.cache_clear()
        self.assertEqual(seen, [("https://api.github.com/repos/o/r/labels", "Bearer t0ken"),
                                ("https://git.example.com/api/v3/repos/o/r/labels", None)])
        self.assertEqual(status, 0)
        self.assertNotIn("t0ken", str(why))

    def test_pages_are_followed(self):
        class Paged(FakeGitHub):
            def __call__(self, method, url, body=None):
                if method == "GET":
                    self.calls.append((method, url, body))
                    page = int(url.rsplit("page=", 1)[1])
                    return 200, ([{"name": f"other-{i}"} for i in range(100)] if page == 1 else [{"name": "wontfix"}])
                return super().__call__(method, url, body)
        gh = Paged()
        self.assertEqual(github_labels.check(self.root, gh).missing, DEFAULTS[:4])


class TestTheVerifierAndRepairUseIt(LabelsBase):
    def _item(self, gh):
        return {i.name: i for i in verify._collect(self.root, final=True, github_probe=gh)}["matt:labels"]

    def test_only_final_verification_asks_github(self):
        gh = FakeGitHub()
        names = {i.name for i in verify._collect(self.root, github_probe=gh)}
        self.assertEqual(("matt:labels" in names, gh.calls), (False, []))

    def test_the_item_fails_until_the_labels_exist(self):
        self.assertEqual(self._item(FakeGitHub(labels=["wontfix"])).state, "FAIL")
        self.assertIn("needs-triage", self._item(FakeGitHub(labels=["wontfix"])).detail)
        self.assertEqual(self._item(FakeGitHub(labels=DEFAULTS)).state, "PASS")
        self.assertEqual(self._item(FakeGitHub(refuse=404)).state, "NOT_VERIFIED")
        self.tracker("# Issue tracker: Local Markdown\n")
        self.assertEqual(self._item(FakeGitHub()).state, "PASS")

    def test_it_is_a_gate_b_item(self):
        self.assertIn("matt:labels", verify.GATE_B)
        self.assertNotIn("matt:labels", verify.GATE_A)

    def test_repair_creates_what_the_verifier_found_missing(self):
        gh = FakeGitHub(labels=["wontfix"])
        report = verify.Report(build="x", items=[self._item(gh)], gate_a=True, gate_b=False,
                               version_written=None, verdict="v")
        with mock.patch.object(verify.Report, "failing", new=property(lambda self: self.items)):
            result = repair.repair(self.root, report, github_probe=gh)
        self.assertEqual(result.applied, ["matt:labels: created needs-triage, needs-info, ready-for-agent, "
                                          "ready-for-human on owner/project"])
        self.assertEqual(self._item(gh).state, "PASS")

    def _repair(self, gh):
        report = verify.Report(build="x", items=[self._item(gh)], gate_a=True, gate_b=False,
                               version_written=None, verdict="v")
        with mock.patch.object(verify.Report, "failing", new=property(lambda self: self.items)):
            return report, repair.repair(self.root, report, github_probe=gh)

    def test_a_refused_create_is_the_one_authorization_instruction(self):
        _, result = self._repair(FakeGitHub(labels=["wontfix"], refuse_writes=403))
        self.assertEqual(result.applied, [])
        self.assertEqual(len(result.unresolved), 1)
        self.assertIn("GH_TOKEN", result.unresolved[0])

    def test_an_unreadable_repository_is_said_once_by_the_verifier_alone(self):
        report, result = self._repair(FakeGitHub(refuse=403))
        self.assertIn("GH_TOKEN", report.items[0].detail)
        self.assertEqual(result.unresolved, [])


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


class TestTheLifecycleWithAGitHubTracker(unittest.TestCase):
    """Bootstrap, Matt's setup choosing GitHub Issues, then the Stop
    continuation: the labels are created before FINAL-VERIFIED, a re-run
    creates none, and a refusal reaches the OWNER exactly once."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.work, remote = tmp / "work", tmp / "remote.git"
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(tmp / "cfg")})
        self._env.start()
        git = lambda *a: subprocess.run(["git", "-C", str(self.work), *a], check=True, capture_output=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        self.work.mkdir()
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com"); git("config", "user.name", "T")
        # fetched from GitHub, pushed to a local bare repo: nothing leaves the machine
        git("remote", "add", "origin", "https://github.com/owner/project.git")
        git("remote", "set-url", "--push", "origin", str(remote))
        (self.work / "README.md").write_text("project\n")
        git("add", "README.md"); git("commit", "-q", "-m", "init")
        tdir = evidence.transcript_dir(self.work); tdir.mkdir(parents=True)
        ts = "2099-01-01T00:00:00.000Z"
        (tdir / "s.jsonl").write_text("\n".join(json.dumps(r) for r in (
            {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": evidence.GIT_DENY_PROBE}}]}},
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Permission to use Bash has been denied."}]}},
        )) + "\n")
        self.probes = {
            "rtk_probe": lambda cmd: _R(0, "rtk 0.46.0\n"),
            "plugin_probe": lambda cmd: _R(0, json.dumps([{"id": "rice@rice-bootstrap", "scope": "user",
                                                           "version": evidence.build_version(), "enabled": True}])),
        }
        self.assertEqual(rice.bootstrap(self.work, archive=False, output_style=None, **self.probes).stage,
                         "waiting-for-matt")
        # Matt's setup: the OWNER chose GitHub Issues and the default vocabulary
        claude_md = self.work / "CLAUDE.md"
        claude_md.write_text(claude_md.read_text() + "\n## Agent skills\n\nGitHub Issues.\n")
        agents = self.work / "docs" / "agents"
        agents.mkdir(parents=True)
        (agents / "issue-tracker.md").write_text("# Issue tracker: GitHub\n\nIssues live in GitHub Issues.\n")
        (agents / "triage-labels.md").write_text(TRIAGE_TEMPLATE)
        (agents / "domain.md").write_text("owner answers\n")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_the_labels_exist_before_final_verified_and_a_re_run_creates_none(self):
        gh = FakeGitHub(labels=["wontfix", "bug"])
        final = rice.continue_after_matt(self.work, github_probe=gh, **self.probes)
        self.assertTrue(final.ok, final.lines)
        self.assertEqual(gh.created(), ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human"])
        self.assertIn("created needs-triage", rice.continuation_message(self.work, final))
        again = verify.run(self.work, final=True, github_probe=gh, **self.probes)
        self.assertTrue(again.ok)
        self.assertEqual(len(gh.created()), 4)  # nothing created twice

    def test_a_refusal_is_one_instruction_to_the_owner_and_never_final_verified(self):
        for gh in (FakeGitHub(labels=["wontfix"], refuse_writes=403), FakeGitHub(refuse=404)):
            outcome = rice.continue_after_matt(self.work, github_probe=gh, **self.probes)
            self.assertFalse(outcome.ok)
            self.assertFalse((self.work / verify.VERSION_FILE).exists())
            message = rice.continuation_message(self.work, outcome)
            self.assertEqual(message.count("set GH_TOKEN"), 1, message)

    def test_a_local_markdown_tracker_never_asks_github(self):
        (self.work / "docs" / "agents" / "issue-tracker.md").write_text("# Issue tracker: Local Markdown\n")
        gh = FakeGitHub()
        self.assertTrue(rice.continue_after_matt(self.work, github_probe=gh, **self.probes).ok)
        self.assertEqual(gh.calls, [])


if __name__ == "__main__":
    unittest.main()
