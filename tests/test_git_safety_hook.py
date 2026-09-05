"""Tests for scripts/git_safety_hook.py (ticket #17).

Run: python3 -m unittest tests.test_git_safety_hook -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from git_safety_hook import decide  # noqa: E402


class TestBareGitBranchDecision(unittest.TestCase):
    # Bare positional-argument creation -> deny.
    def test_bare_name_denied(self):
        self.assertEqual(decide("git branch my-new-branch"), "deny")

    def test_bare_name_with_path_like_slug_denied(self):
        self.assertEqual(decide("git branch feature/foo"), "deny")

    # No argument at all -> listing -> allow.
    def test_no_argument_allowed(self):
        self.assertEqual(decide("git branch"), "allow")

    # Recognized listing/inspection flags -> allow.
    def test_listing_flags_allowed(self):
        for flag_form in [
            "git branch -a",
            "git branch -r",
            "git branch -v",
            "git branch -vv",
            "git branch --list",
            "git branch --show-current",
            "git branch --all",
            "git branch --remotes",
        ]:
            self.assertEqual(decide(flag_form), "allow", flag_form)

    # Deletion/rename flags: not this hook's job (permission deny rules in
    # #15 handle -D; -d/-m/-M are not destructive enough to deny at all),
    # so this hook allows them through -- it only distinguishes creation
    # from listing.
    def test_deletion_and_rename_flags_allowed_by_this_hook(self):
        for flag_form in [
            "git branch -d old-branch",
            "git branch -D old-branch",
            "git branch -m new-name",
            "git branch -M new-name",
        ]:
            self.assertEqual(decide(flag_form), "allow", flag_form)

    # Not "git branch" at all -> not this hook's concern -> allow
    # (other subcommands are handled by native permission deny rules).
    def test_unrelated_git_subcommands_not_denied_by_this_hook(self):
        for other in ["git checkout main", "git status", "git branch --help"]:
            self.assertEqual(decide(other), "allow", other)


if __name__ == "__main__":
    unittest.main()
