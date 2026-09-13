# Changelog

All notable changes to the `rice` plugin. The top entry's version must equal
`.claude-plugin/plugin.json`'s `version` (a release check).

## [0.1.6] - 2026-09-13

A privileged channel for the one git write rice denies the agent.

- **`scripts/delete_remote_branch.py`** deletes one branch on `origin` when
  the OWNER explicitly asks: `python3 <plugin root>/scripts/delete_remote_branch.py
  <repo_root> <branch>`. Refuses invalid names, origin's default branch,
  `main` and `master`; never forces; never touches a local branch (the
  checked-out branch's remote ref may go); an already-absent branch is a
  success; origin is re-read afterwards and the branch must be gone. The
  `permissions.deny` rules on `git branch` / `git push --delete` / `git push
  -d` are unchanged -- the agent runs the helper, the helper runs git.
- **`scripts/session_context.py`**, a plugin-level `SessionStart` hook: in a
  repo rice has bootstrapped, one line of context naming that helper by the
  path of the build actually loaded. Silent everywhere else. No skill, no
  project setting, no re-bootstrap: an old repo learns the channel the moment
  a session loads 0.1.6.

## [0.1.5] - 2026-09-13

Documentation only; no runtime, architecture or test-logic change.

- **README** cut to what a user does: requirements, the two install
  commands, `/rice:bootstrap`, `/rice:rice-uninstall`, what rice configures,
  license. Architecture history, gate explanations, ownership and release
  internals are gone from it.
- **Skills** (`bootstrap`, `rice-uninstall`) cut to their invariants and
  commands; rationale and ticket history removed. Every MUST / NEVER kept.
- Stale code comments that still described a repo-recorded release ref, a
  project-scope distribution or an update lifecycle were corrected.

## [0.1.4] - 2026-09-13

User-scope Rice, and nothing declared twice.

- **No more project-scope plugin declaration.** Through 0.1.3, bootstrap
  also wrote `extraKnownMarketplaces.rice-bootstrap` and
  `enabledPlugins.rice@rice-bootstrap` into the target repo's own
  `.claude/settings.json`, pinned to the release that configured it. Rice's
  distribution is user scope only -- the Environment installs it once, the
  same way Matt Pocock's plugin is installed -- and a repo also declaring
  the plugin risked Claude Code reading it as a second, unsupported
  install, exactly what `plugin:scope` already exists to fail. Nothing
  writes those keys anymore.
- **`/rice:bootstrap` only confirms a usable install exists** (the
  canonical, user-scope one) before reconciling a repo; it never installs,
  enables, or pins the plugin itself.
- **Migration.** A repo a 0.1.3-era bootstrap already left with that
  project-scope declaration has it removed automatically -- on the next
  `/rice:bootstrap` (identified narrowly enough that a project's own
  unrelated marketplace or plugin entry is never touched) and on
  `/rice:rice-uninstall` either way.
- `/rice:rice-uninstall` still never touches the user-level plugin or
  marketplace install; only this repo's own configuration.

## [0.1.3] - 2026-09-12

The OWNER-only setup, written down where it is read first.

- **`README.md` opens with `OWNER-ONLY: Claude Cloud Environment Setup`.**
  Only the OWNER can do that step, in the Claude Code Web UI, on the
  Environment itself -- no rice command and no Claude session can stand in
  for it. It is done once per Environment; every container started from it
  afterwards comes up with RTK, Matt Pocock's skills and rice already
  installed, and each new container picks up the newest published release on
  its own, because the install line names no version.
- **The canonical setup script is in the README verbatim** -- RTK (install,
  `PATH`, `rtk init -g --auto-patch`), Matt Pocock's skills marketplace and
  plugin, then rice from public `main`. The earlier claim that Matt's plugin
  was already provisioned elsewhere is gone: the script installs it. Matt's
  repo-level `/mattpocock-skills:setup-matt-pocock-skills` is still a
  separate OWNER step.
- **Repo usage comes next, as the two lifecycles it is**: `/rice:bootstrap`
  to configure this repo, `/rice:rice-uninstall` to remove rice from it.
  Pinning `@release/<semver>` keeps its own section, moved below them: it is
  the exception, not the way in.
- The release check now caps only the README's lead paragraph, not
  everything above `# FOR AGENTS`, and requires the OWNER-only heading.

## [0.1.2] - 2026-09-12

Two lifecycles, and releases that never move.

- **No update system.** `rice:rice-update` and every part of it are gone:
  no upstream-latest detection, no ref migration, no restart-required flow,
  no plugin-update orchestration. Rice offers exactly two repo lifecycles,
  `rice:bootstrap` and `rice:rice-uninstall`, and neither ever asks the
  OWNER to restart a session or reason about which build is latest.
- **Immutable release branches.** A release is the public branch
  `release/<semver>`, created once and never moved or overwritten;
  publishing a version whose branch already exists FAILS CLOSED. A repo
  pins that branch as its delivery ref, read from the build itself.
- The subagent model pin (`env.CLAUDE_CODE_SUBAGENT_MODEL`) stays retired:
  a working rule in `.claude/rice.md`, not a setting. Repos bootstrapped
  when rice did write it still get it removed on uninstall.
- `permissions.deny` is owned as a multiset: rice adds one occurrence of
  each rule it declares and takes back exactly that one, so a repo's own
  identical rule survives install and uninstall.
- One ownership declaration (`scripts/reconcile.py`) drives install,
  repair and uninstall; environment facts live in `scripts/evidence.py`;
  `scripts/verify.py` remains the only authority on PASS/FAIL.

## [0.1.1] - 2026-09-07

First release candidate.

- One-instruction bootstrap (`rice:bootstrap`) with OWNER choices for the
  session archive and the output style (`rice:老弟` recommended, Claude
  default, an existing style, or a custom project style).
- Deterministic verifier (`scripts/verify.py`): Gate A `RICE-VERIFIED`,
  Gate B `FINAL-VERIFIED`; sole writer of `.claude/rice-version`.
- Git safety as native permission rules; a Sonnet-first subagent working rule (the
  OWNER names another model out loud when wanted); `.claude/rice.md` carrying
  only the OWNER's soft working rules.
- Session archive on `/clear` with a mechanical `session-history/INDEX.md`.
- Matt Pocock official setup artifacts and RTK are required for
  `FINAL-VERIFIED` (v1 is the OWNER's actual workflow).
- OWNER-triggered `rice:rice-uninstall`: exact subtraction of rice-owned
  state from a repo, mechanically checked (`RICE-UNINSTALLED`); the user-level
  plugin is never removed automatically.
