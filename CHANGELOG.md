# Changelog

All notable changes to the `rice` plugin. The top entry's version must equal
`.claude-plugin/plugin.json`'s `version` (a release check).

## [0.1.1] - unreleased

First release candidate.

- One-instruction bootstrap (`rice:bootstrap`) with OWNER choices for the
  session archive and the output style (`rice:老弟` recommended, Claude
  default, an existing style, or a custom project style).
- OWNER-only `rice:rice-update` (`disable-model-invocation`), a two-session
  update that never claims a version the verifier did not stamp.
- Deterministic verifier (`scripts/verify.py`): Gate A `RICE-VERIFIED`,
  Gate B `FINAL-VERIFIED`; sole writer of `.claude/rice-version`.
- Git safety as native permission rules; Sonnet as the subagent default (the
  OWNER names another model out loud when wanted); `.claude/rice.md` carrying
  only the OWNER's soft working rules.
- Session archive on `/clear` with a mechanical `session-history/INDEX.md`.
