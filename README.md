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
3. **Local machine**: a newly installed plugin is loaded by the **next**
   session. Start one and run the bootstrap flow below.
4. **Claude Code on the web**: do not run the two commands inside a session
   expecting them to persist — `~/.claude` is per-container. See "Claude
   Code Web setup" below; that is the primary path, not a same-session
   install.

## Claude Code Web setup (Cloud Environment)

Three layers — worth keeping straight so an install fix lands in the right
one:

1. **Cloud Environment** — the container template a Claude Code Web session
   starts from. Anything a session needs *before it starts* belongs in the
   Environment's setup script. Creating or editing an Environment is a
   one-time OWNER action in the Claude Code Web UI; rice cannot create or
   edit one itself.
2. **rice plugin** — installed once into the Environment's setup script
   (the same two commands from "Installation" above). Every fresh session
   started from that Environment already has rice loaded automatically —
   no per-session install, no `/reload-plugins`.
3. **Repo bootstrap** (`/rice:bootstrap`, see "Bootstrap" below) — configures
   the *current repo only*. It never (re)installs rice itself; that is the
   Environment's job, done once.

Recommended: create a dedicated rice Cloud Environment, or add the two
install commands to an existing dedicated Environment's setup script,
alongside whatever else it already provisions (e.g. `mattpocock-skills`,
RTK). Matt Pocock skills are typically already provided by current
Cloud/platform provisioning — rice's Environment setup does not duplicate
installing Matt; Matt's own **repo-level** setup
(`/mattpocock-skills:setup-matt-pocock-skills`) stays a separate, OWNER-only
step (see "Bootstrap" below).

If a session's Environment genuinely was not set up this way (rice isn't
loaded), the one-off recovery is: run the two commands inside the current
session, then start a new one — but that is a fallback, not the normal
Cloud flow, and does not persist to the next container on its own.

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
   - delivery `ref` = upstream `main`'s exact commit SHA (delivery
     mechanism only — no git tag or GitHub release is used).
3. `reconcile.apply` (rice-owned settings leaves, `.claude/rice.md`, the
   `@.claude/rice.md` line in `CLAUDE.md`).
4. Gate A: run the verifier, fix exactly what a FAIL line says, re-run,
   until it prints `RICE-VERIFIED <build>`. Commit and push.
5. `WAITING_FOR_MATT`: ask the OWNER to run
   `/mattpocock-skills:setup-matt-pocock-skills` themselves. Never run or
   simulate it.
6. After the OWNER's Matt setup: `verify.py <repo> --final`. Only its
   `FINAL-VERIFIED <build>` line means done; that pass writes
   `.claude/rice-version` and clears `WAITING_FOR_MATT`. Matt's setup
   artifacts and RTK are required: without both there is no `FINAL-VERIFIED`.

If the repo already had its own `/clear` archive hook before rice, the OWNER
removes that old hook/script before enabling rice's archive; an existing
`session-history/` can stay.

## Update

Only on the OWNER's explicit request; entrypoint `/rice:rice-update`
(OWNER-invoked only by design).

- Latest stable comes from upstream `L26041040/rice-bootstrap`'s `main`
  branch — its `.claude-plugin/plugin.json` version, CHANGELOG date, and
  exact HEAD commit SHA (`git ls-remote .../rice-bootstrap main`). No git
  tag or GitHub release is used anywhere; the target repo never declares
  "latest".
- Session 1: confirm the target commit exists → move the delivery `ref`
  to that exact SHA →
  `claude plugin marketplace update rice-bootstrap` →
  `claude plugin update rice@rice-bootstrap` → new session.
- Session 2: `reconcile.apply(is_rice_update=True)` → Gate A → Gate B.
- `.claude/rice-version` changes only when the verifier prints
  `FINAL-VERIFIED <build>`; on any failure it is byte-for-byte unchanged.
  The OWNER's output-style choice is preserved.

## Uninstall

Only on the OWNER's explicit, present request ("uninstall rice", "移除
rice-bootstrap", "把 rice 從這個 repo 拿掉"); vague discussion is not a
trigger. Entrypoint: the `rice:rice-uninstall` skill, which runs
`python3 <plugin root>/scripts/uninstall.py <repo>`.

- Target repo cleanup: exact subtraction of rice-owned state only
  (`.claude/rice.md`, the `@.claude/rice.md` line, `.claude/rice-version`,
  `WAITING_FOR_MATT`, the rice marketplace/plugin declaration, the Sonnet
  subagent default when still rice's value, `RICE_SESSION_ARCHIVE`, rice's
  own deny rules, `outputStyle` only when it is exactly `rice:老弟`).
- Kept: `session-history/`, custom output styles and any other
  `outputStyle`, `permissions.allow`/`ask`, project deny rules, other
  plugins/marketplaces, Matt artifacts, RTK, all repo files and history.
- Only the script's `RICE-UNINSTALLED` line means the repo is clean. Plain
  repo-scoped intent stops here — no follow-up question about the
  user-level plugin/marketplace; other repos in the same environment may
  still use them.
- Removing the user-level `rice@rice-bootstrap` plugin/marketplace only
  happens on a separately explicit OWNER request to remove rice globally,
  using the official `claude plugin uninstall`/`claude plugin marketplace
  remove` commands directly; it is never triggered by a repo-scoped
  uninstall and never automatic.

## Release / Development

Releases are cut from the private development repository by its
`rice-release` skill and `scripts/release.py`, on the OWNER's explicit
release intent only. The public repository is a verified export of an
allowlisted surface; it is never a mirror, never receives private history,
and is never a source of truth. Release checks (tests, manifests, version
consistency, this README's required sections, allowlist completeness,
forbidden-path absence, secret scan, standalone-package validation) are
mechanical: any FAIL → `RELEASE BLOCKED`, no publish. No git tag or GitHub
release is ever created, private or public; release identity is
`plugin.json` version + CHANGELOG + the exact commit SHA.
