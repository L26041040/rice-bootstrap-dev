#!/usr/bin/env python3
"""RICE-BOOTSTRAP-044: rice's lifecycle engine -- the one place that knows
the ORDER of things.

Four verbs, and nothing outside this module needs to know more than these:

    bootstrap  configure this repo from scratch, up to the Matt hand-off
    continue   finish a bootstrap after the OWNER's Matt setup (Stop hook)
    styles     the output-style values the OWNER may choose from
    questions  the OWNER choices still unanswered here (none once answered)

There is no update verb (RICE-FINAL-001). Rice has exactly two repo
lifecycles -- bootstrap and uninstall -- and neither ever asks the OWNER to
migrate a ref or reason about which build is "latest". An update is the
platform's: a new session (or /reload-plugins, which the web does not have) loads it, and one re-run of
bootstrap brings a repo up to date.

Every lifecycle verb runs the same canonical flow, which lives here and
only here:

    normal path -> verify -> PASS: done
                          -> FAIL: deterministic repair -> verify again
                                -> still FAIL: the model, last

This module decides nothing about correctness. It does not evaluate a gate,
parse evidence, rebuild a verdict, or repair anything itself:

    reconcile  owns what rice's desired state is, and writes it
    verify     is the sole authority on PASS/FAIL, and the sole writer of
               the verified-version file and remover of the pending marker
    repair     owns which failure identity maps to which lossless restore

What this module owns instead is sequence -- which of those runs when --
and every decision that follows from it (RICE-BOOTSTRAP-044/-044B), plus
one step of its own on an update: saving the OWNER's work with git before
rice's first write, and where that push goes (RICE-UPDATE-PRESAVE-001):

- whether rice is even loaded here, and whether the install is the
  supported one;
- whether this repo still has to wait for the OWNER's Matt setup, or can
  go straight to final verification because Matt's artifacts are there.

Each verb returns, besides the verifier's word, a `directive`: the exact
text the OWNER must see. The skills and the Stop hook are adapters -- they
collect answers, run the engine's verbs, and present the directive verbatim.
They carry no steps, no branching on gates, and no lifecycle knowledge of
their own.
"""
import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import output_styles  # noqa: E402
import evidence  # noqa: E402
import reconcile  # noqa: E402
import repair  # noqa: E402
import settings_template  # noqa: E402
import verify  # noqa: E402

VERIFIER = Path(__file__).resolve().parent / "verify.py"

# The immutable public branch this very build was released from
# (RICE-FINAL-001). Read from the build, never asked of upstream.
RELEASE_REF = settings_template.release_ref()
UNSET = reconcile.UNSET  # "the OWNER was not asked" (see reconcile.write_owner_choices)



def _memoized(probe):
    """One external command, one spawn per run. Readiness and each verifier
    run ask the same two questions (`claude plugin list --json`,
    `rtk --version`); re-spawning them per call is pure latency, and on the
    Stop hook that latency lands at the end of every turn."""
    cache: dict[tuple, tuple] = {}

    def run(cmd):
        key = tuple(cmd)
        if key not in cache:
            try:
                cache[key] = (probe(cmd), None)
            except Exception as exc:  # noqa: BLE001 - replayed verbatim below
                cache[key] = (None, exc)
        result, exc = cache[key]
        if exc is not None:
            raise exc
        return result

    return run


def default_probes() -> dict:
    """The probes every caller should pass unless it has its own: the real
    commands, asked at most once each."""
    return {"plugin_probe": _memoized(evidence._default_plugin_probe),
            "rtk_probe": _memoized(evidence._default_rtk_probe)}


@dataclass
class Outcome:
    """What a verb did: the verifier's last word (None when the stage never
    got far enough to verify), what repair restored on the way, whether the
    repo is now parked waiting for the OWNER's Matt setup, which lifecycle
    stage produced this, and the text the adapter must present verbatim."""
    report: verify.Report | None = None
    repaired: repair.RepairResult | None = None
    waiting_for_matt: bool = False
    stage: str = ""
    directive: list = field(default_factory=list)
    blocked: bool = False

    @property
    def ok(self) -> bool:
        """Did this stage complete everything that was not the OWNER's or the
        environment's to do? The verifier decides whenever it ran; otherwise
        the stage does -- handing off to the OWNER is a success, a probe that
        could not answer is not."""
        return self.report.ok if self.report is not None else not self.blocked

    @property
    def verdict(self) -> str:
        return self.report.verdict if self.report is not None else ""

    @property
    def lines(self) -> list[str]:
        out = []
        if self.repaired is not None:
            out += self.repaired.lines
        elif self.report is not None and self.report.ok:
            out.append("repair: not needed (normal path passed)")
        if self.report is not None:
            out += self.report.lines
        return out


def _precondition_stop(report: verify.Report) -> Outcome | None:
    """A blocked outcome when the verifier says this session must not change
    the repo at all (RICE-SPEC-001 I16): it is not running the installed
    build (an update not yet loaded), or the repo was finalized by a newer
    build. None when nothing blocks."""
    if not report.blocking:
        return None
    return Outcome(report=report, stage="blocked", blocked=True,
                   directive=_precondition_directive(report.blocking))


def _pipeline(repo_root, *, final: bool, carried: repair.RepairResult | None = None, **probes) -> Outcome:
    """The canonical flow, in the only place it exists as code. `carried` is
    what an earlier half of the same lifecycle already restored -- a Gate A
    repair stays in the report even when Gate B then runs cleanly, so the
    output never claims repair was "not needed" after it rewrote files.
    Repair never runs while a precondition fails: the wrong build must not
    rewrite the repo in the name of fixing it."""
    report = verify.run(repo_root, final=final, **probes)
    if report.ok:
        return Outcome(report=report, repaired=carried)
    stop = _precondition_stop(report)
    if stop is not None:
        return stop
    repaired = repair.repair(repo_root, report, **probes)
    if carried is not None:
        repaired = repair.RepairResult(applied=carried.applied + repaired.applied,
                                       unresolved=carried.unresolved + repaired.unresolved)
    return Outcome(report=verify.run(repo_root, final=final, **probes), repaired=repaired)


def _matt_started(repo_root, **probes) -> bool:
    """Has the OWNER's Matt setup left anything behind? Asked of reconcile,
    which owns what Matt's artifacts are -- never inspected here."""
    plugin_probe = probes.get("plugin_probe") or evidence._default_plugin_probe
    return any(evidence.matt_presence(Path(repo_root), plugin_probe).values())


# --------------------------------------------------------------------------
# the official install, as this environment actually has it
# --------------------------------------------------------------------------

def _canonical_install(**probes) -> tuple[list[str] | None, list[str]]:
    """(versions installed at the canonical scope, directive).

    The decision is `evidence.classify_install`'s -- the same one the
    verifier's `plugin:scope` item reports, so rice never holds a second
    truth about what a supported install is. What rice adds is the shape the
    OWNER needs: a definite stop with every scope named and the exact
    cleanup commands."""
    versions, state, detail, scopes = evidence.classify_install(probes.get("plugin_probe"))
    if state == "PASS":
        return versions, []
    if state != "FAIL":
        # could not classify: never a cleanup instruction, because there is no
        # runnable one to give. `scopes is None` = the probe could not answer;
        # `{}` = it answered, and rice is simply not installed.
        return None, (_install_directive() if scopes == {} else _install_unreadable_directive(detail))
    return None, _unsupported_install_directive(detail, scopes)


# --------------------------------------------------------------------------
# the four verbs
# --------------------------------------------------------------------------

def styles(repo_root, **probes) -> list[dict]:
    """Every output-style value that would resolve in this repo, for the
    OWNER's Step 1 choice."""
    plugin_probe = probes.get("plugin_probe") or evidence._default_plugin_probe
    return output_styles.available(repo_root, plugin_probe,
                                   self_plugin=(settings_template.PLUGIN_NAME, evidence.PLUGIN_ROOT))


# the bootstrap flag each unanswered OWNER-defined key is asked for under
_QUESTION_FLAGS = {settings_template.ARCHIVE_KEY: "archive", settings_template.STYLE_KEY: "output-style"}


def questions(repo_root) -> list[str]:
    """The OWNER's Step 1 choices bootstrap would still write, by flag name:
    both on a fresh repo, none once answered (RICE-FINAL-UX-REVIEW-001)."""
    return [_QUESTION_FLAGS[k] for k in reconcile.unanswered_owner_choices(repo_root)]


def bootstrap(repo_root, *, archive=None, output_style=UNSET, **probes) -> Outcome:
    """A fresh repo, up to (and when possible past) the Matt hand-off.

    Rice must be loaded before any of this means anything, so that is asked
    first and nothing is written when it is not. On an update the OWNER's
    work is then saved -- committed and pushed -- before rice's first write
    (I24). Then the OWNER's answers are written once if absent, rice-owned
    state is reconciled, and Gate A goes
    through the canonical flow. On a Gate A pass the repo enters the pending
    state -- and if Matt's artifacts are already present (a re-bootstrap, or
    a repo that had them all along), final verification happens here and now
    rather than asking the OWNER for a setup they have already done. Either
    way the OWNER invokes bootstrap exactly once (RICE-BOOTSTRAP-028), in one
    session, with nothing to restart mid-flow (RICE-FINAL-001)."""
    repo_root = Path(repo_root)
    loaded = verify.run(repo_root, **probes)
    installed = loaded.item("plugin:installed")
    state = installed.state if installed is not None else "MISSING"
    if state == "FAIL":
        return Outcome(report=loaded, stage="install-required", blocked=True,
                       directive=_install_directive())
    if state != "PASS":
        # the probe could not answer -- not the same as "rice is not installed"
        return Outcome(report=loaded, stage="blocked", blocked=True,
                       directive=_unknown_install_directive(installed))
    # ... and the install must be the supported one before anything is written
    _, install_directive = _canonical_install(**probes)
    if install_directive:
        return Outcome(report=loaded, stage="blocked", blocked=True, directive=install_directive)
    # ... and this session must be the build that may change this repo (I16)
    stop = _precondition_stop(loaded)
    if stop is not None:
        return stop
    # ... and an update saves the OWNER's work before rice writes (I24)
    stop, saved = _save_before_update(repo_root)
    if stop is not None:
        return stop
    outcome = _configure(repo_root, archive=archive, output_style=output_style, **probes)
    if saved:
        outcome.directive = [*saved, "", *outcome.directive]
    return outcome


def _configure(repo_root: Path, *, archive, output_style, **probes) -> Outcome:
    """Bootstrap past its preconditions: the OWNER's answers, rice-owned
    state, Gate A, and -- when Matt's artifacts are there -- Gate B."""
    reconcile.write_owner_choices(repo_root, archive=archive, output_style=output_style)
    reconcile.apply(repo_root)

    gate_a = _pipeline(repo_root, final=False, **probes)
    if not gate_a.ok:
        return Outcome(report=gate_a.report, repaired=gate_a.repaired, stage="blocked",
                       directive=_blockers_directive(gate_a.report))
    reconcile.enter_waiting_for_matt(repo_root)
    matt = gate_a.report.item("matt:artifacts")
    if matt is not None and matt.state == "PASS":
        final = _pipeline(repo_root, final=True, carried=gate_a.repaired, **probes)
        final.stage = "finalized" if final.ok else "blocked"
        final.directive = (_finalized_directive(final.report.verdict) if final.ok
                           else _blockers_directive(final.report))
        return final
    return Outcome(report=gate_a.report, repaired=gate_a.repaired, waiting_for_matt=True,
                   stage="waiting-for-matt", directive=_matt_handoff_directive(gate_a.report.verdict))


# The save's push runs inside the bootstrap command the agent started: it must
# give up well before that command's own default timeout, and never sit at a
# credential prompt no one can answer.
SAVE_PUSH_TIMEOUT_SECONDS = 60

# git operations left unfinished are the OWNER's: saving would complete or lose them
_UNFINISHED_GIT_OPERATIONS = (("MERGE_HEAD", "a merge"), ("CHERRY_PICK_HEAD", "a cherry-pick"),
                              ("REVERT_HEAD", "a revert"), ("rebase-merge", "a rebase"),
                              ("rebase-apply", "a rebase or am"))


def _git(repo_root: Path, *args, **kw):
    import archive_hook  # local: the archive sync's git runner, as evidence uses its helpers
    return archive_hook.run(["git", "-C", str(repo_root), *args], **kw)


def _git_reason(r) -> str:
    """Git's own words: its last lines carry the reason (a remote's rejection
    comes before the final "failed to push"); hints are not it."""
    out = [l.strip() for l in f"{r.stdout}\n{r.stderr}".splitlines()
           if l.strip() and not l.strip().startswith("hint:")]
    return " / ".join(out[-4:]) if out else f"exit {r.returncode}"


def _push_destination(repo_root: Path, branch: str) -> tuple[str, bool, str]:
    """(remote, tracked, refusal): where git itself would push `branch` --
    `branch.<b>.pushRemote`, else `remote.pushDefault`, else its upstream's
    remote, else `origin` -- whether it has an upstream, and why not to push
    at all: an upstream of another name, which git's own push.default=simple
    refuses too. The save pushes a branch only under its own name."""
    def config(key: str) -> str:
        return _git(repo_root, "config", "--get", key).stdout.strip()
    upstream_remote, merge = config(f"branch.{branch}.remote"), config(f"branch.{branch}.merge")
    remote = config(f"branch.{branch}.pushRemote") or config("remote.pushDefault") or upstream_remote or "origin"
    tracked = bool(upstream_remote and merge)
    refusal = ""
    if tracked and remote == upstream_remote and merge != f"refs/heads/{branch}":
        refusal = (f"its upstream is {upstream_remote}/{merge.removeprefix('refs/heads/')}, a branch of"
                   " another name -- rice pushes a branch only under its own name")
    return remote, tracked, refusal


def _give_back_rices_writes(repo_root: Path, index: str) -> None:
    """Rice's own writes from an earlier run of this same update -- a new
    session's first run stops for the git-deny probe after writing -- are
    rice's commit to make, not the OWNER's work: staged back to how the
    OWNER's own index (`index`, a tree) had them."""
    staged = _git(repo_root, "diff", "--cached", "--name-only", "-z").stdout.split("\0")
    rices = [rel for rel in staged if rel in reconcile.RICE_WRITES
             and reconcile.only_rices_change(repo_root, rel, _committed_text(repo_root, rel))]
    if rices:
        _git(repo_root, "restore", "--staged", f"--source={index}", "--", *rices)


def _committed_text(repo_root: Path, rel: str) -> str | None:
    """`rel` as HEAD has it, None when HEAD has no such file."""
    r = _git(repo_root, "show", f"HEAD:{rel}")
    return r.stdout if r.returncode == 0 else None


def _save_before_update(repo_root: Path) -> tuple[Outcome | None, list[str]]:
    """RICE-UPDATE-PRESAVE-001: on a real update -- this repo finalized by an
    older rice than this build -- the OWNER running bootstrap is their
    authorization to save everything first: `git add -A` (`.gitignore`
    applies), less rice's own pending writes, a commit when that staged
    anything, then a plain push of the current branch, unpushed commits
    included, to where git itself would push it, under its own name. First
    installs and same-version re-runs save nothing. Called only once the
    preconditions passed, so a session that may not change this repo never
    commits or pushes it either.

    Returns (blocked outcome, []) when a step fails -- nothing of rice's is
    written then, and the work stays in the tree with the OWNER's own index,
    or in the local commit -- else (None, the lines to show the OWNER)."""
    recorded, build, order = verify.version_order(repo_root)
    if order != -1:
        return None, []

    def stop(step: str, reason: str, kept: str, then: str):
        return Outcome(stage="blocked", blocked=True, directive=[
            f"rice did not update this repo ({recorded} -> {build}): saving your work first failed,"
            " so nothing of rice's was written.",
            f"  - {step}: {reason}",
            kept,
            "",
            f"Report this to the OWNER and stop. {then} Then run this same command again: it saves and updates again.",
        ]), []

    as_left = "Your changes are as you left them."
    import archive_hook  # local: the archive sync's branch helper, as evidence uses it
    branch = archive_hook._current_branch(repo_root)
    if not branch:
        return stop("the current branch", "HEAD is not on a branch with commits (detached, unborn,"
                    " or not a git repository)", as_left, "Check out the branch you work on.")
    gitdir = Path(repo_root) / _git(repo_root, "rev-parse", "--git-dir").stdout.strip()
    for state_file, what in _UNFINISHED_GIT_OPERATIONS:
        if (gitdir / state_file).exists():
            return stop("the working tree", f"{what} is in progress", as_left, f"Finish or abort {what} first.")
    index = _git(repo_root, "write-tree")  # the OWNER's own staging, to give back exactly
    if index.returncode != 0:
        return stop("the index", _git_reason(index), as_left, "Resolve the conflicts first.")
    index = index.stdout.strip()
    remote, tracked, refusal = _push_destination(repo_root, branch)
    if refusal:
        return stop(f"pushing {branch}", refusal, as_left, "Push this branch under its own name yourself.")

    r = _git(repo_root, "add", "-A")
    if r.returncode != 0:
        _git(repo_root, "read-tree", index)  # index only: the files themselves are untouched
        return stop("git add -A", _git_reason(r), as_left, "Fix what stopped `git add`.")
    _give_back_rices_writes(repo_root, index)
    subject = f"rice: save work before updating rice {recorded} -> {build}"
    pending = _git(repo_root, "diff", "--cached", "--quiet").returncode
    if pending == 1:
        r = _git(repo_root, "commit", "-q", "-m", subject)
    if pending not in (0, 1) or (pending == 1 and r.returncode != 0):
        _git(repo_root, "read-tree", index)
        return stop("git commit", _git_reason(r) if pending == 1 else f"git diff --cached exited {pending}",
                    as_left, "Fix what stopped the commit.")
    sha = _git(repo_root, "rev-parse", "--short", "HEAD").stdout.strip()
    ahead = _git(repo_root, "rev-list", "--count", "@{push}..HEAD")
    pushed = ahead.returncode != 0 or ahead.stdout.strip() != "0"
    if pushed:
        push = ["push", *(() if tracked else ("-u",)), remote, f"HEAD:refs/heads/{branch}"]
        r = _git(repo_root, *push, timeout=SAVE_PUSH_TIMEOUT_SECONDS, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            kept = (f"Your changes are committed locally as {sha}, not pushed." if pending == 1
                    else "Your commits are still here, not pushed.")
            return stop("git " + " ".join(push), _git_reason(r), kept,
                        "Push it yourself (with no remote, `git remote add origin <url>` first).")
    # the save commit is this run's, or an earlier run's of this same update
    saved = _git(repo_root, "log", "-1", "--format=%s").stdout.strip() == subject
    if not (saved or pushed):
        return None, []
    did = [f"your changes are committed as {sha}"] if saved else []
    did.append(f"{branch} is pushed to {remote}")
    return None, [f"Before updating rice {recorded} -> {build}: {' and '.join(did)}."]


def continue_after_matt(repo_root, **probes) -> Outcome | None:
    """The Stop hook's verb. None means "nothing to continue" -- this repo
    is not mid-bootstrap, or the OWNER's Matt setup has not started, and the
    turn must not be disturbed. Anything else goes through the canonical
    flow to Gate B, so an incomplete Matt setup is reported rather than left
    silently pending (RICE-BOOTSTRAP-042)."""
    repo_root = Path(repo_root)
    if not reconcile.is_waiting_for_matt(repo_root):
        return None
    if not _matt_started(repo_root, **probes):
        return None
    outcome = _pipeline(repo_root, final=True, **probes)
    outcome.stage = "finalized" if outcome.ok else "blocked"
    return outcome


# --------------------------------------------------------------------------
# directives: the exact text the OWNER must see, decided here, presented by
# the adapter verbatim
# --------------------------------------------------------------------------

def _install_directive() -> list[str]:
    return [
        "rice isn't loaded in this environment yet, so nothing has been written to this repo.",
        "The official install is:",
        "",
        f"    claude plugin marketplace add {settings_template.MARKETPLACE_REPO}",
        f"    claude plugin install {settings_template.PLUGIN_KEY}",
        "",
        f"(That installs whatever is newest. To pin this exact version instead, add @{RELEASE_REF} to the first line.)",
        "",
        "- Local machine: those two commands can be run now, then start a new session",
        "  and ask for the bootstrap there.",
        "- Claude Code on the web: the fix belongs at the Environment level, not this",
        "  session -- add those two commands to a Cloud Environment's setup script (a",
        "  dedicated rice Environment, or an existing one alongside mattpocock-skills /",
        "  RTK), then start a new session from it. Every session from that Environment",
        "  onward already has rice loaded. Running them only inside this session is a",
        "  one-off recovery: it does not survive to the next container.",
    ]


def _matt_handoff_directive(verdict: str) -> list[str]:
    """The terse hand-off, and nothing after it (RICE-BOOTSTRAP-028)."""
    return [
        f"`{verdict}`",
        "",
        "還差 Matt repo setup；完成後 Rice 會自動續接 final verification。",
        "",
        "/mattpocock-skills:setup-matt-pocock-skills",
    ]


# What a bootstrap and Matt's setup write; every other working-tree change is
# the OWNER's. Told to commit "the changes in the working tree", a real
# session ran `git add -A` and pushed the OWNER's unrelated work with rice's
# (RICE-FINAL-UX-REVIEW-001). On an update the engine's own save (I24) has
# already committed that work by the time the agent commits rice's.
_COMMIT_PATHS = (".claude/", "CLAUDE.md", "AGENTS.md", f"{Path(evidence.MATT_REQUIRED_DOCS[0]).parent}/")
COMMIT_ONLY = ("stage only " + ", ".join(f"`{p}`" for p in _COMMIT_PATHS) + ", by name -- never `git add -A`"
               " or `git add .` -- and leave every other working-tree change uncommitted for the OWNER,"
               " naming it in your report")

# rice.md's own output rules, by heading: the session that bootstraps never
# loaded rice.md, so rice hands these to the agent wherever it speaks during
# a bootstrap (RICE-BOOTSTRAP-UX-FIX-001).
_OUTPUT_RULE_HEADINGS = ("## Conversation numbering", "## Single-block output")


def output_rules() -> str:
    """rice.md's output rules, in its own words, for an agent that may not
    have rice.md loaded: what every formal report in a bootstrap follows."""
    text = reconcile.RICE_MD_RESOURCE.read_text(encoding="utf-8")
    parts = [h + "\n" + text.split(h + "\n", 1)[1].split("\n## ", 1)[0].strip()
             for h in _OUTPUT_RULE_HEADINGS if h + "\n" in text]
    return ("rice's output rules (from .claude/rice.md, which this session may not have loaded) apply to every"
            " formal report in this bootstrap -- this one, the summary after the OWNER's Matt setup, and the"
            " finalization; native choice prompts stay native:\n\n" + "\n\n".join(parts))


# CLAUDE.md imports `.claude/rice.md` when a session starts, so the session
# that bootstrapped keeps the rules it started with (RICE-FINAL-UX-REVIEW-001)
WHEN_RULES_LOAD = ("Rice's working rules (`.claude/rice.md`) load when a session starts: this session keeps"
              " the ones it started with, and the current rules apply after `/clear` or in a new session.")


def _finalized_directive(verdict: str) -> list[str]:
    return [
        f"`{verdict}`",
        "",
        f"The verifier has stamped the verified-version file. Commit and push this bootstrap's"
        f" changes ({COMMIT_ONLY}) and quote that line verbatim. This repo needs no Matt"
        " hand-off: the setup is already there.",
        "",
        WHEN_RULES_LOAD,
    ]


def _blockers_directive(report) -> list[str]:
    return [
        "Deterministic repair has already had its turn; what is left is yours, and"
        " only what is listed:",
        *[f"  - {r}" for r in report.reasons],
        "",
        "Do exactly what each line's detail says, then run this same command again."
        " Never write Matt setup content yourself -- a missing Matt artifact is the"
        " OWNER's to produce, with /mattpocock-skills:setup-matt-pocock-skills.",
    ]


def _precondition_directive(reasons) -> list[str]:
    return [
        "This session's rice must not change this repo, so nothing was written:",
        *[f"  - {r}" for r in reasons],
        "",
        "Report this to the OWNER and stop. Do not install, update or reload rice"
        " yourself; once the OWNER has fixed it, this same command runs normally.",
    ]


def _unknown_install_directive(item) -> list[str]:
    return [
        "Whether rice is loaded here could not be determined, so nothing was written:",
        f"  - {item.detail if item is not None else 'the verifier produced no install result'}",
        "",
        "Report this and stop. Do not install, and do not assume either answer.",
    ]


def _unsupported_install_directive(detail: str, scopes: dict) -> list[str]:
    """The verifier's own sentence about this install, plus the cleanup as
    runnable lines. Rice removes nothing and picks no winner."""
    return [
        f"{detail}.",
        "",
        "Installed right now:",
        *[f"  - {scope} scope: {sorted(set(v))}" for scope, v in sorted(scopes.items())],
        "",
        "Minimal cleanup -- keep the canonical"
        f" {evidence.CANONICAL_SCOPE}-scope install, remove every other rice install:",
        *[f"    claude plugin uninstall {settings_template.PLUGIN_KEY} --scope {scope}"
          for scope in sorted(scopes) if scope != evidence.CANONICAL_SCOPE],
        *([f"    claude plugin install {settings_template.PLUGIN_KEY}"]
          if evidence.CANONICAL_SCOPE not in scopes else []),
        "",
        "Nothing was written. Rice will not remove either install for you, and will not"
        " guess which one should win. Do the cleanup, then bootstrap again.",
    ]


def _install_unreadable_directive(err: str) -> list[str]:
    return [
        f"The install state could not be read ({err}), so there is no way to tell"
        " which build this session would load. Nothing was written.",
        "",
        "Report this and stop -- rice never bootstraps against an unknown install.",
    ]


def continuation_message(repo_root, outcome: Outcome) -> str:
    """What the Stop hook hands back to the model. The verifier's own verdict
    and reasons, verbatim, plus the one instruction that belongs to this
    lifecycle: finish the job in this same turn, and never write Matt's
    content or send the OWNER back to rice -- and, whatever the outcome,
    rice's output rules for the report that follows."""
    return f"{_continuation(repo_root, outcome)}\n\n{output_rules()}"


def _continuation(repo_root, outcome: Outcome) -> str:
    if outcome.blocked and outcome.directive:
        return "rice: " + "\n".join(outcome.directive) + "\nThis hook stays quiet for the rest of this turn."
    if outcome.ok:
        repaired = ""
        if outcome.repaired and outcome.repaired.applied:
            repaired = (" Deterministic repair restored:\n"
                        + "\n".join(f"  - {a}" for a in outcome.repaired.applied) + "\n")
        return (
            f"rice: {outcome.verdict}. The verifier has stamped the verified-version file "
            f"and cleared the pending marker.{repaired} Commit and push the bootstrap, "
            f"Matt and finalization changes ({COMMIT_ONLY}), then report `{outcome.verdict}` "
            f"to the OWNER as one formal report -- your conversation number, then one fenced code block"
            f" holding the verdict, what you committed and pushed, and this line: {WHEN_RULES_LOAD} --"
            f" and nothing outside it."
        )
    done = outcome.repaired
    repaired = "\n".join(f"  - {a}" for a in done.applied) if done and done.applied else "  - nothing was mechanically repairable"
    # repair passes non-repairable items through in the verifier's own words,
    # so only what it adds (Matt artifacts it refused to rebuild) is new here
    extra = [u for u in (done.unresolved if done else []) if u not in outcome.report.reasons]
    refused = "\n".join(f"  - {u}" for u in extra)
    return (
        "rice: final verification did not pass; the verified-version file is "
        "untouched.\nDeterministic repair ran first:\n" + repaired
        + "\nStill blocking after repair:\n" + "\n".join(f"  - {r}" for r in outcome.report.reasons)
        + (("\nRepair refused to guess at:\n" + refused) if refused else "")
        + "\nYou are the last fallback. Fix exactly what each line names; never "
        "write Matt setup content yourself -- a Matt artifact listed above must be "
        "reported to the OWNER as missing, by name, because only the official Matt "
        "setup can produce it. Then finish the job in this same turn by running "
        f"`python3 {VERIFIER} {repo_root} --final` yourself. Whatever is the OWNER's to do -- a "
        "missing Matt artifact, a GitHub credential -- goes to them once, as one formal report. This "
        "hook stays quiet for the rest of this turn."
    )


# --------------------------------------------------------------------------
# CLI -- what the skills invoke
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice lifecycle: bootstrap / continue / styles / questions")
    sub = ap.add_subparsers(dest="verb", required=True)
    b = sub.add_parser("bootstrap", help="configure this repo, up to (or past) the Matt hand-off")
    b.add_argument("repo_root", nargs="?", default=".")
    b.add_argument("--archive", choices=("0", "1"), help="the OWNER's session-archive choice (written only if absent)")
    b.add_argument("--output-style", help="the OWNER's style choice, or 'none' for Claude's default (written only if absent)")
    for verb, help_text in (("continue", "finish a bootstrap after the OWNER's Matt setup"),
                            ("styles", "list the output styles the OWNER may choose"),
                            ("questions", "list the OWNER choices still unanswered in this repo")):
        sp = sub.add_parser(verb, help=help_text)
        sp.add_argument("repo_root", nargs="?", default=".")
    args = ap.parse_args(argv)

    if args.verb == "questions":
        for q in questions(args.repo_root):
            print(q)
        return 0
    probes = default_probes()
    if args.verb == "styles":
        for s in styles(args.repo_root, **probes):
            print(f"{s['value']}\t{s['source']}")
        return 0
    if args.verb == "bootstrap":
        style = UNSET if args.output_style is None else (None if args.output_style == "none" else args.output_style)
        outcome = bootstrap(args.repo_root,
                            archive=None if args.archive is None else args.archive == "1",
                            output_style=style, **probes)
    else:
        outcome = continue_after_matt(args.repo_root, **probes)
        if outcome is None:
            print("continue: nothing to continue (not mid-bootstrap, or Matt setup has not started)")
            return 0
    print("\n".join(outcome.lines))
    if outcome.directive:
        print("\n" + output_rules())
        print("\n--- present this to the OWNER verbatim ---")
        print("\n".join(outcome.directive))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
