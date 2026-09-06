# rice-bootstrap

One-instruction bootstrap and update for common Claude Code project
configuration: git safety (native permission rules), session archiving with
a mechanical index, Sonnet-by-default with an OWNER-approved premium path,
the 老弟 output style, `PROJECT_STATE.md`, and an OWNER-driven handoff to
Matt Pocock's official skill setup. Ships as one public repository that is
both a Claude Code plugin marketplace (`rice-bootstrap`) and the plugin
(`rice`).

This README is the discovery and installation contract. It is **not** a
verification authority: whether a repo is verified is decided only by the
plugin's deterministic verifier (`scripts/verify.py`), and the verified
version lives only in `.claude/rice-version`, written only by that verifier.

## Install

Tell Claude:

> 幫我安裝 rice-bootstrap

Claude should find this repository and run the official Claude Code plugin
install, which touches only `~/.claude`:

```
claude plugin marketplace add L26041040/rice-bootstrap
claude plugin install rice@rice-bootstrap
```

Then start a **new session** — a newly installed plugin is loaded by the
next session, not the current one.

- **Local machine**: that is all.
- **Claude Code on the web**: `~/.claude` is per-container, so an install
  run inside a session does not survive. Put the two commands above where
  your environment provisions plugins at container start (the same place
  `mattpocock-skills` comes from). Once per environment.

## Bootstrap a repo

In the repo, tell Claude:

> bootstrap this repo with rice

The `rice:bootstrap` skill reconciles rice-owned settings and files, runs
the verifier (`RICE-VERIFIED <build>`), asks you to run
`/mattpocock-skills:setup-matt-pocock-skills` yourself, then runs the final
verifier (`FINAL-VERIFIED <build>`), which writes `.claude/rice-version`.
Anything short of those two lines, printed by the verifier, is not
verified.

## Update a repo

Only you can start this: `/rice:rice-update`. It moves the delivery `ref`,
updates the installed build, asks for a new session, reconciles, and runs
the verifier again. `.claude/rice-version` changes only on a `FINAL-VERIFIED`.

## What rice owns in your repo

`.claude/rice.md`, a fixed set of leaves in `.claude/settings.json`, the
single `@.claude/rice.md` line in `CLAUDE.md`, and `.claude/rice-version`.
Everything else — including `PROJECT_STATE.md`'s content, `permissions.allow`,
other plugins, and all of Matt's artifacts — is yours.

## Session archive

With `env.RICE_SESSION_ARCHIVE` = `"1"`, every `/clear` archives the
session's dialogue to `session-history/<stamp>_<id>.md` and appends one row
to `session-history/INDEX.md`, commits both, and attempts a push. `/clear`
always completes; a failed persist is reported, never hidden.
