---
name: rice-update
description: "The sole entrypoint for changing this repo's rice-bootstrap delivery version. OWNER-invoked only -- Claude cannot start this on its own, by design (see ticket #26 / RICE-BOOTSTRAP-016)."
disable-model-invocation: true
---

# rice-update

This is the one and only way a target repo's rice delivery version ever
changes. `disable-model-invocation: true` means the harness itself hides
this skill from the model and refuses any attempt to invoke it
programmatically — the same mechanism confirmed against
`setup-matt-pocock-skills` (RICE-BOOTSTRAP-014) and observed for this skill
in #18's probes (`rice:rice-update` never appears in a session's skill
list). Ordinary reconcile, bootstrap, and hooks never call this; only an
explicit OWNER-typed `/rice:rice-update` reaches it.

## Two versions, two meanings — never conflated

- **Delivery version** = `extraKnownMarketplaces.rice-bootstrap.source.ref`
  in `.claude/settings.json`. It tells the marketplace which release to
  fetch. It must change *before* the new build can load, so it says nothing
  about what this repo has been verified against.
- **Verified version** = the build whose rubric (`reconcile.check()`) last
  came back `MECHANICAL-VERIFIED rice <build>` with the BEHAVES items
  exercised, in this repo. It is never stored anywhere; it is re-derived
  every time the rubric runs, by the build that runs it. `plugin:build`
  (MATCHES) fails whenever the installed build isn't the one running the
  rubric, so an old build can never claim a new version.

The sequence below spans **two sessions** on purpose. Runtime facts behind
that (ticket #19, verified with real installs and `--debug` logs): a session
keeps running the cached plugin build until `claude plugin update` is run,
and even then the new build is only loaded by a *new* session. Reconciling
or verifying in the old session would run the OLD build's code against the
NEW version's desired state — a "PASS" from that would be a lie. So: move
the delivery version and the install, hand the OWNER a restart, and do
reconcile + rubric only in the session that actually runs the new build.

## Session 1 — move the delivery version and the install

1. **Detect the target release.** If the OWNER named a version, use it.
   Otherwise take the highest SemVer tag of `L26041040/rice-bootstrap`
   (`git ls-remote --tags https://github.com/L26041040/rice-bootstrap.git`).

2. **Confirm the target tag exists and is fetchable before touching
   anything.** If it can't be confirmed, **stop and report FAIL** — write
   nothing.

3. **Change the delivery version.** Set
   `extraKnownMarketplaces.rice-bootstrap.source.ref` to the confirmed tag.
   This is the only place in rice-bootstrap allowed to write that field —
   `reconcile.py` never does (ticket #23). Commit and push. From this moment
   the repo is **NOT VERIFIED** at any version until Session 2 finishes;
   say so.

4. **Update the installed build.** In order:
   ```
   claude plugin marketplace update rice-bootstrap
   claude plugin update rice@rice-bootstrap
   ```
   (`--scope project` if `claude plugin list` shows a project-scope
   install.) The CLI reports "updated from X to Y … Restart to apply
   changes". If either command fails, report FAIL and stop: the delivery
   version now points at the new release but nothing has been reconciled or
   verified, and this session cannot fix that — say exactly that. In Cloud,
   the environment setup script must also be moved to the new version or
   the next container will install the old one again — tell the OWNER.

5. **Stop and hand off.** Tell the OWNER plainly: delivery version is now
   `<tag>`, the install is updated, the repo is not yet verified, and they
   must **start a new session** and run `/rice:rice-update` again to
   finish. Do not run reconcile or check here.

## Session 2 — reconcile and verify on the new build

Detect this case by: `ref` already equals the target/latest tag AND
`claude plugin list` shows that version installed.

6. **Reconcile.** `reconcile.apply(repo_root, is_rice_update=True)`
   (`scripts/reconcile.py`). With `is_rice_update=True`, every rice-defined
   key and `.claude/rice.md` that drifted from the new build's desired
   state is overwritten wholesale — that is the entire point of the flag.

7. **PROJECT_STATE.md.** `project_state.preserve_or_restore(repo_root)`
   (`scripts/project_state.py`) — never `create_if_missing` here. Existing
   content is preserved byte-for-byte; only a missing file gets the empty
   template. Its content is never inspected or rewritten.

8. **Run the rubric, fix, re-run, until VERIFIED.** `reconcile.check()`:
   every EXISTS/MATCHES item PASS (`plugin:build` must show this session
   runs the new build — if it doesn't, you are still on the old build:
   stop, that is Session 1's step 4/5 not done), BEHAVES items exercised
   as in bootstrap Step 4 (`git branch --show-current` → denied;
   `premium-ask` → E2E/OWNER present; `archive` → evidence). On FAIL: fix
   what the detail names, re-run. Commit and push only when clean, and
   report the verdict line verbatim: `MECHANICAL-VERIFIED rice <build>`.
   Anything less is reported as **FAIL / NOT VERIFIED at <tag>**, never as
   "updated".

## What this deliberately does not do

No rollback, no migration framework, no git-history inference, no stored
"verified version" flag. A failed update is reported as failed; fixing it
forward (another explicit `/rice:rice-update`, or manual repair) is a
deliberate OWNER decision, not something this skill attempts automatically.
