"""Tests for scripts/output_styles.py and the verifier's output-style:choice
item (RICE-BOOTSTRAP-023): the OWNER's choice must resolve to a real style;
absent key is Claude's native default; rice never writes the key.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import output_styles  # noqa: E402
import reconcile  # noqa: E402
import settings_template  # noqa: E402


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _probe_with(plugins):
    return lambda cmd: _R(0, json.dumps(plugins))


class TestResolve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")}); self._env.start()

    def tearDown(self):
        self._env.stop(); self._tmp.cleanup()

    def test_absent_is_native_default(self):
        ok, detail = output_styles.resolve(None, self.root, _probe_with([]))
        self.assertTrue(ok); self.assertIn("native default", detail)

    def test_builtins_resolve(self):
        for b in output_styles.BUILTIN:
            self.assertTrue(output_styles.resolve(b, self.root, _probe_with([]))[0], b)

    def test_project_style_resolves_by_frontmatter_name_not_filename(self):
        d = self.root / ".claude" / "output-styles"; d.mkdir(parents=True)
        (d / "laodi.md").write_text("---\nname: 老弟\ndescription: x\n---\nbody\n", encoding="utf-8")
        self.assertTrue(output_styles.resolve("老弟", self.root, _probe_with([]))[0])
        self.assertFalse(output_styles.resolve("laodi", self.root, _probe_with([]))[0])

    def test_user_style_resolves(self):
        d = self.root / "cfg" / "output-styles"; d.mkdir(parents=True)
        (d / "mine.md").write_text("---\nname: mine\n---\n", encoding="utf-8")
        self.assertTrue(output_styles.resolve("mine", self.root, _probe_with([]))[0])

    def test_plugin_style_is_namespaced(self):
        proot = self.root / "plug"; (proot / "output-styles").mkdir(parents=True)
        (proot / "output-styles" / "s.md").write_text("---\nname: fancy\n---\n", encoding="utf-8")
        probe = _probe_with([{"id": "other@mp", "installPath": str(proot)}])
        self.assertTrue(output_styles.resolve("other:fancy", self.root, probe)[0])
        self.assertFalse(output_styles.resolve("fancy", self.root, probe)[0])  # bare name != plugin style

    def test_rice_bundled_style_resolves_via_this_build(self):
        ok, detail = output_styles.resolve(settings_template.RICE_STYLE, self.root, _probe_with([]),
                                           self_plugin=(settings_template.PLUGIN_NAME, reconcile.PLUGIN_ROOT))
        self.assertTrue(ok, detail)

    def test_nonexistent_style_fails(self):
        self.assertFalse(output_styles.resolve("nope", self.root, _probe_with([]))[0])
        self.assertFalse(output_styles.resolve("", self.root, _probe_with([]))[0])
        self.assertFalse(output_styles.resolve(123, self.root, _probe_with([]))[0])

    def test_available_lists_every_source(self):
        d = self.root / ".claude" / "output-styles"; d.mkdir(parents=True)
        (d / "p.md").write_text("---\nname: proj\n---\n", encoding="utf-8")
        got = {s["value"]: s["source"] for s in output_styles.available(self.root, _probe_with([]),
                                                                           self_plugin=("rice", reconcile.PLUGIN_ROOT))}
        self.assertEqual(got["proj"], "project")
        self.assertIn(settings_template.RICE_STYLE, got)
        self.assertEqual(got["default"], "builtin")


class TestVerifierChoiceItem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")}); self._env.start()
        (self.root / ".claude").mkdir()
        self.probe = _probe_with([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(), "scope": "user", "enabled": True}])

    def tearDown(self):
        self._env.stop(); self._tmp.cleanup()

    def _state(self, settings):
        (self.root / ".claude" / "settings.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        items = reconcile.check(self.root, rtk_probe=lambda c: _R(0, "rtk"), plugin_probe=self.probe)
        return {i.name: i for i in items}["output-style:choice"]

    def test_default_absent_passes(self):
        self.assertEqual(self._state(settings_template.desired_settings("v1", True, output_style=None)).state, "PASS")

    def test_rice_style_passes(self):
        self.assertEqual(self._state(settings_template.desired_settings("v1", True)).state, "PASS")

    def test_custom_project_style_passes_only_if_file_exists(self):
        s = settings_template.desired_settings("v1", True, output_style="brand")
        self.assertEqual(self._state(s).state, "FAIL")
        d = self.root / ".claude" / "output-styles"; d.mkdir()
        (d / "brand.md").write_text("---\nname: brand\n---\n", encoding="utf-8")
        self.assertEqual(self._state(s).state, "PASS")

    def test_dangling_value_fails(self):
        self.assertEqual(self._state(settings_template.desired_settings("v1", True, output_style="ghost:style")).state, "FAIL")


if __name__ == "__main__":
    unittest.main()
