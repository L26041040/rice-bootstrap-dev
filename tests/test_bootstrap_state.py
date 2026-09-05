"""Tests for scripts/bootstrap_state.py (ticket #25's WAITING_FOR_MATT marker).

Run: python3 -m unittest tests.test_bootstrap_state -v

Clearing this marker on successful Matt completion is #28's job (blocked on
#27's runtime probe) -- this module only covers what #25 itself needs:
entering the pending state, and checking it so a re-run of bootstrap on an
already-pending repo doesn't redo already-finished work.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from bootstrap_state import (  # noqa: E402
    enter_waiting_for_matt,
    is_waiting_for_matt,
)


class TestWaitingForMattMarker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_not_waiting_initially(self):
        self.assertFalse(is_waiting_for_matt(self.repo_root))

    def test_enter_sets_the_marker(self):
        enter_waiting_for_matt(self.repo_root)
        self.assertTrue(is_waiting_for_matt(self.repo_root))

    def test_entering_twice_is_idempotent(self):
        enter_waiting_for_matt(self.repo_root)
        first = (self.repo_root / ".claude" / "WAITING_FOR_MATT").read_text()
        enter_waiting_for_matt(self.repo_root)
        second = (self.repo_root / ".claude" / "WAITING_FOR_MATT").read_text()
        self.assertEqual(first, second)

    def test_creates_claude_dir_if_missing(self):
        self.assertFalse((self.repo_root / ".claude").exists())
        enter_waiting_for_matt(self.repo_root)
        self.assertTrue(is_waiting_for_matt(self.repo_root))


if __name__ == "__main__":
    unittest.main()
