"""RICE-ONE-COMMAND-UPDATE-001: `/rice:update` -- the plugin and the repo to the
latest release from one command, in the running session.

The seams are the two things the platform and the engine own: the `claude`
CLI (a scripted fake here, and a fake executable on PATH for the
whole-program run) and the installed build's own bootstrap (what runs by
installed path). What these pin is the order, what is trusted (only what
`claude plugin list` reads back), and that nothing is ever reported done
that was not.

Run: python3 -m unittest tests.test_update -v
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import evidence  # noqa: E402
import export_public  # noqa: E402
import update  # noqa: E402

KEY = "rice@rice-bootstrap"
OWNER_BLOCK = "FINAL-VERIFIED 0.1.9\n\nNothing left to do."


def entry(version, install_path, scope="user"):
    return {"id": KEY, "version": version, "scope": scope, "enabled": True, "installPath": str(install_path)}


class FakeClaude:
    """`claude plugin ...` as the platform answers it: a list, a marketplace
    refresh, an update that moves the installed version."""

    def __init__(self, tmp: Path, installed="0.1.8", latest="0.1.9", refresh_ok=True, update_ok=True,
                 claimed=None, scopes=("user",), runs=True):
        self.tmp, self.installed, self.latest = tmp, installed, latest
        self.refresh_ok, self.update_ok, self.claimed, self.scopes = refresh_ok, update_ok, claimed, scopes
        self.calls = []
        for v in {installed, latest}:
            if runs:
                (tmp / v / "scripts").mkdir(parents=True, exist_ok=True)
                (tmp / v / "scripts" / "rice.py").write_text("# stub\n")

    def __call__(self, cmd):
        self.calls.append(cmd[1:])
        if cmd[1:3] == ["plugin", "list"]:
            body = [entry(self.installed, self.tmp / self.installed, s) for s in self.scopes] if self.scopes else []
            return update.Run(0, json.dumps(body))
        if cmd[1:4] == ["plugin", "marketplace", "update"]:
            if not self.refresh_ok:
                return update.Run(1, json.dumps({"outcome": "failed", "message": "could not clone"}))
            return update.Run(0, json.dumps({"outcome": "ok", "message": "refreshed"}))
        if cmd[1:3] == ["plugin", "update"]:
            if not self.update_ok:
                return update.Run(1, json.dumps({"outcome": "failed", "message": "network down",
                                                 "failureCode": "network"}))
            old = self.installed
            self.installed = self.latest
            return update.Run(0, json.dumps({"outcome": "ok", "oldVersion": old,
                                             "newVersion": self.claimed or self.installed,
                                             "updateOutcome": "updated" if old != self.latest else "up_to_date"}))
        raise AssertionError(f"unexpected command {cmd}")

    def verbs(self):
        return [" ".join(c[:3] if c[:2] == ["plugin", "marketplace"] else c[:2])
                for c in self.calls if c[:2] != ["plugin", "list"]]


class UpdateBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        (self.repo / ".claude").mkdir(parents=True)
        (self.repo / ".claude" / "rice.md").write_text("rules\n")          # rice is set up here
        self.booted = []
        self._loaded = mock.patch.object(evidence, "build_version", return_value="0.1.8")  # the loaded session
        self._loaded.start()

    def tearDown(self):
        self._loaded.stop()
        self._tmp.cleanup()

    open_choices = ""                                      # what `rice.py questions` would print

    def engine(self, verb, root, repo, rc=0, out=None):
        if verb == "questions":
            return self.open_choices, 0
        self.booted.append((Path(root), Path(repo)))
        return (out if out is not None else f"rice's rules for the agent\n{update.VERBATIM}\n{OWNER_BLOCK}\n"), rc

    def go(self, claude, rc=0, out=None):
        return update.update(self.repo, run=claude, engine=lambda v, r, p: self.engine(v, r, p, rc, out))


class TestTheOneFlow(UpdateBase):
    """RICE-ONE-COMMAND-UPDATE-001: The order, and which build does the repo step."""

    def test_check_then_refresh_then_update_then_the_new_build_updates_the_repo(self):
        claude = FakeClaude(self.tmp)
        out = self.go(claude)
        self.assertTrue(out.ok)
        self.assertEqual(claude.verbs(), ["plugin marketplace update", "plugin update"])
        # the repo is updated by the INSTALLED build, by its installed path -- not the loaded one
        self.assertEqual(self.booted, [(self.tmp / "0.1.9", self.repo)])
        self.assertEqual(out.owner[0], "rice plugin: updated 0.1.8 -> 0.1.9.")

    def test_the_plugin_line_leads_the_engines_block_and_its_agent_text_stays_outside(self):
        shown = update.render(self.go(FakeClaude(self.tmp)))
        agent, owner = shown.split(update.VERBATIM)
        self.assertIn("rules for the agent", agent)
        self.assertTrue(owner.strip().startswith("rice plugin: updated 0.1.8 -> 0.1.9."))
        self.assertIn("FINAL-VERIFIED 0.1.9", owner)

    def test_the_update_is_user_scope_and_json_never_a_blind_yes(self):
        claude = FakeClaude(self.tmp)
        self.go(claude)
        step = next(c for c in claude.calls if c[:2] == ["plugin", "update"])
        self.assertEqual(step, ["plugin", "update", KEY, "-s", "user", "--json"])
        self.assertFalse(any(a in ("-y", "--yes") for c in claude.calls for a in c))

    def test_the_session_reload_is_named_when_this_session_is_older_than_what_is_installed(self):
        old = self.go(FakeClaude(self.tmp)).owner
        self.assertTrue(any("/reload-plugins" in l and "0.1.8" in l and "0.1.9" in l for l in old), old)
        with mock.patch.object(evidence, "build_version", return_value="0.1.9"):
            same = self.go(FakeClaude(self.tmp, installed="0.1.9", latest="0.1.9")).owner
        self.assertFalse(any("/reload-plugins" in l for l in same), same)


class TestNothingIsReportedDoneThatIsNot(UpdateBase):
    """RICE-ONE-COMMAND-UPDATE-001: Only what `claude plugin list` reads back is believed."""

    def test_no_newer_release_is_said_only_when_the_marketplace_was_actually_checked(self):
        with mock.patch.object(evidence, "build_version", return_value="0.1.9"):   # a session on the latest
            ok = self.go(FakeClaude(self.tmp, installed="0.1.9", latest="0.1.9")).owner[0]
            self.assertEqual(ok, "rice plugin: no newer release is offered; rice 0.1.9 stays installed.")
            unchecked = self.go(FakeClaude(self.tmp, installed="0.1.9", latest="0.1.9", refresh_ok=False)).owner[0]
        self.assertIn("could not check for a newer release", unchecked)
        self.assertIn("could not clone", unchecked)
        self.assertNotIn("no newer release is offered", unchecked)

    def test_a_rerun_after_this_commands_own_update_does_not_read_as_nothing_updated(self):
        """Real session: the first run updated the plugin, then the repo step
        asked for the git-deny probe and the agent re-ran the command. The
        second run found the plugin already installed; its report must still
        say the session started on the older build."""
        second = self.go(FakeClaude(self.tmp, installed="0.1.9", latest="0.1.9")).owner
        self.assertEqual(second[0], "rice plugin: 0.1.9 is installed (this session started on 0.1.8).")
        self.assertNotIn("no newer release", " ".join(second))
        self.assertIn("/reload-plugins", second[1])

    def test_a_failed_plugin_update_stops_before_the_repo_is_touched(self):
        out = self.go(FakeClaude(self.tmp, update_ok=False))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("network down", out.owner[0])
        self.assertIn("Nothing in this repo was changed.", out.owner)

    def test_the_installed_version_is_read_back_not_taken_from_the_updates_own_message(self):
        out = self.go(FakeClaude(self.tmp, claimed="0.2.0"))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("0.2.0", out.owner[0])
        self.assertIn("0.1.9", out.owner[0])

    def test_a_release_that_installs_under_a_hash_not_a_version_is_refused(self):
        """Probed for real: a newer release whose plugin.json cannot be read
        installs fine under its commit hash. It must not be run or called
        an update."""
        out = self.go(FakeClaude(self.tmp, latest="06f57ac0316d"))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("06f57ac0316d", out.owner[0])
        self.assertIn("broken", out.owner[0])
        self.assertIn("@release/0.1.8", out.owner[0])
        self.assertNotIn("updated", " ".join(out.owner))
        # already installed broken by an earlier run: there is no good build to point back to
        again = self.go(FakeClaude(self.tmp, installed="06f57ac0316d", latest="06f57ac0316d"))
        self.assertFalse(again.ok)
        self.assertNotIn("pin", again.owner[0])

    def test_an_installed_build_without_its_scripts_is_a_stop_not_a_guess(self):
        out = self.go(FakeClaude(self.tmp, runs=False))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("rice.py is missing", out.owner[0])

    def test_a_failed_repo_update_is_not_ok_and_carries_the_engines_words(self):
        out = self.go(FakeClaude(self.tmp), rc=1)
        self.assertFalse(out.ok)
        self.assertIn("FINAL-VERIFIED 0.1.9", update.render(out))   # the engine's block, relayed as it is

    def test_no_supported_install_is_a_stop_and_the_manual_way_only_where_it_is_the_remedy(self):
        nothing = self.go(FakeClaude(self.tmp, scopes=()))
        self.assertIn("By hand:", " ".join(nothing.owner))
        for scopes in (("project",), ("user", "project")):       # the verifier's detail has the right commands
            claude = FakeClaude(self.tmp, scopes=scopes)
            out = self.go(claude)
            self.assertFalse(out.ok)
            self.assertEqual(claude.verbs(), [])                  # nothing was updated
            self.assertIn("claude plugin uninstall", out.owner[0])
            self.assertNotIn("By hand:", " ".join(out.owner))
        self.assertEqual(self.booted, [])

    def test_a_missing_claude_cli_is_a_stop_not_a_traceback(self):
        out = update.update(self.repo, run=lambda cmd: update.default_run(["claude-does-not-exist", *cmd[1:]]),
                            engine=self.engine)
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])


class TestTheRepoStepNeverHidesItsFailure(UpdateBase):
    """RICE-ONE-COMMAND-UPDATE-001: A failed repo step, a timeout, a hostile CLI text: each is said."""

    def test_a_crash_without_the_engines_report_still_says_the_repo_update_failed(self):
        out = self.go(FakeClaude(self.tmp), rc=1, out="Traceback (most recent call last):\n  boom\n")
        self.assertFalse(out.ok)
        shown = update.render(out).split(update.VERBATIM)[1]
        self.assertIn("The repo update FAILED (exit 1)", shown)
        self.assertIn("Run this command again", shown)
        self.assertNotIn("Traceback", shown)

    def test_a_timed_out_plugin_update_says_the_plugin_may_be_half_updated(self):
        class Slow(FakeClaude):
            def __call__(self, cmd):
                return update.Run(124, "", "timed out after 150s") if cmd[1:3] == ["plugin", "update"] else super().__call__(cmd)
        out = self.go(Slow(self.tmp))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("half-updated", " ".join(out.owner))

    def test_the_engine_has_a_timeout_and_reports_it_as_a_failure(self):
        with mock.patch.object(update.subprocess, "run", side_effect=update.subprocess.TimeoutExpired("x", 900)):
            out, rc = update._engine("bootstrap", self.tmp, self.repo)
        self.assertEqual(rc, 124)
        self.assertIn("timed out", out)

    def test_a_cli_message_cannot_close_the_owners_code_block(self):
        class Hostile(FakeClaude):
            def __call__(self, cmd):
                if cmd[1:3] == ["plugin", "update"]:
                    return update.Run(1, json.dumps({"outcome": "failed", "message": "bad ```\nreport```"}))
                return super().__call__(cmd)
        out = self.go(Hostile(self.tmp))
        self.assertNotIn("`", out.owner[0])

    def test_a_failed_questions_check_is_not_read_as_unanswered_choices(self):
        out = update.update(self.repo, run=FakeClaude(self.tmp),
                            engine=lambda v, r, p: ("Traceback boom", 1) if v == "questions" else self.engine(v, r, p))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("could not be checked (exit 1", out.owner[-1])
        self.assertNotIn("unanswered", out.owner[-1])

    def test_no_subprocess_waits_on_a_prompt_or_outlives_the_skills_own_timeout(self):
        seen = {}
        def fake_run(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(update.subprocess, "run", fake_run):
            update.default_run(["x"], timeout=5)
            self.assertEqual(seen["stdin"], subprocess.DEVNULL)
            seen.clear()
            update._engine("bootstrap", self.tmp, self.repo)
            self.assertEqual(seen["stdin"], subprocess.DEVNULL)
        self.assertLess(update.TOTAL_BUDGET_SECONDS, 600)          # the skill's Bash timeout is 600000 ms
        self.assertIn("600000", (REPO / "skills" / "update" / "SKILL.md").read_text(encoding="utf-8"))

    def test_an_unrunnable_command_is_a_report_not_a_traceback(self):
        with mock.patch.object(update.subprocess, "run", side_effect=PermissionError(13, "Permission denied")):
            self.assertEqual(update.default_run(["x"]).returncode, 127)
            self.assertEqual(update._engine("bootstrap", self.tmp, self.repo)[1], 127)

    def test_a_repo_with_unanswered_choices_is_not_called_updated(self):
        self.open_choices = "archive\noutput-style\n"
        out = self.go(FakeClaude(self.tmp))
        self.assertFalse(out.ok)
        self.assertEqual(self.booted, [])                          # the engine's bootstrap never ran
        self.assertIn("archive, output-style", out.owner[-1])
        self.assertIn("The repo was not updated.", out.owner[-1])


class TestRecovery(UpdateBase):
    """RICE-ONE-COMMAND-UPDATE-001: The same command is the retry."""

    def test_a_rerun_after_a_failed_repo_update_finishes_it_without_updating_twice(self):
        claude = FakeClaude(self.tmp)
        self.assertFalse(self.go(claude, rc=1).ok)                   # plugin updated, repo not
        again = self.go(claude)
        self.assertTrue(again.ok)
        self.assertEqual(again.owner[0], "rice plugin: 0.1.9 is installed (this session started on 0.1.8).")
        self.assertEqual([r for r, _ in self.booted], [self.tmp / "0.1.9"] * 2)

    def test_a_marketplace_that_cannot_be_refreshed_still_finishes_a_pending_update(self):
        """An earlier run (or the OWNER) installed the new build but the repo
        was never brought up: no network now is no reason to leave it."""
        out = self.go(FakeClaude(self.tmp, installed="0.1.9", latest="0.1.9", refresh_ok=False))
        self.assertTrue(out.ok)
        self.assertEqual([r for r, _ in self.booted], [self.tmp / "0.1.9"])


class TestARepoRiceNeverSetUp(UpdateBase):
    """RICE-ONE-COMMAND-UPDATE-001: The plugin updates; the repo waits for its first bootstrap."""

    def test_the_plugin_is_updated_and_the_repo_is_left_for_the_first_bootstrap(self):
        (self.repo / ".claude" / "rice.md").unlink()
        out = self.go(FakeClaude(self.tmp))
        self.assertTrue(out.ok)
        self.assertEqual(self.booted, [])
        self.assertIn("/rice:bootstrap", out.owner[-1])


class TestTheWholeProgram(UpdateBase):
    """update.py as the skill runs it: a `claude` executable on PATH, the
    installed build's rice.py found by the path `claude plugin list` gives."""

    def fake_claude(self) -> Path:
        state = self.tmp / "state"
        state.write_text("0.1.8")
        for v in ("0.1.8", "0.1.9"):
            (self.tmp / v / "scripts").mkdir(parents=True)
        (self.tmp / "0.1.9" / "scripts" / "rice.py").write_text(
            "import sys\nif sys.argv[1] == 'questions': raise SystemExit(0)\nprint('agent text')\nprint('" + update.VERBATIM + "')\nprint('FINAL-VERIFIED 0.1.9 for', sys.argv[2])\n")
        (self.tmp / "0.1.8" / "scripts" / "rice.py").write_text("raise SystemExit('the old build must not run')\n")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        exe = bin_dir / "claude"
        exe.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
state, a = Path({str(state)!r}), sys.argv[1:]
v = state.read_text()
if a[:2] == ["plugin", "list"]:
    print(json.dumps([{{"id": "{KEY}", "version": v, "scope": "user", "enabled": True,
                       "installPath": {str(self.tmp)!r} + "/" + v}}]))
elif a[:3] == ["plugin", "marketplace", "update"]:
    print(json.dumps({{"command": "marketplace-update", "outcome": "ok", "message": "ok"}}))
elif a[:2] == ["plugin", "update"]:
    state.write_text("0.1.9")
    print(json.dumps({{"command": "update", "outcome": "ok", "oldVersion": v, "newVersion": "0.1.9",
                      "updateOutcome": "updated"}}))
else:
    sys.exit(2)
""")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        return bin_dir

    def test_one_run_updates_the_plugin_and_runs_the_installed_builds_bootstrap_on_the_repo(self):
        bin_dir = self.fake_claude()
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "update.py"), str(self.repo)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        agent, owner = r.stdout.split(update.VERBATIM)
        self.assertIn("agent text", agent)
        self.assertIn("rice plugin: updated 0.1.8 -> 0.1.9.", owner)
        self.assertIn(f"FINAL-VERIFIED 0.1.9 for {self.repo}", owner)

    def test_a_failure_exits_nonzero_and_still_ends_in_one_owner_block(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
        (bin_dir / "claude").chmod(0o755)
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "update.py"), str(self.repo)],
                           capture_output=True, text=True, env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.count(update.VERBATIM), 1)
        self.assertIn("Nothing in this repo was changed.", r.stdout)


class TestTheSkillIsAThinKeyOnlyTheOwnerTurns(unittest.TestCase):
    SKILL = REPO / "skills" / "update" / "SKILL.md"

    def test_it_is_owner_invoked_declared_and_shipped(self):
        text = self.SKILL.read_text(encoding="utf-8")
        front = text.split("---")[1]
        self.assertIn("name: update", front)
        self.assertIn("disable-model-invocation: true", front)
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertIn("./skills/update", manifest["skills"])
        for rel in ("skills/update/SKILL.md", "scripts/update.py"):
            self.assertIn(rel, export_public.ALLOWLIST)

    def test_it_runs_one_command_and_does_not_carry_the_steps(self):
        text = self.SKILL.read_text(encoding="utf-8")
        self.assertIn('scripts/update.py', text)
        self.assertLessEqual(len(text.splitlines()), 40)
        for step in ("marketplace update", "plugin update", "Gate A", "--final", "reconcile", "verify.py"):
            self.assertNotIn(step, text, step)

    def test_it_never_claims_a_reload_the_model_cannot_do(self):
        """Probed: `/reload-plugins` is a built-in command; the Skill tool
        refuses it ("Ask the user to run /reload-plugins themselves")."""
        text = self.SKILL.read_text(encoding="utf-8")
        self.assertIn("never type `/reload-plugins`", text)


if __name__ == "__main__":
    unittest.main()
