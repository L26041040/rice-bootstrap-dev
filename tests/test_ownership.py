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

import evidence  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import settings_template  # noqa: E402
import uninstall  # noqa: E402
import verify  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF = settings_template.release_ref()

PROJECT_DENY = ["Bash(curl *)", "Bash(rm -rf /*)", "WebFetch"]
PROJECT_CLAUDE_MD = (
    "# The Project\n\nHouse rules nobody asked rice about.\n\n"
    "## Build\n\n`make all`\n\n## Agent skills\n\n### Issue tracker\n\nSee docs/agents.\n"
)
PROJECT_AGENTS_MD = "# Agents\n\nCross-agent guidance this repo already had.\n\n## Testing\n\n`make test`\n"



def _rubric(repo_root, **probes):
    """The full rubric the verifier evaluates: rice-owned state plus
    environment evidence (RICE-BOOTSTRAP-046 split them)."""
    return reconcile.check_owned(repo_root) + evidence.collect(repo_root, **probes)


class _R:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def _ok_rtk(cmd):
    return _R(0, "rtk 0.46.0\n")


def _installed(cmd):
    return _R(0, json.dumps([{"id": "rice@rice-bootstrap", "version": evidence.build_version(),
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
        reconcile.write_owner_choices(self.root, archive=True, **kw)
        return reconcile.apply(self.root)


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
        self.assertTrue(after["enabledPlugins"]["other@mp"])
        self.assertEqual(after["extraKnownMarketplaces"]["mp"], before["extraKnownMarketplaces"]["mp"])
        self.assertEqual(after["hooks"], before["hooks"])
        self.assertEqual(after["somethingElse"], before["somethingElse"])
        self.assertEqual(after["outputStyle"], "theirs")  # the OWNER's choice was already made
        # ... and rice's own state is there too, at user scope only
        # (RICE-FIX-001): nothing is added to the project's own containers
        self.assertTrue(reconcile.rice_installed_here(self.root))
        self.assertNotIn("rice-bootstrap", after["extraKnownMarketplaces"])
        self.assertNotIn("rice@rice-bootstrap", after["enabledPlugins"])

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
        reconcile.apply(self.root, converge_drift=True)
        self.assertEqual(self.settings()["permissions"]["deny"], deny)

        # a rice rule the OWNER deleted comes back; a project rule never goes
        hacked = [d for d in deny if d != settings_template.RICE_DENY_ENTRIES[0]]
        s = self.settings(); s["permissions"]["deny"] = hacked
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        reconcile.apply(self.root)
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


class TestDenyIsAMultiset(OwnershipBase):
    """RICE-BOOTSTRAP-046: rice owns exactly ONE occurrence of each rule it
    declares. Fresh installs add it, everything else leaves the multiset
    alone, and uninstall takes back exactly one."""

    RULE = settings_template.RICE_DENY_ENTRIES[0]

    def deny(self):
        return self.settings()["permissions"]["deny"]

    def test_a_fresh_repo_gets_exactly_one_occurrence_of_each_rule(self):
        self.install()
        deny = self.deny()
        self.assertEqual(sorted(deny), sorted(settings_template.RICE_DENY_ENTRIES))
        for rule in settings_template.RICE_DENY_ENTRIES:
            self.assertEqual(deny.count(rule), 1, rule)
        uninstall.uninstall(self.root)
        self.assertFalse((self.root / ".claude").exists())  # rice created it all, rice took it all

    def test_a_repeated_bootstrap_never_adds_a_third_copy(self):
        self.existing_repo()
        s = _existing_settings(); s["permissions"]["deny"].append(self.RULE)
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        self.assertEqual(self.deny().count(self.RULE), 2)
        for _ in range(3):
            self.install()
            self.assertEqual(self.deny().count(self.RULE), 2)

    def test_an_update_never_grows_the_multiplicity(self):
        self.existing_repo()
        self.install()
        before = list(self.deny())
        for _ in range(3):
            reconcile.apply(self.root, converge_drift=True)
        self.assertEqual(self.deny(), before)

    def test_a_rule_the_owner_deleted_comes_back_exactly_once(self):
        self.existing_repo()
        self.install()
        s = self.settings()
        s["permissions"]["deny"] = [d for d in s["permissions"]["deny"] if d != self.RULE]
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        self.assertEqual(self.deny().count(self.RULE), 1)

    def test_uninstalling_twice_never_eats_the_projects_copy(self):
        self.existing_repo()
        s = _existing_settings(); s["permissions"]["deny"].append(self.RULE)
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        uninstall.uninstall(self.root)
        after_first = list(self.deny())
        self.assertEqual(after_first.count(self.RULE), 1)
        uninstall.uninstall(self.root)
        self.assertEqual(self.deny(), after_first)

    def test_losing_only_rice_md_never_loses_the_deny_takeback(self):
        """Code review finding: `rice_installed_here` must not collapse to a
        single fragile signal. `.claude/rice.md` deleted on its own (an
        unrelated edit, not an uninstall) must not read as "rice was never
        here" -- CLAUDE.md's import line and AGENTS.md's pointer block are
        still rice's own evidence, and uninstall must still take its deny
        occurrence back."""
        self.existing_repo()
        self.install()
        (self.root / ".claude" / "rice.md").unlink()
        self.assertTrue(reconcile.rice_installed_here(self.root))
        uninstall.uninstall(self.root)
        self.assertEqual([d for d in self.settings()["permissions"]["deny"] if d in PROJECT_DENY], PROJECT_DENY)
        for rule in settings_template.RICE_DENY_ENTRIES:
            self.assertNotIn(rule, self.settings()["permissions"]["deny"])

    def test_freshness_is_read_from_rices_own_whole_file_not_a_manifest(self):
        """The marker is `.claude/rice.md` itself (RICE-FIX-001, once the
        settings leaves it used to double up with were retired): written by
        apply, removed by remove, living in the file being edited."""
        self.existing_repo()
        self.assertFalse(reconcile.rice_installed_here(self.root))
        self.install()
        self.assertTrue(reconcile.rice_installed_here(self.root))
        uninstall.uninstall(self.root)
        self.assertFalse(reconcile.rice_installed_here(self.root))
        # ... and no new state file was created anywhere to hold that answer
        self.assertEqual(sorted(p.name for p in (self.root / ".claude").iterdir())
                         if (self.root / ".claude").exists() else [], ["settings.json"])


class TestMultisetSafetyUnderDrift(OwnershipBase):
    """Review found ways the multiset could grow or eat a project rule when
    rice's marker and the deny list disagreed. Each is pinned here.
    RICE-FIX-001 retired the settings-leaf half of the old marker pair --
    `.claude/rice.md` is now the only signal -- so what is pinned is that
    unrelated settings drift never moves it."""

    RULE = settings_template.RICE_DENY_ENTRIES[0]

    def deny(self):
        return self.settings()["permissions"]["deny"]

    def test_unrelated_plugin_containers_never_affect_freshness(self):
        self.existing_repo()
        self.install()
        before = list(self.deny())
        s = self.settings()  # project-owned containers, untouched by rice's own writes
        del s["enabledPlugins"], s["extraKnownMarketplaces"]
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        self.assertEqual(sorted(self.deny()), sorted(before))

    def test_a_013_era_leftover_never_lets_uninstall_subtract_twice(self):
        self.existing_repo()
        self.install()
        s = self.settings(); s["enabledPlugins"]["rice@rice-bootstrap"] = False  # a 0.1.3-era leftover
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        uninstall.uninstall(self.root)
        once = list(self.deny())
        uninstall.uninstall(self.root)
        self.assertEqual(self.deny(), once, "a second uninstall subtracted again")
        self.assertEqual([d for d in self.deny() if d in PROJECT_DENY], PROJECT_DENY)

    def test_a_clean_uninstall_is_reported_clean_and_stays_clean(self):
        self.existing_repo()
        self.install()
        uninstall.uninstall(self.root)
        items, verdict = uninstall.check(self.root)
        self.assertEqual(verdict, "RICE-UNINSTALLED",
                         "\n".join(f"{i.name} {i.state} {i.detail}" for i in items))
        after = _tree(self.root)
        uninstall.uninstall(self.root)
        self.assertEqual(_tree(self.root), after)

    def test_a_retired_setting_is_still_taken_back_on_uninstall(self):
        """A repo bootstrapped before RICE-BOOTSTRAP-046 still carries the
        subagent pin rice used to write; nothing re-creates it, and uninstall
        still removes rice's own value."""
        self.existing_repo()
        self.install()
        s = self.settings(); s.setdefault("env", {})["CLAUDE_CODE_SUBAGENT_MODEL"] = "sonnet"
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()  # an update never reasserts it
        self.assertEqual(self.settings()["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")
        uninstall.uninstall(self.root)
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", self.settings().get("env", {}))

    def test_a_project_value_at_the_retired_path_is_kept(self):
        self.existing_repo()
        self.install()
        s = self.settings(); s.setdefault("env", {})["CLAUDE_CODE_SUBAGENT_MODEL"] = "opus"
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        uninstall.uninstall(self.root)
        self.assertEqual(self.settings()["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "opus")


class TestIndistinguishableValues(OwnershipBase):
    """Where a value cannot be told apart, rice still must not guess.

    `permissions.deny` is settled: rice owns one occurrence of each rule it
    declares, so a project's identical rule survives (multiset ownership,
    RICE-BOOTSTRAP-046) -- no manifest needed. What remains inherently
    ambiguous is a rice-defined *leaf*, where a single path holds a single
    value: if the project set it to exactly rice's value, nothing on disk
    distinguishes them. Rice keeps no install manifest to tell them apart --
    that would be new state that can go stale, be committed, be edited, or
    disagree with the repo."""

    def test_a_deny_rule_the_project_already_had_survives_uninstall(self):
        """RICE-BOOTSTRAP-046 closed this one with multiset ownership: rice
        adds its OWN occurrence beside the project's and takes back exactly
        that one, so an identical rule is no longer a loss."""
        self.existing_repo()
        rule = settings_template.RICE_DENY_ENTRIES[0]
        s = _existing_settings(); s["permissions"]["deny"].append(rule)
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        self.assertEqual(self.settings()["permissions"]["deny"].count(rule), 2)
        uninstall.uninstall(self.root)
        after = self.settings()
        self.assertEqual(after["permissions"]["deny"].count(rule), 1)
        self.assertEqual(after["permissions"]["deny"], PROJECT_DENY + [rule])
        self.assertEqual(after["env"]["MY_VAR"], "keep")

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

    def test_a_marketplace_entry_at_rices_name_but_not_rices_repo_is_left_alone(self):
        """RICE-FIX-001: identity, not name, decides what is rice's. A
        project's own entry that merely happens to be named `rice-bootstrap`
        but does not point at rice's public repo is never touched -- rice
        keeps nothing at project scope to collide with in the first place."""
        self.existing_repo()
        s = _existing_settings()
        s["extraKnownMarketplaces"]["rice-bootstrap"] = {"source": {"repo": "someone/else"}}
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        self.install()
        item = self._states(reconcile.check_owned(self.root))["settings:legacy-project-scope"]
        self.assertEqual(item.state, "PASS")
        self.assertEqual(self.settings()["extraKnownMarketplaces"]["rice-bootstrap"]["source"]["repo"],
                         "someone/else")

    def test_a_deny_list_that_is_not_a_list_is_a_conflict_not_a_rewrite(self):
        self.existing_repo()
        s = _existing_settings(); s["permissions"]["deny"] = "Bash(git push --force*)"
        (self.root / ".claude" / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        applied = self._states(self.install())[f"settings:{settings_template.DENY_PATH}"]
        self.assertEqual(applied.state, "conflict")
        self.assertEqual(self.settings()["permissions"]["deny"], "Bash(git push --force*)")
        item = self._states(_rubric(self.root, **PROBES))[f"settings:{settings_template.DENY_PATH}"]
        self.assertEqual(item.state, "FAIL")
        self.assertIn("cannot add", item.detail)

    def test_hand_edited_rice_files_are_conflicts(self):
        self.existing_repo()
        self.install()
        (self.root / ".claude" / "rice.md").write_text("mine now\n", encoding="utf-8")
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        (self.root / "AGENTS.md").write_text(
            agents.replace("read `.claude/rice.md`", "ignore rice"), encoding="utf-8")
        states = self._states(reconcile.apply(self.root))
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


class TestTwoLifecyclesOnly(OwnershipBase):
    """RICE-FINAL-001's UX invariant: install is one `/rice:bootstrap`,
    removal is one `/rice:rice-uninstall`, both inside one session. Nothing
    in either asks the OWNER to restart, update, or reason about a ref."""

    def test_install_and_removal_each_complete_in_one_pass(self):
        self.existing_repo()
        self.install()                         # what /rice:bootstrap does
        self.assertTrue(reconcile.rice_installed_here(self.root))
        expected = uninstall.expected_after(self.root)
        uninstall.uninstall(self.root)         # what /rice:rice-uninstall does
        items, verdict = uninstall.check(self.root, expected=expected)
        self.assertEqual(verdict, "RICE-UNINSTALLED",
                         "\n".join(f"{i.name} {i.state} {i.detail}" for i in items))

    def test_neither_lifecycle_depends_on_a_second_session(self):
        surfaces = [REPO / "skills" / "bootstrap" / "SKILL.md",
                    REPO / "skills" / "rice-uninstall" / "SKILL.md"]
        for path in surfaces:
            text = path.read_text(encoding="utf-8").lower()
            for forbidden in ("rice:rice-update", "two-session", "restart", "update rice",
                              "release sha", "latest version"):
                self.assertNotIn(forbidden, text, f"{path.name}: {forbidden}")


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
                reconcile.write_owner_choices(root, archive=True)
                applied = {i.name for i in reconcile.apply(root)
                           if i.state in ("created", "updated", "noop", "conflict")}
                owned = {i.name for i in _rubric(root, **PROBES) if i.owned}
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
