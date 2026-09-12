# Changelog

All notable changes to the `rice` plugin. The top entry's version must equal
`.claude-plugin/plugin.json`'s `version` (a release check).

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
