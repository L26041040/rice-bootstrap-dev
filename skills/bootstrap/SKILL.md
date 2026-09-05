---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, model delegation, output style, PROJECT_STATE.md), then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

Bootstrap this repo onto rice-bootstrap. Everything below is idempotent by
construction (`reconcile.py`'s apply is create-if-missing / no-op-if-correct
/ conflict-if-drifted), so re-running this skill on a partly or fully
bootstrapped repo is always safe. Re-derive where you are from actual repo
state each time; never assume a first run.

Runtime facts this flow is built on (ticket #18, verified in real Cloud and
local sessions — not assumed):
- Declaring the marketplace/plugin in `.claude/settings.json` does **not**
  install the plugin. Installing is a separate, official step.
- A Cloud session never trusts the workspace folder, so project-level
  `extraKnownMarketplaces` / `enabledPlugins` are ignored there entirely.
  In Cloud the plugin only exists if it is installed at **user scope in the
  environment** (the same way `mattpocock-skills` is), and an install done
  inside one Cloud session does not survive to the next container.
- A newly installed plugin is only loaded by a **new** session.
- The plugin's output style is addressed as `rice:老弟`.

## Step 0 — Is the plugin actually installed here?

Run `claude plugin list`. If `rice@rice-bootstrap` is listed, this skill is
running from the plugin: go to Step 1.

If it is not listed (you are reading this file from a checkout, or from an
uninstalled state), **stop and hand off** — this is the one step the OWNER
must do, once per environment, and it is the only interruption before Matt:

> rice isn't installed in this environment yet.
> - **Cloud**: add `rice@rice-bootstrap` (marketplace `L26041040/rice-bootstrap`)
>   to the environment the same way `mattpocock-skills@mattpocock` is
>   already there — user-scope, environment-level. An install run inside
>   a session does not persist to the next container.
> - **Local**: `claude plugin marketplace add L26041040/rice-bootstrap`
>   then `claude plugin install rice@rice-bootstrap`.
> Then start a new session and run `/rice:bootstrap` again.

Do not try to work around this with a per-session install: it would not be
loaded until the next session anyway, and in Cloud it would be gone by then.

## Step 1 — The two owner-defined settings

Read `.claude/settings.json` (create `{}` if absent). Set, only if missing:

- `extraKnownMarketplaces.rice-bootstrap.source.ref` — the highest SemVer
  tag of `L26041040/rice-bootstrap` (`git ls-remote --tags
  https://github.com/L26041040/rice-bootstrap.git`). If the repo has **no
  tags yet**, say so plainly and use `main`; do not invent a version.
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

## Step 4 — Verify, then commit

`items, summary = reconcile.check(repo_root)`. Every rice-owned item
(`plugin:installed`, every `settings:*`, `rice.md`, `CLAUDE.md:import-line`)
must be PASS. `matt:artifacts` is expected to be FAIL at this point and
`rtk` is whatever the environment says — neither is rice's to fix and
neither blocks this step. If anything rice-owned is FAIL or conflict, stop
and report it plainly. Otherwise commit and push the rice-owned changes.

## Step 5 — Hand off to Matt

`bootstrap_state.enter_waiting_for_matt(repo_root)` (`scripts/bootstrap_state.py`),
then tell the OWNER:

> Rice's own setup is done and verified. The one remaining step needs you
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
Steps 1–4 — re-run `reconcile.check()` and report honestly: still waiting
on Matt (`matt:artifacts` FAIL), or Matt's artifacts are now present and
#28's verification is what's next.
