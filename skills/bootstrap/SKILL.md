---
name: bootstrap
description: "(rice plugin v0.1.1) One-instruction bootstrap for a new repo: installs rice's own configuration (git safety, session archiving, model delegation, output style, PROJECT_STATE.md), then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

Bootstrap this repo onto rice-bootstrap. This spans a session restart (Claude
Code only picks up a newly-declared plugin on a fresh session) and ends in an
OWNER-driven handoff to the official Matt Pocock setup skill, which rice must
never invoke or imitate itself (see `.claude/rice.md` once it's installed;
until then, this file is the whole policy).

Every step below is idempotent by construction (`reconcile.py`'s apply is
create-if-missing/no-op-if-correct/conflict-if-drifted) — re-running this
skill on a repo that's already partly or fully bootstrapped is always safe.
Re-derive where you are from actual repo state each time; do not assume this
is the first run.

## Step 1 — Declare the plugin (if not already declared)

Check `.claude/settings.json` in this repo. If it already declares
`extraKnownMarketplaces.rice-bootstrap` and `enabledPlugins["rice@rice-bootstrap"]`,
skip to Step 2.

Otherwise:

1. Find the latest stable release tag of the `rice-bootstrap` marketplace
   itself (`L26041040/rice-bootstrap` on GitHub) — e.g.
   `git ls-remote --tags https://github.com/L26041040/rice-bootstrap.git`,
   picking the highest SemVer tag (`vX.Y.Z`, ignore any pre-release suffix
   unless none exists yet, in which case say so and stop rather than
   guessing).
2. Ask the OWNER whether this bootstrap should turn session archiving on
   (only if their instruction didn't already say so explicitly). If their
   instruction already said "with session archive" or equivalent, use that
   without asking again.
3. Build the desired settings with `scripts/settings_template.py`'s
   `desired_settings(ref, archive_default)` (`ref` = the tag from step 1,
   `archive_default` = the OWNER's choice or the shipped release default if
   they didn't say) and write it to `.claude/settings.json`, merging with
   whatever's already there (never clobber unrelated keys a project may
   already have under `.claude/settings.json` that rice doesn't own).
4. Commit and push this change.
5. **Stop here and tell the OWNER plainly**: the plugin is now declared, but
   this session cannot use its skills/hooks yet — Claude Code only loads a
   newly-declared plugin on a fresh session. Ask them to start a new session
   (or `/clear` — NOT VERIFIED whether that alone is sufficient; if unsure,
   ask for a genuine restart) and re-run `/rice:bootstrap` there to continue.

   NOT VERIFIED (ticket #18, blocked — see the project's own tracker):
   the exact Cloud install/restart mechanics haven't been runtime-tested
   solo. Say what you actually did (declared the plugin, pushed) and what
   you need (a fresh session) — do not claim more certainty about the
   restart mechanics than this.

## Step 2 — Reconcile rice-owned state

Once the plugin is loaded (this skill is itself running from it), run:

```
python3 "<plugin root>/scripts/reconcile.py"  # see note below
```

There's no CLI entrypoint file yet — call `reconcile.apply(repo_root)` and
`reconcile.check(repo_root)` from Python directly (`import sys; sys.path.insert(0, ".../scripts"); import reconcile`), or via a one-line `python3 -c`. This
copies `.claude/rice.md` from the plugin's `resources/rice.md`, reconciles
every rice-defined `.claude/settings.json` key, and reports per-item
PASS/FAIL/conflict.

Do not pass `is_rice_update=True` here — this is bootstrap, not an update
transaction.

## Step 3 — CLAUDE.md integration

`reconcile.apply()` in Step 2 already handles this (the
`CLAUDE.md:import-line` item): creates `CLAUDE.md` with just the import line
if it didn't exist, or appends only that line if the file existed without
it. Never touch anything else in the file. Nothing further to do here beyond
confirming that item's result.

## Step 4 — PROJECT_STATE.md

Call `project_state.create_if_missing(repo_root)` (`scripts/project_state.py`).
Never call `preserve_or_restore` here — that's `rice-update`'s call, not
bootstrap's.

## Step 5 — Verify

Run `reconcile.check(repo_root)`. Every rice-owned item (settings keys,
`.claude/rice.md`, the `CLAUDE.md` import line) must report PASS. `matt:artifacts`
and `rtk` are expected to still be FAIL/whatever they are — they are not
rice's to fix and do not block this step. If anything rice-owned reports
FAIL or conflict, stop and report it plainly — do not proceed to Step 6
claiming things are fine.

## Step 6 — Hand off to Matt

Call `bootstrap_state.enter_waiting_for_matt(repo_root)` (`scripts/bootstrap_state.py`).

Then tell the OWNER, plainly:

> Rice's own setup is done and verified. The one remaining step needs you
> personally: please run `/mattpocock-skills:setup-matt-pocock-skills`
> yourself. Claude cannot run it for you or simulate its questions — the
> skill is intentionally OWNER-invoked only. Once you've completed it, let
> me know (or just run bootstrap again) and I'll verify it and finish up.

**Do not report bootstrap as "complete" while this marker is present.** A
false claim of completion is worse than an honest "waiting on you" — see
`.claude/rice.md`'s "Trust verification" principle. What happens after the
OWNER runs Matt setup is ticket #28's job (Matt-completion detection),
currently blocked on a runtime probe (#27) that itself needs the OWNER to
actually run Matt setup once — it cannot be resolved by Claude alone.

## Re-running this skill

If `bootstrap_state.is_waiting_for_matt(repo_root)` is already true, don't
redo Steps 1–5 — just re-check whether Matt's artifacts are now present
(`reconcile.check()`'s `matt:artifacts` item) and report status honestly:
still waiting, or ready to hand off to #28's verification once it exists.
