---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, model delegation, output style, PROJECT_STATE.md), runs the mechanical verifier, then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

This skill describes *how to operate* rice. It decides nothing. The only
authority on whether this repo is verified is the deterministic verifier:

```
python3 "<plugin root>/scripts/verify.py" <repo_root>          # Gate A
python3 "<plugin root>/scripts/verify.py" <repo_root> --final  # Gate B
```

Only its output may say `RICE-VERIFIED <build>` or `FINAL-VERIFIED <build>`,
and only its Gate B pass writes `.claude/rice-version`. If it prints a
FAIL line, the repo is not verified — there is nothing for you to weigh,
waive, or interpret; fix what the listed item says and run it again.

Everything below is idempotent (`reconcile.apply` is create-if-missing /
no-op-if-correct / conflict-if-drifted), so re-running on a partly or fully
bootstrapped repo is always safe. Re-derive where you are from the verifier
output each time; never assume a first run.

Runtime facts (ticket #18, four real Cloud sessions plus local probes):
declaring the marketplace/plugin in `.claude/settings.json` does not install
it, and a Cloud session never trusts the folder so that declaration is
ignored there; the official install is two non-interactive commands that
touch only `~/.claude`, loaded by the **next** session; in Cloud `~/.claude`
is per-container, so those commands must run at container start (where
`mattpocock-skills` is provisioned). The plugin's output style is `rice:老弟`.

## Step 0 — Is rice installed here?

Run the verifier once. If `plugin:installed` is FAIL, **stop and hand off**
(this is the one step before Matt that needs the OWNER, once per
machine/environment):

> rice isn't installed in this environment yet. The official install is
>
>     claude plugin marketplace add L26041040/rice-bootstrap
>     claude plugin install rice@rice-bootstrap
>
> - **Local machine**: I can run those two commands now, then you start a
>   new session and say "bootstrap this repo with rice" again.
> - **Claude Code on the web**: an install run inside a session does not
>   survive to the next container. Add those two commands to this
>   environment's setup (how `mattpocock-skills` is already there), then
>   start a new session.

No per-session workaround in Cloud: it would not be loaded until the next
session, and it would be gone by then.

## Step 1 — The two owner-defined settings

Read `.claude/settings.json` (create `{}` if absent). Set, only if missing:

- `extraKnownMarketplaces.rice-bootstrap.source.ref` — the **delivery
  mechanism only**: the highest SemVer tag of `L26041040/rice-bootstrap`
  (`git ls-remote --tags https://github.com/L26041040/rice-bootstrap.git`).
  If the repo has no tags yet, say so and use `main`. This value is never
  a verified version; `.claude/rice-version` (verifier-written) is.
- `env.RICE_SESSION_ARCHIVE` — `"1"` if the OWNER asked for session
  archiving, otherwise the release default. Ask only if genuinely unclear.

Never overwrite either if present (owner-defined, ticket #15). Never touch
`.claude/rice-version` — it is not yours to write, in any step.

## Step 2 — Reconcile rice-owned state

```
import sys; sys.path.insert(0, "<plugin root>/scripts"); import reconcile
reconcile.apply(repo_root)          # never is_rice_update=True here
```

Creates every rice-defined settings leaf, copies `.claude/rice.md`, ensures
the `@.claude/rice.md` line in `CLAUDE.md`. Project-owned content is left
alone.

## Step 3 — PROJECT_STATE.md

`project_state.create_if_missing(repo_root)`. Never `preserve_or_restore`
here — that is `rice-update`'s call.

## Step 4 — Gate A: run the verifier until it says RICE-VERIFIED

Run `verify.py <repo_root>`. Every `[A]` item must be PASS. For each line
under `RICE-GATE-A FAIL`, do exactly what its detail says, then re-run:

- `settings:*` / `rice.md` / `CLAUDE.md:import-line` / `project-state:exists`
  → re-run Step 2 / Step 3 (a `conflict` means a rice-owned value was
  hand-edited; report it, do not overwrite outside `rice-update`).
- `plugin:*` / `output-style:exists` → Step 0 (wrong or missing build).
- `git-deny:behaves` → run **exactly** `git branch --show-current` through
  Bash in this repo. It should be denied; under normal Claude Code
  execution the harness records that denial in the session transcript,
  and the verifier reads that record — your own statement that it was
  denied counts for nothing. If the command *runs*, the deny rules are not
  in effect; the verifier will keep failing; report that.

When the verifier prints `RICE-VERIFIED <build>`: commit and push the
rice-owned changes. Quote that line verbatim in your report. Nothing else
counts as verified.

## Step 5 — Hand off to Matt

`bootstrap_state.enter_waiting_for_matt(repo_root)`, then tell the OWNER:

> Rice's own setup is verified (`RICE-VERIFIED <build>`). The one step that
> needs you personally: run `/mattpocock-skills:setup-matt-pocock-skills`
> yourself — Claude cannot run it or simulate it. When you're done, say
> "bootstrap this repo with rice" again and I'll run the final verifier.

**Never report bootstrap as complete while `WAITING_FOR_MATT` exists.**

## Step 6 — Gate B: final verification (after the OWNER's Matt setup)

Run `verify.py <repo_root> --final`. The `[B]` items need things only the
OWNER/environment provide; the verifier reads their evidence:

- `matt:artifacts` → the OWNER ran Matt setup.
- `rtk` → the environment must provide it; Gate B fails until it does.
- `archive:behaves` → one real `/clear` with `RICE_SESSION_ARCHIVE=1` has
  produced an INDEX row and archive in this repo (or the switch is off).
- `premium-ask:behaves` → with the OWNER present, request a subagent with
  model `opus`; the OWNER declines the native prompt; the harness records
  it; the verifier reads it.

On `FINAL-VERIFIED <build>` the verifier itself has written
`.claude/rice-version` and removed `WAITING_FOR_MATT`. Commit and push;
quote the line verbatim. On `FINAL FAIL`, `rice-version` is untouched — say
which items the verifier listed, do what they say, re-run.

## Re-running this skill

Just run the verifier first. Its output tells you which step you are at.
