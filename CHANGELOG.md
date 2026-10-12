# Changelog

All notable changes to the `rice` plugin. The top entry's version must equal
`.claude-plugin/plugin.json`'s `version` (a release check).

## [Unreleased]

First-bootstrap fixes (RICE-BOOTSTRAP-UX-FIX-001):

- **GitHub triage labels are created.** When Matt's setup chose GitHub
  Issues, the labels its triage vocabulary names (`needs-triage`,
  `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, or the
  OWNER's own names) are created on the repository if missing -- never twice
  -- and FINAL-VERIFIED waits until GitHub has them. No gh CLI is needed; a
  missing permission is one clear instruction, never a pretended pass.
  Other trackers are left alone.
- **Formal reports in one code block.** The bootstrap report, the summary
  after Matt's setup and the finalization now follow rice.md's numbering and
  single-block rules in the very session that bootstraps, before rice.md is
  loaded: the bootstrap command and every Stop continuation carry them, and
  while a bootstrap waits for Matt's setup each prompt does too (a new
  UserPromptSubmit hook, silent at any other time). Native choice prompts
  are unchanged.

## [0.1.7] - 2026-10-09

Branches that converge, a CLAUDE.md that stays clean, a session archive that
stays closed, and updates that are safe. Rice still has no update verb: the
platform updates the plugin, and re-running `/rice:bootstrap` in a new
session brings a repo up to date.

Git convergence, CLAUDE.md governance, session history (RICE-SPEC-001):

- **「合併回主枝」.** When the OWNER says it, the agent converges the session's
  branch onto the default branch in a fixed, provable order (fast-forward,
  push, prove it landed) and then removes the remote session branch with
  rice's privileged helper -- and only then. Nothing is merged or deleted on
  the agent's own judgement.
- **CLAUDE.md stays routing-only.** A plugin `PreToolUse` guard refuses any
  agent write to `CLAUDE.md` once the repo's bootstrap has finished; the
  agent is told to put the content in an ordinary project file instead.
  Rice's import line and Matt's block, written during bootstrap, need no
  exception.
- **`session-history/` is closed by default.** The agent cannot read the
  archive until the OWNER types `/rice:history`; that key covers the current
  `/clear` segment and closes at the next `/clear`.
- All three are plugin-level: a repo bootstrapped by an older build picks
  them up as soon as a session loads 0.1.7. No repo state is added.

Update safety (RICE-UPDATE-SPEC-001):

- **Build precondition.** `/rice:bootstrap` and the Stop continuation check
  `plugin:build` before their first repo write. A session that has not loaded
  the installed build (updated but not reloaded) stops, and nothing is written.
- **Anti-downgrade.** A new verifier item, `rice-version:not-newer` (Gate A),
  refuses a repo finalized by a newer rice: nothing is written and the version
  is never restamped backwards. A version that is not `MAJOR.MINOR.PATCH`
  blocks rather than being guessed at.
- **Staleness line.** When a repo was finalized by an older rice than the one
  running, the SessionStart context adds one line naming both versions and
  `/rice:bootstrap`. Otherwise nothing is added.
- **README.** Says how an update reaches a repo, and that a pinned Environment
  stays on its release until the pin is changed.

Upgrade compatibility (RICE-UPGRADE-MATRIX-001): every published release,
upgraded by re-running `/rice:bootstrap`, now converges to exactly a fresh
install, and uninstall leaves only the project.

- **No empty containers left by this build's migration.** Taking back the
  project-scope declaration 0.1.2/0.1.3 wrote now also takes back the
  `extraKnownMarketplaces` / `enabledPlugins` objects it empties. A repo
  that 0.1.4-0.1.6 already migrated keeps the empty objects they left: they
  do nothing, and nothing can prove they are rice's.
- **The subagent model is the project's.** `env.CLAUDE_CODE_SUBAGENT_MODEL`
  is never removed or rewritten by bootstrap, update, repair or uninstall,
  whatever its value -- uninstall no longer removes `"sonnet"`, as 0.1.2-0.1.6
  did. No release branch ever wrote it, and a project sets that value for
  its own reasons. A repo the pre-release 0.1.1 builds bootstrapped keeps the
  `"sonnet"` they wrote until the OWNER deletes it.
- **Re-running bootstrap is a no-op.** The release gate now checks that a
  second `/rice:bootstrap` on every upgraded and fresh repo changes no byte.
- **Narrower legacy identity.** Only the exact enablement those builds
  wrote (`true`, beside rice's own marketplace entry) is rice's; a project's
  `false`, or the enablement of a same-named marketplace that is not rice's,
  is left alone.

Updating saves your work first (RICE-UPDATE-PRESAVE-001):

- **An update commits and pushes first.** When `/rice:bootstrap` finds a
  repo set up by an older rice, it commits every change in the working tree
  (`git add -A`, `.gitignore` respected) and pushes the current branch,
  unpushed commits included, before writing anything of its own. Running it
  is the OWNER's go-ahead; nothing is asked. It happens only after the build
  and anti-downgrade checks pass. A first install, a same-version re-run and
  the Stop continuation save nothing.
- **A failed save stops the update.** If the commit or the push fails, or a
  merge, rebase, cherry-pick or revert is in progress, nothing of rice's is
  written; the work stays in the tree (with your own staging) or in the
  local commit, and re-running saves again. It pushes where git would
  (`pushRemote`, `pushDefault`, upstream, `origin`), never onto another
  branch name. A repo with no remote is not updated until one is added.
  Never a force push, rewrite or branch deletion.

What real sessions showed (RICE-FINAL-UX-REVIEW-001):

- **A re-run asks nothing.** `/rice:bootstrap` asks only the choices
  `rice.py questions` prints: both on a fresh repo, none on one rice already
  configured. An update used to re-ask both and silently drop the answers,
  since each is written only when absent.
- **When the rules load.** Both ways a bootstrap finishes now say that
  `.claude/rice.md` loads when a session starts: the current rules apply
  after `/clear` or in a new session.
- **The staleness line reaches the OWNER.** Claude Code now shows it at
  session start (a hook `systemMessage`); as context alone, no agent passed
  it on. The agent's copy marks `/rice:bootstrap` as the OWNER's to type.
- **Only rice's changes are committed.** Bootstrap and uninstall now tell
  the agent to stage `.claude/`, `CLAUDE.md`, `AGENTS.md` (and Matt's
  `docs/agents/`) by name, never `git add -A`, and to leave the OWNER's
  other work uncommitted. Told to commit "the changes in the working tree",
  an agent had pushed the OWNER's unrelated work-in-progress with rice's.
- **Update instructions.** The README names `/reload-plugins` and the two
  commands that update a machine; a session whose build is not the
  installed one is told `/reload-plugins` works as well as a new session.

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
