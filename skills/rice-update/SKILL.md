---
name: rice-update
description: "The sole entrypoint for changing this repo's installed rice-bootstrap version. OWNER-invoked only -- Claude cannot start this on its own, by design (see ticket #26 / RICE-BOOTSTRAP-016)."
disable-model-invocation: true
---

# rice-update

This is the one and only way a target repo's pinned rice version ever
changes. `disable-model-invocation: true` means the harness itself hides
this skill from the model and refuses any attempt to invoke it
programmatically — the same mechanism confirmed against
`setup-matt-pocock-skills` (RICE-BOOTSTRAP-014) and observed again for this
skill in #18's probes (`rice:rice-update` never appears in a session's skill
list). Ordinary reconcile, bootstrap, and hooks never call this; only an
explicit OWNER-typed `/rice:rice-update` reaches it.

The sequence below spans **two sessions** on purpose. Runtime facts behind
that (ticket #19, verified with real installs and `--debug` logs): a session
keeps running the cached plugin build until `claude plugin update` is run,
and even then the new build is only loaded by a *new* session. Running this
skill's reconcile/check in the old session would execute the OLD plugin's
code against the NEW version's desired state — a "PASS" from that would be
meaningless. So: update the install first, hand the OWNER a restart, and
finish in the session that actually runs the new build.

## Session 1 — move the pin and the install

1. **Detect the target release.** If the OWNER named a version, use it.
   Otherwise take the highest SemVer tag of `L26041040/rice-bootstrap`
   (`git ls-remote --tags https://github.com/L26041040/rice-bootstrap.git`).

2. **Confirm the target tag exists and is fetchable before touching
   anything.** If it can't be confirmed, **stop and report FAIL** — write
   nothing. This is the whole safety net against "ref says vN but nothing
   was reconciled": `ref` is never written until the new version is
   confirmed real, so that state is structurally unreachable without any
   rollback machinery.

3. **Change the pinned `ref`.** In `.claude/settings.json`, set
   `extraKnownMarketplaces.rice-bootstrap.source.ref` to the confirmed tag.
   This is the only place in rice-bootstrap allowed to write that field —
   `reconcile.py`'s `apply`/`check` never do (ticket #23). Commit and push.

4. **Update the installed build.** Run, in order:
   ```
   claude plugin marketplace update rice-bootstrap
   claude plugin update rice@rice-bootstrap
   ```
   (`--scope project` if the plugin was installed at project scope — see
   `claude plugin list`.) The CLI reports "updated from X to Y ... Restart to
   apply changes". If either command fails, report FAIL and stop: `ref` now
   points at the new version but nothing has been reconciled, and this
   session cannot fix that — say exactly that.

5. **Stop and hand off.** Tell the OWNER plainly: `ref` is now `<tag>`, the
   install is updated, and they must **start a new session** and run
   `/rice:rice-update` again to finish. Do not run reconcile or check here.

## Session 2 — reconcile and verify on the new build

Detect this case by: `ref` in `.claude/settings.json` already equals the
target/latest tag AND `claude plugin list` shows that version installed.

6. **Reconcile.** `reconcile.apply(repo_root, is_rice_update=True)`
   (`scripts/reconcile.py`). With `is_rice_update=True`, every rice-defined
   key and `.claude/rice.md` that drifted from the new version's desired
   state is overwritten wholesale — that is the entire point of the flag.

7. **PROJECT_STATE.md.** `project_state.preserve_or_restore(repo_root)`
   (`scripts/project_state.py`) — never `create_if_missing` here. Existing
   content is preserved byte-for-byte; only a missing file gets the empty
   template. Its content is never inspected.

8. **Verify.** `reconcile.check(repo_root)`. Report the result exactly as
   returned — PASS/FAIL/NOT VERIFIED per item plus the one-line summary. If
   anything rice-owned is FAIL or conflict, the update as a whole is FAIL,
   stated plainly. Commit and push what step 6/7 changed only when check
   is PASS on every rice-owned item.

## What this deliberately does not do

No rollback, no migration framework, no git-history inference. A failed
update is reported as failed; fixing it forward (another explicit
`/rice:rice-update`, or manual repair) is a deliberate OWNER decision, not
something this skill attempts automatically.
