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

No git tag or GitHub release exists anywhere in this lifecycle
(RICE-BOOTSTRAP-030). Upstream version identity is read straight from
`L26041040/rice-bootstrap`'s `main` branch: its `.claude-plugin/plugin.json`
version and CHANGELOG top entry say *what* version main is, and main's
exact commit SHA (`git ls-remote https://github.com/L26041040/rice-bootstrap
main`) is *which build* to pin to — that SHA is the delivery `ref`, not a
tag name.

## Three things that are not the same

- **Upstream latest** — `L26041040/rice-bootstrap`'s `main` branch: read its
  `.claude-plugin/plugin.json` version and CHANGELOG date, and its exact
  HEAD commit SHA. The target repo never declares "latest"; it only asks
  upstream.
- **Delivery mechanism** — `extraKnownMarketplaces.rice-bootstrap.source.ref`
  in `.claude/settings.json`, pinned to that exact commit SHA (never a tag
  name, never a floating branch name). The platform fetches the build by
  it, so it must move *before* the new build can load. It is never a
  verified version.
- **Verified version** — `.claude/rice-version`. Written only by
  `verify.py --final` on a Gate B pass, with the build that ran the
  verifier. Left byte-for-byte untouched on any failure. Nothing else —
  not this skill, not the agent, not bootstrap — may write it.

An update is a two-session sequence (ticket #19: a session keeps the cached
build until `claude plugin update`, and only a new session loads the new
build). Verifying in the old session would run the old build's verifier
against the new desired state; `plugin:build` fails it by construction.

## Session 1 — move the delivery mechanism and the install

1. Read `.claude/rice-version` (current verified version, if any) and
   upstream `main`'s version + commit SHA (its `.claude-plugin/plugin.json`
   version, CHANGELOG top date, and `git ls-remote
   https://github.com/L26041040/rice-bootstrap main`). If the OWNER named a
   version, confirm it matches what main currently is — main is always the
   only thing upstream serves, there is no other ref to ask for a specific
   older version. If the version equals the verified version and the OWNER
   did not ask for a reinstall, say so and stop.
2. Confirm that commit SHA is fetchable. If not: report FAIL, write
   nothing.
3. Set the delivery `ref` to that exact commit SHA (never a tag name, never
   the floating branch name `main`). Commit and push. From here the repo's
   verified version is still whatever `.claude/rice-version` says — say so
   plainly; do not describe the repo as "on" the new version.
4. Run `claude plugin marketplace update rice-bootstrap` then
   `claude plugin update rice@rice-bootstrap` (`--scope project` if
   `claude plugin list` shows a project-scope install). On failure: report
   FAIL and stop. In Cloud, tell the OWNER the container-start install line
   must move to the new commit SHA too, or the next container installs the
   old one.
5. Stop: tell the OWNER to start a new session and run `/rice:rice-update`
   again.

## Session 2 — reconcile, then let the verifier decide

Detect this case by: delivery `ref` already at the target, and
`claude plugin list` showing that version installed.

6. `reconcile.apply(repo_root, is_rice_update=True)` — rice-defined leaves,
   `.claude/rice.md` and the rice pointer block in `AGENTS.md` that drifted
   from the new build's desired state are overwritten wholesale;
   project-owned content in `CLAUDE.md` / `AGENTS.md` (including Matt's
   `## Agent skills` section) untouched. The OWNER's `outputStyle` choice
   (including an absent key = Claude default, and any custom
   `.claude/output-styles/*.md`) is never touched by an update.
7. **Gate A**: `verify.py <repo_root>`. On a FAIL, run
   `repair.py <repo_root>` once (verify → deterministic repair → verify;
   see bootstrap Step 3), and only fix by hand what it still lists.
   `plugin:build` failing means you are still on the old build — that is
   Session 1's step 4/5 not done; stop and say so.
8. **Gate B**: `verify.py <repo_root> --final`, and on a FAIL
   `repair.py <repo_root> --final` once. On `FINAL-VERIFIED <build>` the
   verifier has written `.claude/rice-version`; commit and push and quote
   the line verbatim. On `FINAL FAIL`, `rice-version` still holds the
   previous version — report the listed items; nothing is "updated".

## What this deliberately does not do

No rollback, no migration framework, no git-history inference, no
transaction engine. A failed update is reported as failed with the
verifier's own list; fixing forward is the OWNER's call.
