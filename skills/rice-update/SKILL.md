---
name: rice-update
description: "The sole entrypoint for changing this repo's installed rice-bootstrap version. OWNER-invoked only -- Claude cannot start this on its own, by design (see ticket #26 / RICE-BOOTSTRAP-016)."
disable-model-invocation: true
---

# rice-update

This is the one and only way a target repo's pinned rice version ever
changes. `disable-model-invocation: true` means the harness itself refuses
any attempt to invoke this skill programmatically or via ordinary reasoning
— the same mechanism confirmed against `setup-matt-pocock-skills` (see the
project's RICE-BOOTSTRAP-014 runtime probe). Ordinary reconcile, bootstrap,
and hooks never call this; only an explicit OWNER-typed command reaches it.

## Sequence

1. **Detect the target release.** If the OWNER named a specific version, use
   it. Otherwise find the latest stable tag of `L26041040/rice-bootstrap`
   (e.g. `git ls-remote --tags https://github.com/L26041040/rice-bootstrap.git`,
   highest SemVer).

2. **Confirm the target tag actually exists and is fetchable before touching
   anything.** If it can't be confirmed (unreachable, typo'd version, network
   failure), **stop here and report FAIL** — do not write `ref`. This is the
   whole safety net against "ref says vN but nothing was reconciled": by
   never writing `ref` until the new version is confirmed real, that bad
   state is structurally impossible without needing any rollback machinery.

3. **Change the pinned `ref`.** Read `.claude/settings.json`, set
   `extraKnownMarketplaces.rice-bootstrap.ref` to the confirmed target
   version, write the file. This is the only place in the whole rice-bootstrap
   codebase allowed to write that field — `reconcile.py`'s `apply`/`check`
   never do, by design (see ticket #23).

4. **Reconcile.** Call `reconcile.apply(repo_root, is_rice_update=True)`
   (`scripts/reconcile.py`). With `is_rice_update=True`, every rice-defined
   key and `.claude/rice.md` that has drifted from the new version's desired
   state gets overwritten (wholesale replace, not merged) — that is the
   entire point of this flag existing.

5. **PROJECT_STATE.md.** Call `project_state.preserve_or_restore(repo_root)`
   (`scripts/project_state.py`) — never `create_if_missing` here. Existing
   content is preserved byte-for-byte; only if the file is missing entirely
   does this restore the empty template. Its content is never inspected.

6. **Restart/reload.** Tell the OWNER a session restart may be needed for
   the new plugin version's code (skills/hooks) to actually take effect,
   the same way bootstrap's Step 1 does.

   NOT VERIFIED (ticket #19, blocked — see the project's own tracker): the
   exact Cloud ref-change/reload mechanics haven't been runtime-tested
   solo. State plainly what changed (`ref` is now X) and that a restart may
   be needed to run the new version's own code — do not claim more
   certainty than this.

7. **Verify.** Call `reconcile.check(repo_root)`. Report the result exactly
   as it comes back — PASS/FAIL/NOT VERIFIED per item, plus the one-line
   summary. If anything rice-owned is FAIL or conflict, the update as a
   whole is FAIL, plainly stated as such. Never report a version bump as
   successful when only part of it actually reconciled.

## What this deliberately does not do

No rollback, no migration framework, no git-history inference. A failed
update is reported as failed; fixing it forward (a further explicit
`rice-update`, or manual repair) is a deliberate follow-up action the
OWNER decides on, not something this skill attempts automatically.
