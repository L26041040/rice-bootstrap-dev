---
name: rice-update
description: "The sole entrypoint for moving this repo to a newer rice-bootstrap release. OWNER-invoked only -- Claude cannot start this on its own, by design (see ticket #26 / RICE-BOOTSTRAP-016)."
disable-model-invocation: true
---

# rice-update

`disable-model-invocation: true`: the harness hides this skill from the
model and refuses programmatic invocation (confirmed against
`setup-matt-pocock-skills`, RICE-BOOTSTRAP-014, and observed for this skill
in #18's probes). Only an explicit OWNER-typed `/rice:rice-update` reaches it.

This skill describes *how to operate* an update. It decides nothing. The
verifier (`scripts/verify.py`) is the only authority on PASS, and its Gate
B pass is the only writer of `.claude/rice-version`.

## Three things that are not the same

- **Upstream latest** — the highest SemVer tag of `L26041040/rice-bootstrap`.
  The target repo never declares "latest"; it only asks upstream.
- **Delivery mechanism** — `extraKnownMarketplaces.rice-bootstrap.source.ref`
  in `.claude/settings.json`. The platform fetches the build by it, so it
  must move *before* the new build can load. It is never a verified version.
- **Verified version** — `.claude/rice-version`. Written only by
  `verify.py --final` on a Gate B pass, with the build that ran the
  verifier. Left byte-for-byte untouched on any failure. Nothing else —
  not this skill, not the agent, not bootstrap — may write it.

An update is a two-session sequence (ticket #19: a session keeps the cached
build until `claude plugin update`, and only a new session loads the new
build). Verifying in the old session would run the old build's verifier
against the new desired state; `plugin:build` fails it by construction.

## Session 1 — move the delivery mechanism and the install

1. Read `.claude/rice-version` (current verified version, if any) and the
   upstream latest tag. If the OWNER named a version, use that instead of
   latest. If it equals the verified version and the OWNER did not ask for
   a reinstall, say so and stop.
2. Confirm the target tag exists and is fetchable. If not: report FAIL,
   write nothing.
3. Set the delivery `ref` to the target tag. Commit and push. From here the
   repo's verified version is still whatever `.claude/rice-version` says —
   say so plainly; do not describe the repo as "on" the new version.
4. Run `claude plugin marketplace update rice-bootstrap` then
   `claude plugin update rice@rice-bootstrap` (`--scope project` if
   `claude plugin list` shows a project-scope install). On failure: report
   FAIL and stop. In Cloud, tell the OWNER the container-start install line
   must move to the new tag too, or the next container installs the old one.
5. Stop: tell the OWNER to start a new session and run `/rice:rice-update`
   again.

## Session 2 — reconcile, then let the verifier decide

Detect this case by: delivery `ref` already at the target, and
`claude plugin list` showing that version installed.

6. `reconcile.apply(repo_root, is_rice_update=True)` — rice-defined leaves
   and `.claude/rice.md` that drifted from the new build's desired state
   are overwritten wholesale; project-owned content untouched. The OWNER's
   `outputStyle` choice (including an absent key = Claude default, and any
   custom `.claude/output-styles/*.md`) is never touched by an update.
7. `project_state.preserve_or_restore(repo_root)` — existing content
   byte-for-byte preserved; only a missing file gets the empty template.
8. **Gate A**: `verify.py <repo_root>`. Fix exactly what each listed item
   says (see bootstrap Step 4), re-run, until `RICE-VERIFIED <build>`.
   `plugin:build` failing means you are still on the old build — that is
   Session 1's step 4/5 not done; stop and say so.
9. **Gate B**: `verify.py <repo_root> --final`. On `FINAL-VERIFIED <build>`
   the verifier has written `.claude/rice-version`; commit and push and
   quote the line verbatim. On `FINAL FAIL`, `rice-version` still holds the
   previous version — report the listed items; nothing is "updated".

## What this deliberately does not do

No rollback, no migration framework, no git-history inference, no
transaction engine. A failed update is reported as failed with the
verifier's own list; fixing forward is the OWNER's call.
