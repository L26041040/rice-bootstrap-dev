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

import github_labels  # noqa: E402
import repair  # noqa: E402
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

    def __init__(self, labels=(), refuse=None):
        self.labels, self.refuse, self.calls = list(labels), refuse, []

    def __call__(self, method, url, body=None):
        self.calls.append((method, url, body))
        if self.refuse:
            return self.refuse, {"message": "Resource not accessible by integration"}
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
        """Without gh (and without any credential) the token is simply
        absent: reads go anonymous, nothing crashes, gh is never called."""
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}), \
                mock.patch.object(github_labels.shutil, "which", return_value=None), \
                mock.patch.object(github_labels.subprocess, "run", return_value=failed) as run:
            self.assertIsNone(github_labels._token())
        self.assertEqual([c.args[0][:2] for c in run.call_args_list], [["git", "credential"]])
        self.assertNotIn('"gh", "api"', Path(github_labels.__file__).read_text(encoding="utf-8"))


class TestTheVerifierAndRepairUseIt(LabelsBase):
    def _item(self, gh):
        return {i.name: i for i in verify._collect(self.root, github_probe=gh)}["matt:labels"]

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

    def test_repair_reports_a_refusal_unresolved(self):
        gh = FakeGitHub(refuse=403)
        report = verify.Report(build="x", items=[self._item(gh)], gate_a=True, gate_b=False,
                               version_written=None, verdict="v")
        with mock.patch.object(verify.Report, "failing", new=property(lambda self: self.items)):
            result = repair.repair(self.root, report, github_probe=gh)
        self.assertEqual(result.applied, [])
        self.assertTrue(result.unresolved[0].startswith("matt:labels: GitHub answered HTTP 403"))


if __name__ == "__main__":
    unittest.main()
