# rice-bootstrap

One-instruction bootstrap and update for common Claude Code project
configuration: git safety as native permission rules, a session archive with
a mechanical index, Sonnet as the subagent default, an OWNER-chosen output
style (the bundled 老弟 style recommended), the OWNER's soft working rules in
`.claude/rice.md` (imported by `CLAUDE.md` for Claude, pointed at from
`AGENTS.md` for every other coding agent), and an OWNER-driven handoff to
Matt Pocock's official skill setup. One public repository is both the Claude Code
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
rice" or equivalent). It decides nothing and holds no steps: it asks the
OWNER's two bounded choices and runs one command (RICE-BOOTSTRAP-044B).

```
python3 <plugin root>/scripts/rice.py bootstrap <repo> \
    [--archive 0|1] [--output-style <name>|none]
```

The lifecycle engine (`scripts/rice.py`) decides everything else:

1. Is rice loaded here? If not, nothing is written and the OWNER gets the
   install instructions above (per Environment in Cloud, per machine
   locally).
2. The delivery `ref` — upstream `main`'s exact commit SHA, read by the
   engine itself (delivery mechanism only; no git tag or GitHub release is
   used anywhere). The OWNER's answers — session archive
   (`env.RICE_SESSION_ARCHIVE`) and output style, including "Claude default"
   as an absent key and a custom project style the agent writes to
   `.claude/output-styles/<name>.md` — are written once, only if absent.
   `rice.py styles <repo>` lists what resolves.
3. Rice-owned state is reconciled: settings leaves, `.claude/rice.md`, the
   `@.claude/rice.md` line in `CLAUDE.md`, the rice pointer block in
   `AGENTS.md`. One policy source: the rules are written only in
   `.claude/rice.md`; `CLAUDE.md` imports them (a real `@` import for
   Claude Code) and `AGENTS.md` carries a marker-delimited instruction to
   read them (AGENTS.md readers such as Codex concatenate the file verbatim
   and have no import syntax). Project-owned content in both is never
   touched.
4. The canonical flow, in one place only: **verify → PASS: done; FAIL →
   deterministic repair → verify again → still FAIL: the agent, last**.
   Repair (`scripts/repair.py`) restores every rice-owned failure the
   verifier named from this build's canonical state and declares nothing;
   the verifier's second run is the word that counts, and only its
   `RICE-VERIFIED <build>` / `FINAL-VERIFIED <build>` lines mean verified.
5. What the OWNER must see comes back as a directive the skill presents
   verbatim: a terse Matt hand-off ending in exactly
   `/mattpocock-skills:setup-matt-pocock-skills`, a verdict to quote and
   push, an install to do, or the remaining blockers. A repo that already
   has Matt's setup is finalized inside the same invocation and never gets
   a hand-off.
6. The continuation is automatic: a `Stop` hook
   (`scripts/bootstrap_continue.py`) asks the same engine, at the end of
   every turn, whether there is anything to continue. It stays silent until
   Matt's setup has left something behind, then runs the same flow to the
   end. That pass writes `.claude/rice-version` and clears the pending
   marker. Matt's setup artifacts and RTK are required: without both there
   is no `FINAL-VERIFIED`. Matt artifacts are repaired only from the repo's
   own history (tracked at HEAD, missing from the working tree); anything
   never committed is reported as unresolved by name — never generated,
   never copied from a template — so an incomplete Matt setup is never left
   silently pending. A fresh bootstrap costs the OWNER exactly one
   `rice:bootstrap` invocation — rice never asks for a second one.

If the repo already had its own `/clear` archive hook before rice, the OWNER
removes that old hook/script before enabling rice's archive; an existing
`session-history/` can stay.

## Update

Only on the OWNER's explicit request; entrypoint `/rice:rice-update`
(OWNER-invoked only by design).

```
python3 <plugin root>/scripts/rice.py update <repo>
```

One command, run once; the engine decides the stage from mechanical facts
and returns a directive to present verbatim:

- Latest stable is upstream `L26041040/rice-bootstrap`'s `main` branch — its
  exact HEAD commit SHA, read by the engine (`git ls-remote`). No git tag or
  GitHub release is used anywhere; the target repo never declares "latest".
- If this repo is pinned elsewhere: the delivery `ref` moves to that SHA and
  the official install commands run (`--scope project` when the install is
  project-scoped). The OWNER is then told to commit and push, move the
  Cloud container-start install line to the same SHA, and start a new
  session — an update spans two sessions, because a session keeps its
  cached build until it restarts.
- In that new session: rice-owned state is converged onto the new build
  (project-owned content and the OWNER's output-style choice untouched),
  then the same canonical flow runs to the end.
- `.claude/rice-version` changes only when the verifier prints
  `FINAL-VERIFIED <build>`; on any failure it is byte-for-byte unchanged.

## Uninstall

Only on the OWNER's explicit, present request ("uninstall rice", "移除
rice-bootstrap", "把 rice 從這個 repo 拿掉"); vague discussion is not a
trigger. Entrypoint: the `rice:rice-uninstall` skill, which runs
`python3 <plugin root>/scripts/uninstall.py <repo>`.

- Target repo cleanup: exact subtraction of rice-owned state only
  (`.claude/rice.md`, the `@.claude/rice.md` line, the rice pointer block
  in `AGENTS.md`, `.claude/rice-version`,
  `WAITING_FOR_MATT`, the rice marketplace/plugin declaration, the Sonnet
  subagent default when still rice's value, `RICE_SESSION_ARCHIVE`, rice's
  own deny rules, `outputStyle` only when it is exactly `rice:老弟`).
- Kept: `session-history/`, custom output styles and any other
  `outputStyle`, `permissions.allow`/`ask`, project deny rules, other
  plugins/marketplaces, project-owned `AGENTS.md`/`CLAUDE.md` content,
  Matt artifacts, RTK, all repo files and history.
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
