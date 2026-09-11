"""Tests for the ownership model (RICE-BOOTSTRAP-045).

`reconcile` is the single declaration of what rice owns in a target repo,
and every lifecycle is a verb over it: install and update converge it,
repair restores what the verifier failed, uninstall subtracts it. Two
invariants are pinned here, because both are the kind that only break in
someone else's repo:

  ownership is declared once -- no other module keeps a list;
  existing repos are safe -- project-owned bytes never disappear because
  rice was installed, updated or removed, and a collision that cannot be
  merged losslessly is reported, never guessed at.

Run: python3 -m unittest tests.test_ownership -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import reconcile  # noqa: E402
import repair  # noqa: E402
import settings_template  # noqa: E402
import uninstall  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF = "0123456789abcdef0123456789abcdef01234567"

PROJECT_DENY = ["Bash(curl *)", "Bash(rm -rf /*)", "WebFetch"]
PROJECT_CLAUDE_MD = (
    "# The Project\n\nHouse rules nobody asked rice about.\n\n"
    "## Build\n\n`make all`\n\n## Agent skills\n\n### Issue tracker\n\nSee docs/agents.\n"
)
PROJECT_AGENTS_MD = "# Agents\n\nCross-agent guidance this repo already had.\n\n## Testing\n\n`make test`\n"


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": reconcile.build_version(),
                              "scope": "user", "enabled": True}]))


PROBES = {"rtk_probe": _ok_rtk, "plugin_probe": _installed}


def _existing_settings() -> dict:
    """Settings a real repo might already have, with something in every
    container rice also writes into."""
    return {
        "permissions": {"deny": list(PROJECT_DENY), "allow": ["Bash(npm test *)"], "ask": ["Bash(rm *)"]},
        "env": {"MY_VAR": "keep"},
        "enabledPlugins": {"other@mp": True},
        "extraKnownMarketplaces": {"mp": {"source": {"source": "github", "repo": "x/y"}}},
        "hooks": {"SessionEnd": [{"matcher": "clear", "hooks": [{"type": "command", "command": "echo hi"}]}]},
        "outputStyle": "theirs",
        "somethingElse": {"nested": [1, 2, 3]},
    }


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


class OwnershipBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "cfg")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def existing_repo(self) -> dict[str, bytes]:
        """A repo with content of its own everywhere rice will write.
        Returns its byte-for-byte snapshot."""
        (self.root / ".claude").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "settings.json").write_text(
            json.dumps(_existing_settings(), indent=2) + "\n", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text(PROJECT_CLAUDE_MD, encoding="utf-8")
        (self.root / "AGENTS.md").write_text(PROJECT_AGENTS_MD, encoding="utf-8")
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
        (self.root / "docs" / "agents").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "agents" / "issue-tracker.md").write_text("theirs\n", encoding="utf-8")
        return _tree(self.root)

    def settings(self) -> dict:
        return json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def install(self, **kw):
        reconcile.write_owner_choices(self.root, ref=REF, archive=True, **kw)
        return reconcile.apply(self.root, **PROBES)


class TestExistingRepoSafety(OwnershipBase):
    """The hard invariant: rice can be installed into a repo that already has
    content, and nothing of that content disappears."""

    def test_dirty_existing_settings_survive_a_bootstrap(self):
        self.existing_repo()
        before = _existing_settings()
        self.install()
        after = self.settings()
        self.assertEqual(after["permissions"]["allow"], before["permissions"]["allow"])
        self.assertEqual(after["permissions"]["ask"], before["permissions"]["ask"])
        self.assertEqual(after["env"]["MY_VAR"], "keep")
        self.assertEqual(after["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")  # rice's, added beside it
        self.assertTrue(after["enabledPlugins"]["other@mp"])
        self.assertEqual(after["extraKnownMarketplaces"]["mp"], before["extraKnownMarketplaces"]["mp"])
        self.assertEqual(after["hooks"], before["hooks"])
        self.assertEqual(after["somethingElse"], before["somethingElse"])
        self.assertEqual(after["outputStyle"], "theirs")  # the OWNER's choice was already made
        # ... and rice's own state is there too
        self.assertTrue(after["enabledPlugins"]["rice@rice-bootstrap"])

    def test_existing_deny_rules_survive_install_update_and_uninstall(self):
        """The one that used to be impossible: `permissions.deny` was owned
        wholesale, so a repo with its own rules could not be bootstrapped at
        all without losing them."""
        self.existing_repo()
        self.install()
        deny = self.settings()["permissions"]["deny"]
        self.assertEqual(deny[:len(PROJECT_DENY)], PROJECT_DENY, "project rules kept, in order, in front")
        for rule in settings_template.RICE_DENY_ENTRIES:
            self.assertIn(rule, deny)
        self.assertEqual(len(deny), len(PROJECT_DENY) + len(settings_template.RICE_DENY_ENTRIES))

        # an update refreshes rice's own entries and touches no other
        reconcile.apply(self.root, is_rice_update=True, **PROBES)
        self.assertEqual(self.settings()["permissions"]["deny"], deny)

        # a rice rule the OWNER deleted comes back; a project rule never goes
        hacked = [d for d in deny if d != settings_template.RICE_DENY_ENTRIES[0]]
        s = self.settings(); s["permissions"]["deny"] = hacked
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        reconcile.apply(self.root, **PROBES)
        restored = self.settings()["permissions"]["deny"]
        self.assertIn(settings_template.RICE_DENY_ENTRIES[0], restored)
        self.assertEqual([d for d in restored if d in PROJECT_DENY], PROJECT_DENY)

        # uninstall subtracts exactly rice's entries
        uninstall.uninstall(self.root)
        self.assertEqual(self.settings()["permissions"]["deny"], PROJECT_DENY)

    def test_project_claude_md_and_agents_md_survive(self):
        self.existing_repo()
        self.install()
        claude_md = (self.root / "CLAUDE.md").read_text(encoding="utf-8")
        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(claude_md.startswith(PROJECT_CLAUDE_MD))
        self.assertIn(reconcile.IMPORT_LINE, claude_md)
        self.assertTrue(agents_md.startswith(PROJECT_AGENTS_MD))
        self.assertIn(reconcile.AGENTS_BEGIN, agents_md)
        # Matt's section, written into CLAUDE.md by someone else, is untouched
        self.assertIn("## Agent skills", claude_md)

    def test_apply_then_remove_is_a_round_trip_on_project_owned_state(self):
        """Every file byte-for-byte, except the one file rice legitimately
        rewrites: settings.json comes back with the same content, though not
        necessarily the same key order or indentation."""
        before = self.existing_repo()
        before_settings = json.loads(before[".claude/settings.json"])
        self.install()
        self.assertNotEqual(_tree(self.root), before)  # rice really did write
        uninstall.uninstall(self.root)
        after = _tree(self.root)
        self.assertEqual(json.loads(after.pop(".claude/settings.json")), before_settings)
        before.pop(".claude/settings.json")
        self.assertEqual(after, before, "project-owned bytes did not survive the round trip")

    def test_the_round_trip_holds_for_a_repo_that_had_nothing(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print(1)\n", encoding="utf-8")
        before = _tree(self.root)
        self.install(output_style=settings_template.RICE_STYLE)
        uninstall.uninstall(self.root)
        self.assertEqual(_tree(self.root), before)  # nothing of rice's left at all
        self.assertFalse((self.root / ".claude").exists())  # rice created it, rice took it away


class TestIndistinguishableValues(OwnershipBase):
    """The honest limit of subtractive ownership, pinned so it cannot be
    forgotten: when a repo's own value is byte-identical to rice's canonical
    one, nothing on disk can tell them apart, and uninstall removes it.

    The alternative would be a manifest of what rice created, written at
    install time -- new state that can go stale, be committed, be edited, or
    disagree with the repo. Rice does not keep one: the rule is "rice owns
    exactly what matches its canonical declaration", stated once, with this
    consequence accepted and documented."""

    def test_a_project_value_identical_to_rices_is_removed_with_rices(self):
        self.existing_repo()
        s = _existing_settings()
        s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "sonnet"           # same as rice's
        s["permissions"]["deny"].append(settings_template.RICE_DENY_ENTRIES[0])  # same as a rice rule
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        uninstall.uninstall(self.root)
        after = self.settings()
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", after.get("env", {}))
        self.assertNotIn(settings_template.RICE_DENY_ENTRIES[0], after["permissions"]["deny"])
        # everything that is genuinely theirs is still there
        self.assertEqual(after["env"]["MY_VAR"], "keep")
        self.assertEqual(after["permissions"]["deny"], PROJECT_DENY)

    def test_a_deny_rule_an_older_build_shipped_is_treated_as_the_projects(self):
        """Rice owns exactly the entries THIS build declares; an entry a past
        build added and this one no longer lists is indistinguishable from a
        project rule, so it is never removed -- the safe direction."""
        self.existing_repo()
        self.install()
        s = self.settings(); s["permissions"]["deny"].append("Bash(git nonsense*)")
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        uninstall.uninstall(self.root)
        self.assertIn("Bash(git nonsense*)", self.settings()["permissions"]["deny"])


class TestCollisionsBlock(OwnershipBase):
    """A collision rice cannot merge losslessly is reported and left alone --
    never overwritten, never guessed at."""

    def _states(self, items):
        return {i.name: i for i in items}

    def test_a_leaf_the_project_already_set_differently_is_a_conflict(self):
        self.existing_repo()
        s = _existing_settings(); s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] = "opus"
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        item = self._states(reconcile.check(self.root, **PROBES))["settings:env.CLAUDE_CODE_SUBAGENT_MODEL"]
        self.assertEqual(item.state, "FAIL")
        self.assertIn("not overwritten", item.detail)
        self.assertEqual(self.settings()["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "opus")

    def test_a_deny_list_that_is_not_a_list_is_a_conflict_not_a_rewrite(self):
        self.existing_repo()
        s = _existing_settings(); s["permissions"]["deny"] = "Bash(git push --force*)"
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        applied = self._states(self.install())[f"settings:{settings_template.DENY_PATH}"]
        self.assertEqual(applied.state, "conflict")
        self.assertEqual(self.settings()["permissions"]["deny"], "Bash(git push --force*)")
        item = self._states(reconcile.check(self.root, **PROBES))[f"settings:{settings_template.DENY_PATH}"]
        self.assertEqual(item.state, "FAIL")
        self.assertIn("cannot add", item.detail)

    def test_hand_edited_rice_files_are_conflicts(self):
        self.existing_repo()
        self.install()
        (self.root / ".claude" / "rice.md").write_text("mine now\n", encoding="utf-8")
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        (self.root / "AGENTS.md").write_text(
            agents.replace("read `.claude/rice.md`", "ignore rice"), encoding="utf-8")
        states = self._states(reconcile.apply(self.root, **PROBES))
        self.assertEqual(states["rice.md"].state, "conflict")
        self.assertEqual(states["AGENTS.md:rice-pointer"].state, "conflict")
        self.assertEqual((self.root / ".claude" / "rice.md").read_text(encoding="utf-8"), "mine now\n")
        self.assertIn("ignore rice", (self.root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_a_collision_blocks_the_gate_rather_than_being_repaired_away_silently(self):
        """Repair converges rice-owned drift on purpose -- but only where the
        verifier failed it, and the OWNER sees it either way."""
        self.existing_repo()
        self.install()
        (self.root / ".claude" / "rice.md").write_text("mine now\n", encoding="utf-8")
        report = verify.run(self.root, **PROBES)
        self.assertFalse(report.gate_a)
        self.assertTrue(any(r.startswith("rice.md") for r in report.reasons), report.reasons)


class TestSubtractionTouchesOnlyRice(OwnershipBase):
    """Regressions from review: every one of these was a way for rice to take
    away something it never put there, or to claim it had handled something
    it had not."""

    def test_a_container_the_project_left_empty_is_not_rices_to_drop(self):
        (self.root / ".claude").mkdir(parents=True)
        (self.root / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {}, "permissions": {"allow": ["Bash(npm *)"]}}), encoding="utf-8")
        before = _tree(self.root)
        items, verdict = uninstall.check(self.root)
        self.assertEqual(verdict, "RICE-UNINSTALLED",
                         "a repo rice never touched reported rice state")
        uninstall.uninstall(self.root)
        self.assertEqual(_tree(self.root), before, "uninstall rewrote a repo it does not own")

    def test_a_container_rice_emptied_is_dropped(self):
        self.existing_repo()
        s = _existing_settings()
        del s["permissions"]["allow"], s["permissions"]["ask"]
        s["permissions"]["deny"] = []
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        self.assertIn("deny", self.settings()["permissions"])
        uninstall.uninstall(self.root)
        self.assertNotIn("permissions", self.settings())

    def test_a_settings_file_rice_cannot_read_is_left_alone(self):
        (self.root / ".claude").mkdir(parents=True)
        (self.root / ".claude" / "settings.json").write_text("not json at all\n", encoding="utf-8")
        before = _tree(self.root)
        actions = uninstall.uninstall(self.root)
        self.assertEqual(_tree(self.root), before)
        self.assertEqual([a for a in actions if "settings" in a], [])
        items, verdict = uninstall.check(self.root)
        self.assertTrue(verdict.startswith("RICE-UNINSTALL FAIL"))  # and it is not called clean

    def test_repair_never_counts_an_unmergeable_collision_as_handled(self):
        """`owned` means apply is the repair, not that it succeeded."""
        self.existing_repo()
        self.install()
        s = self.settings(); s["permissions"]["deny"] = "a string, not a list"
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        report = verify.run(self.root, **PROBES)
        self.assertFalse(report.gate_a)
        done = repair.repair(self.root, report, **PROBES)
        name = f"settings:{settings_template.DENY_PATH}"
        self.assertTrue(any(u.startswith(name) for u in done.unresolved), done.unresolved)
        self.assertFalse(any(name in a for a in done.applied), done.applied)
        self.assertEqual(self.settings()["permissions"]["deny"], "a string, not a list")

    def test_every_owned_file_is_both_removed_and_checked(self):
        """One declaration: adding a rice-created file cannot leave a hole in
        the removal check."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in reconcile.OWNED_FILES:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x", encoding="utf-8")
            labels = reconcile.remnants(root)
            for rel, label in reconcile.OWNED_FILES.items():
                self.assertTrue(labels[label], f"{rel} is removed but never checked")
            reconcile.remove(root)
            self.assertFalse(any(reconcile.remnants(root)[l] for l in reconcile.OWNED_FILES.values()))


class TestOwnershipIsDeclaredOnce(unittest.TestCase):
    def test_repair_keeps_no_second_list_of_what_rice_owns(self):
        src = (REPO / "scripts" / "repair.py").read_text(encoding="utf-8")
        self.assertNotIn("RECONCILE_OWNED", src)
        self.assertNotIn("RICE_DEFINED_KEYS", src)
        self.assertNotIn("rice.md", src)
        self.assertNotIn("AGENTS.md", src)
        self.assertIn("i.owned", src)

    def test_uninstall_keeps_no_second_list_of_what_rice_owns(self):
        src = (REPO / "scripts" / "uninstall.py").read_text(encoding="utf-8")
        for forbidden in ("RICE_DENY", "_SONNET", "RICE_STYLE", "extraKnownMarketplaces",
                          "enabledPlugins", "CLAUDE_CODE_SUBAGENT_MODEL", "RICE_SESSION_ARCHIVE",
                          "IMPORT_LINE", "rice.md"):
            self.assertNotIn(forbidden, src, f"uninstall re-derives ownership: {forbidden}")
        self.assertIn("reconcile.remove(", src)
        self.assertIn("reconcile.remnants(", src)

    def test_every_owned_item_is_one_reconcile_produces(self):
        """`owned` is not decoration: it must mark exactly the items apply()
        can converge, which is what repair relies on."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(root / "cfg")}):
                reconcile.write_owner_choices(root, ref=REF, archive=True)
                applied = {i.name for i in reconcile.apply(root, **PROBES)
                           if i.state in ("created", "updated", "noop", "conflict")}
                owned = {i.name for i in reconcile.check(root, **PROBES) if i.owned}
        self.assertTrue(owned)
        self.assertEqual(owned - applied, set(), "an item claims to be owned but apply never touches it")

    def test_the_pending_marker_is_rice_owned_state_like_the_rest(self):
        """RICE-BOOTSTRAP-045 folded the old bootstrap_state module in: the
        marker is something rice puts in a repo, so it is declared where the
        rest of that is."""
        self.assertFalse((REPO / "scripts" / "bootstrap_state.py").exists())
        allowlist = (REPO / "scripts" / "export_public.py").read_text(encoding="utf-8")
        self.assertNotIn("bootstrap_state", allowlist)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertFalse(reconcile.is_waiting_for_matt(root))
            reconcile.enter_waiting_for_matt(root)
            self.assertTrue(reconcile.is_waiting_for_matt(root))
            self.assertTrue(reconcile.remnants(root)["waiting-marker:absent"])
            reconcile.remove(root)
            self.assertFalse(reconcile.is_waiting_for_matt(root))


if __name__ == "__main__":
    unittest.main()
