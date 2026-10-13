"""Tests for the OWNER's working rules shipped in resources/rice.md
(RICE-SPEC-001 §2, §9): OWNER-triggered convergence in its fixed order, no
invented branches, CLAUDE.md routing-only, session-history cold behind the
History Skill key -- each stated once.

Run: python3 -m unittest tests.test_working_rules -v
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reconcile  # noqa: E402
import release  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RULES = reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:] if nxt < 0 else text[start:nxt]


class TestConvergence(unittest.TestCase):
    SECTION = _section(RULES, "## Merging back to the default branch")

    def test_only_the_owners_explicit_request_triggers_it(self):
        s = self.SECTION
        self.assertIn("合併回主枝", s)
        self.assertIn("only", s.lower())
        self.assertRegex(s, r"(?i)never .*(on your own|infer|guess)")

    def test_the_seven_steps_appear_numbered_in_the_frozen_order(self):
        steps = re.findall(r"(?m)^(\d+)\. (.+)$", self.SECTION)
        self.assertEqual([int(n) for n, _ in steps], list(range(1, 8)))
        markers = ("ExitWorktree", "git switch", "git fetch", "merge --ff-only",
                   "git push origin", "merge-base --is-ancestor", "remote")
        for (_, line), marker in zip(steps, markers):
            self.assertIn(marker, line)

    def test_preconditions_and_stop_without_deleting(self):
        s = self.SECTION
        for precondition in ("clean", "default branch", "origin"):
            self.assertIn(precondition, s)
        self.assertRegex(s, r"(?i)stop.*delet(e|ing) nothing|nothing is deleted")
        self.assertIn("delete_branch_on_merge", s.replace("Automatically delete head branches", "delete_branch_on_merge"))
        self.assertRegex(s, r"(?i)never .*local branch")
        for forbidden in ("--force", "rebase", "git branch -D"):
            self.assertIn(forbidden, s)  # named only as things never to do

    def test_the_cleanup_uses_the_existing_helper_by_the_path_the_session_names(self):
        self.assertIn("delete_remote_branch.py", self.SECTION)
        self.assertNotIn("/root/", self.SECTION)  # no guessed plugin path in a repo file


class TestPolicyLines(unittest.TestCase):
    def test_platform_branches_are_workspaces_and_none_is_invented(self):
        s = _section(RULES, "## Branches")
        self.assertIn("workspace", s)
        self.assertRegex(s, r"(?i)never (create|invent)")

    def test_claude_md_is_routing_only(self):
        s = _section(RULES, "## CLAUDE.md")
        self.assertIn("routing", s)
        for always_loaded in ("@import", ".claude/rules", "paths:", "MEMORY.md", "AGENTS.md"):
            self.assertIn(always_loaded, s)

    def test_session_history_is_cold_behind_the_key(self):
        s = _section(RULES, "## Session history")
        self.assertIn("/rice:history", s)
        self.assertIn("/clear", s)
        self.assertRegex(s, r"(?i)do not read")

    def test_each_rule_is_stated_once_and_carries_no_ticket_archaeology(self):
        for heading in ("## Merging back to the default branch", "## Branches", "## CLAUDE.md",
                        "## Session history"):
            self.assertEqual(RULES.count(heading), 1, heading)
        self.assertNotRegex(RULES, r"RICE-[A-Z]+-\d+")
        for kept in ("## Control hierarchy", "## Conversation numbering", "## Questions to the OWNER"):
            self.assertIn(kept, RULES)


class TestReadme(unittest.TestCase):
    def test_one_line_each_and_still_short(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertEqual(readme.count("/rice:history"), 1)
        self.assertEqual(readme.count("合併回主枝"), 1)
        self.assertLessEqual(len(readme.splitlines()), 80)
        self.assertEqual(release.gate_readme(REPO).state, "PASS")

    def test_it_says_how_an_update_reaches_a_repo_and_what_a_pin_means(self):
        """RICE-SPEC-001 I15 / I19 (amended by RICE-ONE-COMMAND-UPDATE-001):
        one `/rice:update`; the hand-made way for a rice that predates it; a
        pin never moves on its own."""
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        bootstrap = _section(readme, "## Bootstrap a repo")
        self.assertIn("/rice:update", bootstrap)
        self.assertIn("asking nothing", bootstrap)
        # RICE-UPDATE-PRESAVE-001: running it to update is the go-ahead to save first
        self.assertRegex(bootstrap, r"(?is)commits\s+and\s+pushes.*\.gitignore")
        # the session keeps its old hooks until the OWNER reloads: said, not hidden
        self.assertRegex(bootstrap, r"(?is)hooks\s+load\s+the\s+new\s+build\s+after\s+`/reload-plugins`")
        # RICE-WEB-UPDATE-DOC-001: /reload-plugins does not work on the web; the README says so
        self.assertRegex(bootstrap, r"(?is)on\s+the\s+web,\s+only\s+a\s+new\s+session.*`/reload-plugins`\s+is\s+not\s+supported")
        # a rice without the command: refresh the marketplace first (alone, `plugin update` says "latest")
        self.assertRegex(bootstrap, r"(?is)without\s+`/rice:update`.*marketplace\s+update.*plugin\s+update"
                                    r".*`/rice:bootstrap`\s+in\s+a\s+new\s+session")
        install = _section(readme, "## Install once")
        self.assertRegex(install, r"(?is)pinned.*stays on that release until you change the pin")
        self.assertRegex(install, r"(?is)unpinned.*latest")
        self.assertNotIn("rice-update", readme)


class TestChangelogDraft(unittest.TestCase):
    def test_the_top_entry_is_the_build_and_the_release_that_carries_update_safety_says_so(self):
        """The top entry is plugin.json's version. The update-safety lines
        belong to the entry of the release that introduced them, 0.1.7, and
        stay there as later releases are cut above it."""
        log = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertEqual(release.changelog_top_version(REPO), release.plugin_version(REPO))
        carrier = _section(log, "## [0.1.7]")
        for behaviour in ("rice-version:not-newer", "nothing is written", "/rice:bootstrap"):
            self.assertIn(behaviour, carrier)


if __name__ == "__main__":
    unittest.main()
