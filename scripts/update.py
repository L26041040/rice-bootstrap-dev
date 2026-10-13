#!/usr/bin/env python3
"""rice update (RICE-ONE-COMMAND-UPDATE-001): the plugin and this repo, to the
latest release, from one command in the running session.

Facts this rests on, each probed in a real interactive Claude Code session
(auto permission mode, inside a Claude Code on the web container, against a
local git marketplace -- see docs/research/RICE-ONE-COMMAND-UPDATE-001-feasibility.md
for what was and was not verified):

  * `claude plugin marketplace update` + `claude plugin update`, run from
    the Bash tool, install the new build under its own version directory
    and leave the running session's hooks and skill text on the old one.
  * The model cannot reload plugins (`/reload-plugins` is the OWNER's own
    command, not a skill), so nothing here tries to.
  * The new build's scripts can be run by their installed path at once, and
    then `plugin:build` (the installed version equals the running build)
    holds, so the new build's own bootstrap -- the whole repo update, its
    save of the OWNER's work included -- runs in this same session.

So: check -> refresh the marketplace -> update the plugin -> confirm what is
installed -> run the INSTALLED build's `rice.py bootstrap`. Nothing here
decides whether a repo is up to date or writes into one: the bootstrap
engine does, and the verifier it ends with says whether it worked. This
module decides nothing about the repo: it reports what the plugin step did,
never "updated" on its own word (the installed version is read back from
`claude plugin list`), and refuses only to run a build that cannot be run.

Re-running is the recovery: a step already done reports "already at the
latest release" and the next step runs; nothing is half-applied, because
the plugin step is the platform's and the repo step saves before it writes.
"""
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence  # noqa: E402
import reconcile  # noqa: E402
import settings_template  # noqa: E402

PLUGIN_KEY = settings_template.PLUGIN_KEY
MARKETPLACE = settings_template.MARKETPLACE_NAME
SCOPE = evidence.CANONICAL_SCOPE
CLI_TIMEOUT_SECONDS = 150           # the CLI's own clone/refresh timeouts are 120s
TOTAL_BUDGET_SECONDS = 540          # the skill's Bash call is killed at 600s: answer before that, in our own words
VERBATIM = "--- present this to the OWNER verbatim ---"   # the engine's own marker


def _load_new_build() -> str:
    """How the OWNER gets the session itself onto the installed build."""
    return "a new session" if evidence.on_web() else "/reload-plugins (or a new session)"


def _manual() -> str:
    return (f"claude plugin marketplace update {MARKETPLACE}; claude plugin update {PLUGIN_KEY}; "
            f"then {_load_new_build()} and /rice:bootstrap")


@dataclass
class Run:                                   # what a subprocess answered; also the test seam's shape
    returncode: int
    stdout: str = ""
    stderr: str = ""


def default_run(cmd, timeout=CLI_TIMEOUT_SECONDS) -> Run:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Run(124, "", f"timed out after {int(timeout)}s")
    except OSError as exc:                                 # not found, not executable, ...
        return Run(127, "", f"{cmd[0]}: {exc.strerror or exc}")
    return Run(r.returncode, r.stdout, r.stderr)


def _brief(text: str, limit: int = 200) -> str:
    """One line of a CLI's explanation, its end kept: the cause comes last."""
    one = " ".join(str(text).replace("`", "'").split())      # a CLI's text must not close the OWNER's code block
    return one if len(one) <= limit else "..." + one[-(limit - 3):]


def _reason(r: Run) -> str:
    """Why a CLI step did not succeed, in one line: its own JSON message, else its stderr."""
    return _brief(_json_line(r.stdout).get("message") or r.stderr or f"exit {r.returncode}")


def _json_line(text: str) -> dict:
    """The last JSON object a `--json` command printed ({} when none)."""
    for line in reversed(text.strip().splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _installed(run) -> tuple[dict | None, str, bool]:
    """(the canonical rice install as `claude plugin list --json` reports it,
    why there is none, whether the by-hand update is the remedy). What a
    supported install is stays `evidence.classify_install`'s decision -- its
    own detail already carries the commands for a duplicate install -- and
    the entry itself is `evidence.installed_entries`'."""
    listing = run(["claude", "plugin", "list", "--json"])
    probe = lambda cmd: listing                                    # noqa: E731 - one list call, both reads
    _, state, detail, _ = evidence.classify_install(probe)
    if state != "PASS":
        return None, detail, state != "FAIL"
    entries, _ = evidence.installed_entries(probe)
    return next((e for e in entries if e.get("scope") == SCOPE), None), "no user-scope rice install", True


@dataclass
class UpdateResult:
    ok: bool
    owner: list = field(default_factory=list)   # lines for the OWNER's report
    bootstrap_out: str = ""                      # the installed build's own words, if it ran


def _stop(why: str, *more: str) -> UpdateResult:
    return UpdateResult(False, [why, *more, "Nothing in this repo was changed."])


def update(repo_root, *, run=default_run, engine=None) -> UpdateResult:
    repo_root = Path(repo_root)
    engine = engine or _engine
    before, why, by_hand = _installed(run)
    if before is None:
        return _stop(f"rice cannot be updated from here: {why}", *([f"By hand: {_manual()}."] if by_hand else []))
    old = str(before.get("version"))

    refresh = run(["claude", "plugin", "marketplace", "update", MARKETPLACE, "--json"])
    refreshed = refresh.returncode == 0 and _json_line(refresh.stdout).get("outcome") == "ok"
    refresh_why = "" if refreshed else _reason(refresh)

    step = run(["claude", "plugin", "update", PLUGIN_KEY, "-s", SCOPE, "--json"])
    result = _json_line(step.stdout)
    if step.returncode != 0 or result.get("outcome") != "ok":
        return _stop(f"The rice plugin could not be updated: {_reason(step)}",
                     *([f"(the marketplace could not be refreshed either: {refresh_why})"] if refresh_why else []),
                     *(["The plugin may be half-updated; running this command again checks and finishes it."]
                       if step.returncode == 124 else []))

    # read back what is installed -- never trust the update's own message
    after, why, _ = _installed(run)
    if after is None:
        return _stop(f"The plugin update ran, but the install can no longer be read: {why}")
    new, root = str(after.get("version")), Path(str(after.get("installPath")))
    claimed = result.get("newVersion")
    if claimed and claimed != new:
        return _stop(f"The plugin update reported {claimed}, but {new} is what is installed.")
    if settings_template.semver(new) is None:
        # real: a release whose manifest cannot be read installs under its commit hash, as a "version"
        keep = (f" Claude Code keeps rice {old} for 14 days; to stay on it, pin it: claude plugin marketplace add "
                f"{settings_template.MARKETPLACE_REPO}@release/{old}." if settings_template.semver(old) else "")
        return _stop(f"The installed rice build declares no MAJOR.MINOR.PATCH version ({new}): that release is "
                     f"broken, so it was not used.{keep} Run this command again once a fixed release is out.")
    if not (root / "scripts" / "rice.py").is_file():
        return _stop(f"rice {new} is installed, but {root}/scripts/rice.py is missing.")

    loaded = evidence.build_version()
    if new != old:
        plugin = f"rice plugin: updated {old} -> {new}."
    elif loaded != new:
        # installed before this run (an earlier run of this command, or the OWNER's own update): the
        # repo's first attempt may have asked for a re-run, which must not read as "nothing was updated"
        plugin = f"rice plugin: {new} is installed (this session started on {loaded})."
    elif refreshed:
        # a pinned marketplace is "no newer release offered" too, never "latest"
        plugin = f"rice plugin: no newer release is offered; rice {new} stays installed."
    else:
        plugin = (f"rice plugin: could not check for a newer release ({refresh_why}); "
                  f"staying on the installed {new}.")
    notes = [plugin]
    if loaded != new:
        notes.append(f"This session's hooks and skills are still rice {loaded}; "
                     + (f"start a new session to load {new} (/reload-plugins does not work in Claude Code on "
                        "the web)." if evidence.on_web() else f"/reload-plugins (or a new session) loads {new}."))

    if not reconcile.rice_installed_here(repo_root):
        return UpdateResult(True, [*notes, "This repo is not set up for rice yet: run /rice:bootstrap once to set it up."])
    open_choices, rc = engine("questions", root, repo_root)
    if rc != 0:
        return UpdateResult(False, [*notes, f"The repo could not be checked (exit {rc}: {_brief(open_choices)}); "
                                    "it was not updated. Run this command again."])
    if open_choices.strip():
        return UpdateResult(False, [*notes, "This repo's rice setup has unanswered choices "
                              f"({', '.join(open_choices.split())}), which only /rice:bootstrap asks: run it "
                              f"{'in a new session' if evidence.on_web() else 'after /reload-plugins or in a new session'}. "
                              "The repo was not updated."])
    out, rc = engine("bootstrap", root, repo_root)
    if rc != 0 and VERBATIM not in out:
        notes.append(f"The repo update FAILED (exit {rc}) without a report of its own; nothing is recorded as "
                     "updated. Run this command again.")
    return UpdateResult(rc == 0, notes, out)


def _engine(verb: str, root: Path, repo_root: Path, timeout: float = TOTAL_BUDGET_SECONDS) -> tuple[str, int]:
    """The installed build's own engine verb -- the repo update -- run by path."""
    try:
        r = subprocess.run([sys.executable, str(root / "scripts" / "rice.py"), verb, str(repo_root)],
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"rice {verb} timed out after {int(timeout)}s", 124
    except OSError as exc:
        return f"rice {verb} could not be run: {exc.strerror or exc}", 127
    return r.stdout + (r.stderr if r.returncode else ""), r.returncode


def render(outcome: UpdateResult) -> str:
    """The engine's output with this module's lines at the top of what the
    OWNER is shown (its own agent-facing text, rules included, comes first)."""
    mine = "\n".join(outcome.owner)
    if VERBATIM in outcome.bootstrap_out:
        agent, owner = outcome.bootstrap_out.split(VERBATIM, 1)
        return f"{agent}{VERBATIM}\n{mine}\n" + owner.lstrip("\n")
    if outcome.bootstrap_out:
        return f"{outcome.bootstrap_out}\n{VERBATIM}\n{mine}"
    return f"{VERBATIM}\n{mine}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS

    def left(own: float) -> float:
        return max(1.0, min(own, deadline - time.monotonic()))

    outcome = update(argv[0] if argv else ".",
                     run=lambda cmd: default_run(cmd, timeout=left(CLI_TIMEOUT_SECONDS)),
                     engine=lambda verb, root, repo: _engine(verb, root, repo, timeout=left(TOTAL_BUDGET_SECONDS)))
    print(render(outcome))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
