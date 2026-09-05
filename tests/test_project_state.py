"""Tests for scripts/project_state.py (ticket #21).

Run: python3 -m unittest tests.test_project_state -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from project_state import (  # noqa: E402
    TEMPLATE,
    create_if_missing,
    preserve_or_restore,
)


class TestProjectStateLifecycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)
        self.path = self.repo_root / "PROJECT_STATE.md"

    def tearDown(self):
        self._tmp.cleanup()

    # --- bootstrap: create_if_missing ---

    def test_create_if_missing_writes_template_when_absent(self):
        self.assertFalse(self.path.exists())
        create_if_missing(self.repo_root)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_text(), TEMPLATE)

    def test_create_if_missing_does_not_touch_existing_content(self):
        custom = "# My in-progress notes\nsomething custom here\n"
        self.path.write_text(custom)
        create_if_missing(self.repo_root)
        self.assertEqual(self.path.read_text(), custom)

    # --- rice-update: preserve_or_restore ---

    def test_preserve_or_restore_leaves_existing_content_untouched(self):
        custom = "# Now\ndoing something specific\n## Blockers\nnone\n"
        self.path.write_text(custom)
        preserve_or_restore(self.repo_root)
        self.assertEqual(self.path.read_text(), custom, "content must be byte-for-byte preserved")

    def test_preserve_or_restore_writes_template_when_missing(self):
        self.assertFalse(self.path.exists())
        preserve_or_restore(self.repo_root)
        self.assertEqual(self.path.read_text(), TEMPLATE)

    def test_preserve_or_restore_never_partially_rewrites(self):
        # A file with content that happens to look like a stale/older
        # template variant must still be preserved verbatim -- this
        # function never inspects content, only existence.
        weird = "not even markdown, just some text\x00binary-ish"
        self.path.write_bytes(weird.encode("utf-8", errors="surrogateescape"))
        preserve_or_restore(self.repo_root)
        self.assertEqual(
            self.path.read_bytes(),
            weird.encode("utf-8", errors="surrogateescape"),
        )

    def test_template_has_required_sections_and_is_short(self):
        for heading in ["Now", "Next", "Blockers", "Last updated"]:
            self.assertIn(heading, TEMPLATE)
        self.assertLessEqual(len(TEMPLATE.splitlines()), 40)


if __name__ == "__main__":
    unittest.main()
