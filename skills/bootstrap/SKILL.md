---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, model delegation, output style, PROJECT_STATE.md), runs this build's verification rubric, then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

Bootstrap this repo onto rice-bootstrap. Everything below is idempotent by
construction (`reconcile.py`'s apply is create-if-missing / no-op-if-correct
/ conflict-if-drifted), so re-running this skill on a partly or fully
bootstrapped repo is always safe. Re-derive where you are from actual repo
state each time; never assume a first run.

**Nothing is "installed" because a script finished.** Bootstrap ends only
when this build's rubric (`reconcile.check()`) is fully PASS on every
EXISTS/MATCHES item and every BEHAVES item has been exercised — then the
repo is VERIFIED at this build's version, and not before.

Runtime facts this flow is built on (ticket #18, four real Cloud sessions
plus local probes — not assumed):
- Declaring the marketplace/plugin in `.claude/settings.json` does **not**
  install the plugin; a Cloud session never trusts the workspace folder, so
  that declaration is ignored there entirely. It still matters: it is the
  declared delivery version, and it is what `/plugin install` / local
  trust-based installs use.
- `claude plugin marketplace add L26041040/rice-bootstrap` +
  `claude plugin install rice@rice-bootstrap` is the official install, runs
  non-interactively, touches only `~/.claude`, and is loaded by the **next**
  session. Locally that persists. In Cloud `~/.claude` is per-container:
  an install done in one session is gone in the next, so the two commands
  must run at container start (the environment setup script — the same way
  `mattpocock-skills` is present in every fresh container).
- The plugin's output style is addressed as `rice:老弟`.

## Step 0 — Is the plugin actually installed here?

Run `claude plugin list`. If `rice@rice-bootstrap` is listed, this skill is
running from the plugin: go to Step 1.

If it is not listed, **stop and hand off** — this is the one step before
Matt that needs the OWNER, and it is done once per machine/environment:

> rice isn't installed in this environment yet. The official install is:
>
>     claude plugin marketplace add L26041040/rice-bootstrap
>     claude plugin install rice@rice-bootstrap
>
> - **Local machine**: I can run those two commands now; then start a new
>   session and run `/rice:bootstrap` again.
> - **Claude Code on the web (Cloud)**: an install run inside a session does
>   not survive to the next container. Add those two commands to this
>   environment's setup script (that is how `mattpocock-skills` is already
>   present in every fresh container), then start a new session and run
>   `/rice:bootstrap` again.

Do not try to work around this with a per-session install in Cloud: it
would not be loaded until the next session, and it would be gone by then.

## Step 1 — The two owner-defined settings

Read `.claude/settings.json` (create `{}` if absent). Set, only if missing:

- `extraKnownMarketplaces.rice-bootstrap.source.ref` — the **delivery
  version**: the highest SemVer tag of `L26041040/rice-bootstrap`
  (`git ls-remote --tags https://github.com/L26041040/rice-bootstrap.git`).
  If the repo has **no tags yet**, say so plainly and use `main`; do not
  invent a version. `ref` never means "verified" — only the rubric does.
- `env.RICE_SESSION_ARCHIVE` — `"1"` if the OWNER's instruction asked for
  session archiving ("with session archive" or equivalent), otherwise the
  release default (`settings_template.desired_settings` documents it). Ask
  only if the instruction is genuinely silent and you can't tell.

Never overwrite either if already present — they are owner-defined
(ticket #15); ordinary reconcile never reasserts them, and neither does
this step on a re-run.

## Step 2 — Reconcile rice-owned state

From Python (`sys.path.insert(0, "<plugin root>/scripts")`):

```
import reconcile; items = reconcile.apply(repo_root)
```

This creates every rice-defined settings leaf (marketplace source/repo,
`enabledPlugins`, `autoMemoryEnabled`, `outputStyle: rice:老弟`,
`CLAUDE_CODE_SUBAGENT_MODEL`, `permissions.deny` / `permissions.ask`),
copies `.claude/rice.md`, and ensures the `@.claude/rice.md` line exists in
`CLAUDE.md` (creating the file with only that line if it didn't exist,
appending only that line if it did — nothing else in the file is touched).
Project-owned settings are left alone. Do not pass `is_rice_update=True`.

## Step 3 — PROJECT_STATE.md

`project_state.create_if_missing(repo_root)` (`scripts/project_state.py`).
Never `preserve_or_restore` here — that is `rice-update`'s call.

## Step 4 — Run the rubric, fix, re-run, until VERIFIED

`items, summary = reconcile.check(repo_root)` — this build's versioned
rubric. Each item is EXISTS / MATCHES / BEHAVES.

1. Every **EXISTS** and **MATCHES** item must be PASS, except
   `matt:artifacts` (expected FAIL until Step 5) and `rtk` (environment's,
   report as-is). On any other FAIL: fix the specific thing the detail
   names (usually re-run Step 2, or the Step 0 handoff if `plugin:*`
   fails), then re-run `check`. Do not proceed on a FAIL.
2. **BEHAVES** items — exercise them now, in this session, and record the
   outcome next to the item:
   - `git-deny:behaves`: run `git branch --show-current` through Bash.
     Expected: denied by the permission rule. If it *runs*, the deny rules
     are not in effect → FAIL, stop, report.
   - `premium-ask:behaves`: leave for the E2E (#29) unless the OWNER is
     present to answer the approval prompt; say so in the report.
   - `archive:behaves`: `check` reports evidence-based PASS only after a
     real `/clear` has produced an INDEX row + archive in this repo; until
     then it is NOT VERIFIED and says so. Do not fake it.
3. Only when 1 is clean and 2 is recorded: commit and push the rice-owned
   changes, and state the verdict line from `summary` verbatim
   (`MECHANICAL-VERIFIED rice <build>`) plus the BEHAVES outcomes. That —
   and nothing earlier — is "rice's own setup is verified at <build>".

## Step 5 — Hand off to Matt

`bootstrap_state.enter_waiting_for_matt(repo_root)` (`scripts/bootstrap_state.py`),
then tell the OWNER:

> Rice's own setup is verified at <build>. The one remaining step needs you
> personally: run `/mattpocock-skills:setup-matt-pocock-skills` yourself.
> Claude cannot run it or simulate its questions — the skill is
> intentionally OWNER-invoked only. When you've completed it, run
> `/rice:bootstrap` again and I'll verify and finish up.

**Do not report bootstrap as complete while `WAITING_FOR_MATT` is present.**
A false "done" is worse than an honest "waiting on you" (`.claude/rice.md`,
"Trust verification"). What happens after the OWNER runs Matt setup is
ticket #28 (completion detection), which itself waits on #27 — a runtime
probe that needs the OWNER to run Matt setup once for real.

## Re-running this skill

If `bootstrap_state.is_waiting_for_matt(repo_root)` is true, don't redo
Steps 1–3 — re-run `reconcile.check()` and report honestly: still waiting
on Matt (`matt:artifacts` FAIL), or Matt's artifacts are now present and
#28's verification is what's next.
