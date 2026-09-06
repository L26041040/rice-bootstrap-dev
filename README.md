# rice-bootstrap

One-instruction bootstrap and update for common Claude Code project
configuration: git safety as native permission rules, a session archive with
a mechanical index, Sonnet as the subagent default, an OWNER-chosen output
style (the bundled 老弟 style recommended), the OWNER's soft working rules in
`.claude/rice.md`, and an OWNER-driven handoff to Matt Pocock's official
skill setup. One public repository is both the Claude Code
plugin marketplace (`rice-bootstrap`) and the plugin (`rice`).

# FOR AGENTS

This README is the discovery and installation contract. It is not a
verification authority: whether a repo is verified is decided only by the
plugin's deterministic verifier (`scripts/verify.py`), and the verified
version lives only in `.claude/rice-version`, written only by that verifier.

## Installation

Trigger: the user expresses install intent in any natural language —
"install rice-bootstrap", "幫我裝 rice-bootstrap", "set up rice-bootstrap",
"bootstrap this repo with rice" (when rice is not installed yet). Do not ask
the user to remember or type any command.

1. Canonical public repository: `L26041040/rice-bootstrap` on GitHub
   (marketplace name `rice-bootstrap`, plugin `rice`, plugin id
   `rice@rice-bootstrap`).
2. Use the official Claude Code plugin mechanism, non-interactive, touching
   only `~/.claude`:
   ```
   claude plugin marketplace add L26041040/rice-bootstrap
   claude plugin install rice@rice-bootstrap
   ```
3. A newly installed plugin is loaded by the **next** session. Tell the
   user to start a new session; that is the only handoff.
   - Local machine: nothing else.
   - Claude Code on the web: `~/.claude` is per-container, so an install
     run inside a session does not persist. Tell the user to add the two
     commands above where their environment provisions plugins at
     container start (the same place `mattpocock-skills` comes from), once
     per environment.
4. In the new session, run the bootstrap flow below.

## Bootstrap

Entrypoint: the `rice:bootstrap` skill (trigger: "bootstrap this repo with
rice" or equivalent). It decides nothing; it operates the verifier.

1. Verifier `python3 <plugin root>/scripts/verify.py <repo>`; if
   `plugin:installed` is FAIL → Installation above.
2. OWNER choices, asked once, written only if absent:
   - session archive on/off (`env.RICE_SESSION_ARCHIVE`);
   - output style: (1) `rice:老弟` — recommended; (2) Claude default (no
     `outputStyle` key); (3) an existing project/user/plugin style, listed
     from `scripts/output_styles.py` and verified to exist; (4) a custom
     style the agent writes to `.claude/output-styles/<name>.md`
     (project-owned; rice never rewrites it).
   - delivery `ref` = highest upstream tag (delivery mechanism only).
3. `reconcile.apply` (rice-owned settings leaves, `.claude/rice.md`, the
   `@.claude/rice.md` line in `CLAUDE.md`).
4. Gate A: run the verifier, fix exactly what a FAIL line says, re-run,
   until it prints `RICE-VERIFIED <build>`. Commit and push.
5. `WAITING_FOR_MATT`: ask the OWNER to run
   `/mattpocock-skills:setup-matt-pocock-skills` themselves. Never run or
   simulate it.
6. After the OWNER's Matt setup: `verify.py <repo> --final`. Only its
   `FINAL-VERIFIED <build>` line means done; that pass writes
   `.claude/rice-version` and clears `WAITING_FOR_MATT`.

## Update

Only on the OWNER's explicit request; entrypoint `/rice:rice-update`
(OWNER-invoked only by design).

- Latest stable comes from upstream release tags of `L26041040/rice-bootstrap`
  (`git ls-remote --tags`). The target repo never declares "latest".
- Session 1: confirm the target tag exists → move the delivery `ref` →
  `claude plugin marketplace update rice-bootstrap` →
  `claude plugin update rice@rice-bootstrap` → new session.
- Session 2: `reconcile.apply(is_rice_update=True)` → Gate A → Gate B.
- `.claude/rice-version` changes only when the verifier prints
  `FINAL-VERIFIED <build>`; on any failure it is byte-for-byte unchanged.
  The OWNER's output-style choice is preserved.

## Release / Development

Releases are cut from the private development repository by its
`rice-release` skill and `scripts/release.py`, on the OWNER's explicit
release intent only. The public repository is a verified export of an
allowlisted surface; it is never a mirror, never receives private history,
and is never a source of truth. Release checks (tests, manifests, version
consistency, this README's required sections, allowlist completeness,
forbidden-path absence, secret scan, standalone-package validation) are
mechanical: any FAIL → `RELEASE BLOCKED`, no tag, no publish.
