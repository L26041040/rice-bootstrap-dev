#!/usr/bin/env python3
"""rice update (RICE-ONE-COMMAND-UPDATE-001): the plugin and this repo, to the
latest release, from one command in the running session.

Facts this rests on, each probed in a real interactive session (see
docs/research/RICE-ONE-COMMAND-UPDATE-001-feasibility.md):

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
module only reports what the plugin step did, never "updated" on its own
word: the installed version is read back from `claude plugin list`.

Re-running is the recovery: a step already done reports "already at the
latest release" and the next step runs; nothing is half-applied, because
the plugin step is the platform's and the repo step saves before it writes.
"""
import json
import subprocess
import sys
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
VERBATIM = "--- present this to the OWNER verbatim ---"   # the engine's own marker
MANUAL = (f"claude plugin marketplace update {MARKETPLACE}; claude plugin update {PLUGIN_KEY}; "
          "then /reload-plugins (or a new session) and /rice:bootstrap")


@dataclass
class Run:                                   # what a subprocess answered; also the test seam's shape
    returncode: int
    stdout: str = ""
    stderr: str = ""


def default_run(cmd, timeout=CLI_TIMEOUT_SECONDS) -> Run:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return Run(127, "", f"{cmd[0]}: command not found")
    except subprocess.TimeoutExpired:
        return Run(124, "", f"timed out after {timeout}s")
    return Run(r.returncode, r.stdout, r.stderr)


def _brief(text: str, limit: int = 200) -> str:
    """One line of a CLI's explanation, its end kept: the cause comes last."""
    one = " ".join(str(text).split())
    return one if len(one) <= limit else "..." + one[-(limit - 3):]


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


def _installed(run) -> tuple[dict | None, str]:
    """(the canonical rice install as `claude plugin list --json` reports it,
    why there is none). What a supported install is stays
    `evidence.classify_install`'s decision; the entry itself is
    `evidence.installed_entries`'."""
    listing = run(["claude", "plugin", "list", "--json"])
    probe = lambda cmd: listing                                    # noqa: E731 - one list call, both reads
    _, state, detail, _ = evidence.classify_install(probe)
    if state != "PASS":
        return None, detail
    entries, _ = evidence.installed_entries(probe)
    return next((e for e in entries if e.get("scope") == SCOPE), None), "no user-scope rice install"


@dataclass
class Outcome:
    ok: bool
    owner: list = field(default_factory=list)   # lines for the OWNER's report
    bootstrap_out: str = ""                      # the installed build's own words, if it ran
    bootstrap_rc: int = 0


def _stop(why: str, *more: str) -> Outcome:
    return Outcome(False, [why, *more, "Nothing in this repo was changed."])


def update(repo_root, *, run=default_run, bootstrap=None) -> Outcome:
    repo_root = Path(repo_root)
    bootstrap = bootstrap or _bootstrap
    before, why = _installed(run)
    if before is None:
        return _stop(f"rice cannot be updated from here: {why}", f"By hand: {MANUAL}.")
    old = str(before.get("version"))

    refresh = run(["claude", "plugin", "marketplace", "update", MARKETPLACE, "--json"])
    refreshed = refresh.returncode == 0 and _json_line(refresh.stdout).get("outcome") == "ok"
    refresh_why = "" if refreshed else _brief(_json_line(refresh.stdout).get("message") or refresh.stderr
                                              or f"exit {refresh.returncode}")

    step = run(["claude", "plugin", "update", PLUGIN_KEY, "-s", SCOPE, "--json"])
    result = _json_line(step.stdout)
    if step.returncode != 0 or result.get("outcome") != "ok":
        reason = _brief(result.get("message") or step.stderr or f"exit {step.returncode}")
        return _stop(f"The rice plugin could not be updated: {reason}",
                     *([f"(the marketplace could not be refreshed either: {refresh_why})"] if refresh_why else []))

    # read back what is installed -- never trust the update's own message
    after, why = _installed(run)
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
        plugin = f"rice plugin: already at the latest release ({new})."
    else:
        plugin = (f"rice plugin: could not check for a newer release ({refresh_why}); "
                  f"staying on the installed {new}.")
    notes = [plugin]
    if loaded != new:
        notes.append(f"This session's hooks and skills are still rice {loaded}; "
                     f"/reload-plugins (or a new session) loads {new}. Nothing else depends on it.")

    if not reconcile.rice_installed_here(repo_root):
        return Outcome(True, [*notes, "This repo is not set up for rice yet: run /rice:bootstrap once to set it up."])
    out, rc = bootstrap(root, repo_root)
    return Outcome(rc == 0, notes, out, rc)


def _bootstrap(root: Path, repo_root: Path) -> tuple[str, int]:
    """The installed build's own bootstrap -- the repo update -- run by path."""
    r = subprocess.run([sys.executable, str(root / "scripts" / "rice.py"), "bootstrap", str(repo_root)],
                       capture_output=True, text=True)
    return r.stdout + (r.stderr if r.returncode else ""), r.returncode


def render(outcome: Outcome) -> str:
    """The engine's output with this module's lines at the top of what the
    OWNER is shown (its own agent-facing text, rules included, comes first)."""
    mine = "\n".join(outcome.owner)
    if VERBATIM in outcome.bootstrap_out:
        agent, owner = outcome.bootstrap_out.split(VERBATIM, 1)
        return f"{agent}{VERBATIM}\n{mine}\n{owner.lstrip(chr(10))}"
    if outcome.bootstrap_out:
        return f"{outcome.bootstrap_out}\n{VERBATIM}\n{mine}"
    return f"{VERBATIM}\n{mine}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    outcome = update(argv[0] if argv else ".")
    print(render(outcome))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
