# RICE-ONE-COMMAND-UPDATE-001 — feasibility research and evidence

Status: research behind SPEC-001 I15 (amended), I27 and §17. Date:
2026-10-13. Real interactive Claude Code 2.1.296 sessions inside a Claude
Code on the web container, driven through tmux with an isolated
`CLAUDE_CONFIG_DIR` and a local git marketplace (scratch builds only; the
public repository and every release branch were never touched).

## Question

Can one OWNER-typed command, inside one running session, check the version,
update the plugin, load the new build, update the repo, and verify — without
asking anything and without ever reporting an update that did not happen?

## What the official documentation says (code.claude.com/docs)

| Fact | Where |
|---|---|
| `claude plugin marketplace update` refreshes a marketplace; `claude plugin update <plugin>@<marketplace>` installs "the latest version its marketplace offers"; unchanged computed version ⇒ "already at the latest version". | plugins/cli-reference, plugins/loading |
| A plugin's version is the manifest `version`, else the commit SHA. | plugins/loading |
| `/reload-plugins` applies pending plugin changes to the running session; where there is no terminal it runs "only when you type it into the session yourself". Hook commands keep the previous version's path until it runs. | plugins/cli-reference, plugins/loading |
| `--json` on `plugin update` prints one result line; exit 0 covers success and "already latest". | plugins/cli-reference |
| Old cache directories are kept 14 days. | plugins/loading |
| Cloud sessions: plugins from the repo's settings are not installed; the setup script runs before Claude starts and its result is snapshot-cached up to about 7 days; github.com is on the default allowlist. | cloud-environments |
| **Silent:** whether the model can run `/reload-plugins`; whether `claude plugin` runs from the Bash tool of a cloud session; whether a new cloud session gets the latest commit. | — |

## Probes (what the silences turned out to be)

| # | Probe | Observed |
|---|---|---|
| 1 | In a session on build A (0.1.8), the agent ran `claude plugin marketplace update rice-bootstrap` then `claude plugin update rice@rice-bootstrap`. | Both exit 0: "updated from 0.1.8 to 0.1.9 for scope user. Restart to apply changes." Cache now holds both version directories. |
| 2 | Next prompt in the same session. | The running session's UserPromptSubmit hook is still build A's (`BUILD-MARKER-A`). |
| 3 | The agent invoked the Skill tool with `reload-plugins`. | Refused: "reload-plugins is a built-in CLI command, not a skill. Ask the user to run /reload-plugins themselves — it cannot be invoked via the Skill tool." `rice:history` (`disable-model-invocation`) is refused the same way. |
| 4 | The OWNER typed `/reload-plugins` (tmux). | "Reloaded: 2 plugins · 30 skills · 6 agents · 6 hooks"; the next prompt ran build B's hook (`BUILD-MARKER-B`). |
| 5 | `claude plugin update --json`, `list --json`, `marketplace update --json`, failures. | One JSON line each; `updateOutcome` `up_to_date` when current; `failureCode: not_found` and exit 1 for an unknown plugin or marketplace; `list --json` carries `installPath` per plugin. |
| 6 | A newer release whose `plugin.json` is invalid JSON. | `plugin update` exits 0 and installs it under its **commit hash** as the "version" (`06f57ac0316d`); the build's own verifier then reports `build ?`. Without a guard that reads as "updated". |
| 7 | Whole flow, `/rice:update` typed in a session on A, repo finalized by A with unsaved work, B published meanwhile. | Plugin 0.1.8 → 0.1.9; the work saved (README, notes) as one commit; rice's own commit; pushed; `FINAL-VERIFIED 0.1.9`; clean tree; one conversation number and one code block; 0 questions. The bootstrap's git-deny probe round made the agent re-run the command once. |
| 8 | The re-run of probe 7. | The plugin was already installed, so its first line read "already at the latest release" — a report that hid the update. **Defect, fixed:** `b is installed (this session started on a)`. |
| 9 | After `/reload-plugins`, `/rice:update` again. | Build B's hook live; "already at the latest release (0.1.9)"; nothing to commit; `FINAL-VERIFIED 0.1.9`. |
| 10 | Marketplace host down. | Refresh fails; `plugin update` still answers from the local clone; the report says "could not check for a newer release (...); staying on the installed ..." — never "latest". |
| 11 | Broken release (probe 6) through the command, then a fixed release. | Without the guard the command ran the broken build and the engine blocked it with `build ?`. With the guard it stops: "declares no MAJOR.MINOR.PATCH version ... broken", repo untouched. Once a fixed release (0.1.10) is out the same command updated `06f57ac0316d → 0.1.10` and finalized. |

## Conclusion

Feasible in one session, with one limit proven impossible: the session's own
hooks and skill text cannot be reloaded by the model, and `/reload-plugins`
is the OWNER's. The design does not need it: the repo update runs from the
installed build's path, which the repo's bootstrap accepts (`plugin:build`),
so the OWNER types one command; the report says plainly that the session's
hooks load the new build after `/reload-plugins` or a new session.

Minimal alternative for the one impossible link: none needed beyond that
sentence; nothing else depends on the reload.

## Limits and NOT VERIFIED

- A rice installed before `/rice:update` exists has no such skill: the first
  update to a release carrying it is the by-hand way (README).
- Cloud: the update lasts the session; the next session starts from the
  Environment's setup script and snapshot, so run `/rice:update` there again.
- NOT VERIFIED: a brand-new Cloud session created from the web UI with an
  Environment setup script and snapshot reuse (this session's container is a
  Cloud container, but a second session cannot be created and driven from
  here); `claude plugin` against github.com as the marketplace source (the
  proxy path was exercised by the repo's own git remote, not by the
  marketplace); a no-`gh`, no-`claude` environment is a stop with the manual
  way, covered by unit tests only.
