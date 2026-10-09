# RICE-SPEC-001 — Git convergence, CLAUDE.md governance, session-history gate

Status: canonical, frozen. Source: RICE-ARCH-REVIEW-001/-002/-003 and the OWNER's
final decisions (amended by RICE-TO-TICKET-002: the CLAUDE.md guard activates on
bootstrap completion instead of carrying a Matt exception; patched by
RICE-UPDATE-SPEC-001: update safety, §14, I15–I19, from RICE-UPDATE-RESEARCH-001
and RICE-UPDATE-GRILL-001; and by RICE-UPGRADE-MATRIX-001 / RICE-UPGRADE-PATH-001:
upgrade compatibility, §15, I20–I23; corrected by RICE-OWNERSHIP-FIX-001; and by
RICE-FINAL-UX-REVIEW-001: I15, I18 and §14.4–14.5 as real sessions showed
them, evidence in `docs/research/RICE-FINAL-UX-REVIEW-001-real-sessions.md`;
and by RICE-UPDATE-PRESAVE-001: an update saves the OWNER's work first, I24
and §14.8). This document freezes an approved design; it does not reopen it.
Base: rice 0.1.6 (private main `4e7f210`).

Keywords MUST / MUST NOT / MAY are normative.

---

## Problem Statement

Three things keep going wrong for the OWNER in repos rice has bootstrapped:

1. **Branches pile up.** Every Cloud session works on a platform-created
   `claude/<slug>` branch. Rice denies the agent the git commands that would
   finish such a branch, so the work is never converged onto `main` and the
   branch is never removed — or the agent improvises around the deny.
2. **CLAUDE.md turns into a trash can.** Agents append status reports,
   decisions and hand-off notes to `CLAUDE.md`. Everything in that file is
   loaded into every session, so the repo's always-on context grows with every
   bit of working state anyone jotted down.
3. **History is either always in the way or unreachable.** `session-history/`
   is a cold archive of past sessions. An agent that greps the repo pulls old
   conversations into context; an OWNER who actually needs the archaeology has
   no sanctioned way to let the agent at it.

## Solution

From the OWNER's point of view:

1. Work on whatever branch the platform gave the session. When the OWNER says
   **「合併回主枝」**, the agent converges that branch onto the default branch in a
   fixed, provable order and removes the remote temporary branch — and only then.
   Nothing is ever merged or deleted on the agent's own judgement.
2. `CLAUDE.md` stays routing-only forever. An agent that tries to write anything
   else there is mechanically refused and told to put the content in an
   ordinary project file that is not loaded automatically. The OWNER is never
   prompted. The guard switches on only once a repo's bootstrap has fully
   completed (`FINAL-VERIFIED`), so Rice's import line and Matt Pocock's
   `## Agent skills` block — both written during bootstrap — need no exception.
3. `session-history/` is closed to the agent by default. The OWNER invokes the
   **History Skill** (`/rice:history`) to hand the agent the key for the current
   `/clear` segment; the agent then decides for itself what to search and read.
   The key closes itself at the next `/clear`.

All of it ships inside the rice plugin: one PreToolUse guard, one thin skill, a
few lines in `rice.md`. No repo state is added, so bootstrap, repair, verify and
uninstall are unchanged, and repos bootstrapped by older builds pick the
behaviour up the moment a session loads the new build.

---

## User Stories

1. As the OWNER, I want platform-created session branches and worktrees to be legal, so that Cloud and worktree sessions work without fighting rice.
2. As the OWNER, I want the agent never to invent an additional branch, so that a repo has exactly the branches the platform and I created.
3. As the OWNER, I want a session branch to count as finished only when I say 「合併回主枝」, so that nothing is merged behind my back.
4. As the OWNER, I want convergence to run in one fixed order inside the same session, so that I can predict exactly what happens when I ask for it.
5. As the OWNER, I want the agent to prove the default branch contains the session's work before any branch is deleted, so that work can never be lost to a cleanup.
6. As the OWNER, I want a non-fast-forward or conflicting merge to stop everything and be reported, so that I resolve conflicts, not the agent.
7. As the OWNER, I want PR-based merges to rely on GitHub deleting the head branch, so that rice does nothing GitHub already does.
8. As the OWNER, I want the existing remote-branch helper used for the non-PR path, so that no new cleanup tooling appears.
9. As the OWNER, I want no timer, scanner, pending-task store or background cleanup, so that rice never acts when I am not asking.
10. As the OWNER, I want the destructive git deny rules unchanged, so that force-pushes and history rewrites stay impossible for the agent.
11. As the OWNER, I want `CLAUDE.md` to stay routing-only, so that every session starts with a small, stable context.
12. As the OWNER, I want an agent's attempt to write status into `CLAUDE.md` refused mechanically, so that the rule does not depend on the agent remembering it.
13. As the OWNER, I want that refusal to tell the agent where to put the content instead, so that my task still gets done without me.
14. As the OWNER, I want never to be prompted about `CLAUDE.md` writes, so that the guard costs me nothing.
15. As the OWNER, I want the agent to choose the destination file itself, so that there is no single permanent dumping-ground file.
16. As the OWNER, I want the agent never to be steered toward an always-loaded destination, so that the bloat does not simply move.
17. As the OWNER, I want Matt Pocock's setup to keep writing its routing block during bootstrap, so that my workflow is untouched.
18. As the OWNER, I want the guard to switch on only after bootstrap has fully completed, so that no exception parser is ever needed and no new state is added.
19. As the OWNER, I want rice to leave Claude's Auto Memory entirely alone, so that there is one memory system, Anthropic's.
20. As the OWNER, I want the `/clear` archive to keep working exactly as today, so that nothing I rely on changes.
21. As the OWNER, I want the agent not to read `session-history/` on its own, so that cold evidence stays out of live context.
22. As the OWNER, I want a single command that opens history access for this `/clear` segment, so that archaeology is one invocation, not a negotiation.
23. As the OWNER, I want to give no keywords and approve no individual reads once I have invoked the skill, so that the agent does the digging.
24. As the OWNER, I want the key to close at the next `/clear`, so that history access never stays open by accident.
25. As the OWNER, I want no search API, index, ranking or summary engine, so that rice does not grow a product around my archive.
26. As the OWNER, I accept that a repo-wide search may show a few matching history lines, so that rice does not add machinery to chase them.
27. As the OWNER, I want repos bootstrapped by older rice builds to gain all of this by loading the new build, so that no repo needs re-bootstrapping or migration.
28. As the OWNER, I want `/rice:rice-uninstall` to remain an exact subtraction with nothing new to subtract, so that leaving rice is as clean as before.
29. As an agent, I want the refusal reason to name what to do next, so that I can finish the task without asking.
30. As an agent, I want the guard to stay silent in repos rice has not bootstrapped, so that rice never governs a repo it was not asked into.
31. As an agent, I want a guard failure never to block unrelated work, so that a bug in rice does not stall a session.
32. As the OWNER, I want the hook's words to be the only place these rules are decided, so that skills and prose carry no second copy of the logic.

---

## Glossary (terms used normatively below)

- **default branch** — the branch `origin/HEAD` points at (the same read
  `delete_remote_branch.py` already makes). Falls back to the local
  `refs/remotes/origin/HEAD` symref. Unknown ⇒ convergence refuses.
- **session branch** — the branch checked out when the OWNER asks for
  convergence. It MUST NOT be the default branch.
- **convergence** — the fixed sequence in §2 that lands a session branch on the
  default branch and removes its remote ref. Triggered by the OWNER only.
- **routing content** — in `CLAUDE.md`: rice's `@.claude/rice.md` import line
  and Matt's `## Agent skills` block. Nothing else.
- **bootstrap completion** — `.claude/rice-version` exists in the repo. Only
  `verify.py` writes it, only on a Gate B pass (`FINAL-VERIFIED`, which requires
  Matt's artifacts); only `/rice:rice-uninstall` removes it. It is the existing,
  sole evidence that a repo's initialisation — including Matt's write to
  `CLAUDE.md` — is over.
- **working state** — status, progress, reports, decisions, hand-off notes,
  working memory. Never routing content.
- **always-loaded destination** — any file Claude Code loads into every
  session: `CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md`, files reached by
  `@import`, `.claude/rules/*.md` without `paths:` frontmatter, auto memory's
  `MEMORY.md`, `AGENTS.md` when it is the project instruction file.
- **the guard** — the single plugin-level PreToolUse hook introduced by this
  spec.
- **History Skill** — the plugin skill `history`, invoked as `/rice:history`.
  A capability key; it does nothing but open the gate.
- **/clear segment** — the records of the current transcript after the last
  genuine `/clear` boundary, as `archive_hook.clear_boundary_ends` already
  defines it (a user message whose content is the `/clear` command string;
  tool results that merely mention `/clear` are not boundaries).
- **rice repo** — a repo where `reconcile.rice_installed_here(cwd)` is true.

---

## 1. Invariants

- I1 Convergence MUST be triggered only by the OWNER's explicit request to merge
  back to the default branch. Rice MUST NOT infer completion from any signal.
- I2 Convergence MUST follow the order in §2 and MUST stop at the first failing
  step without deleting anything.
- I3 Rice MUST NOT add a timer, scheduler, poller, pending-cleanup store, stale-
  branch scanner or background cleanup of any kind.
- I4 The 28 existing `permissions.deny` entries MUST stay byte-identical. The
  verifier's `git-deny:behaves` probe (`git branch --show-current`) MUST stay.
- I5 `CLAUDE.md` in a rice repo MUST receive only routing content from bootstrap
  completion on. Pre-existing project content is project-owned and MUST NOT be
  rewritten by rice.
- I6 After bootstrap completion, any Edit / Write / NotebookEdit targeting a
  rice repo's `CLAUDE.md` MUST be denied by the guard with a reason. Before
  completion the guard MUST stay silent on `CLAUDE.md`, so bootstrap and Matt's
  setup write it exactly as today. There is no actor- or content-based
  exception. Rice MUST NOT ask the OWNER, MUST NOT use `updatedInput`, and MUST
  NOT add a `permissions.ask` or `permissions.deny` rule for this.
- I7 The deny reason MUST tell the model to put working state in an ordinary
  project file that is not loaded automatically, MUST list the always-loaded
  destinations as forbidden, and MUST NOT name a specific destination file.
- I8 Rice MUST NOT read, write, sync, enable or disable Claude's Auto Memory.
- I9 The `/clear` archive lifecycle (`SessionEnd(clear)` archive, local commit,
  `SessionStart(clear)` safe sync) MUST stay unchanged. No other `SessionEnd`
  reason MAY archive.
- I10 Reads that explicitly target `session-history/**` MUST be denied by the
  guard unless the History Skill was invoked in the current `/clear` segment.
- I11 The History Skill MUST be the only key. Once invoked, the guard MUST allow
  targeted history reads for the rest of the segment without any further OWNER
  approval. The key MUST close at the next `/clear`.
- I12 Rice MUST NOT add repo-level state for any of this: no new settings keys,
  no marker files, no Gate A/B items. Everything lives in the plugin. (§14
  adds exactly one verifier item, `rice-version:not-newer`, and no state; §15
  adds none.)
- I13 The guard MUST stay silent (no decision) outside rice repos, and MUST stay
  silent on its own internal error so that a rice bug never blocks a session.
- I14 Existing bootstrap / repair / verify / uninstall behaviour MUST be
  unchanged, bit for bit, by this spec. (§14 narrows this: two read-only
  preconditions move ahead of the first repo write, and on an update the
  OWNER's work is saved after them and before it (I24); nothing else
  changes.)
- I15 Update model. Rice MUST NOT update, reinstall or reload itself, and
  MUST NOT carry an `/rice:update` verb. An update is: the platform installs
  the new plugin build (`claude plugin update rice@rice-bootstrap`, or a new
  Cloud session from an unpinned Environment) → a session boundary (a new
  session, or the OWNER's own `/reload-plugins`) → the OWNER re-runs
  `/rice:bootstrap` in each repo. That re-run is the whole repo update, and
  it asks the OWNER nothing: the bootstrap skill asks only the choices
  `rice.py questions` prints, none on a repo rice already configured
  (RICE-FINAL-UX-REVIEW-001; the answers are written only when absent, so a
  re-asked question's answer was silently dropped). Both ways a bootstrap
  finishes say that rice's working rules load when a session starts: the
  current ones apply after `/clear` or in a new session.
- I16 Build precondition. Both lifecycle entry points (`/rice:bootstrap` and
  the Stop continuation) MUST confirm `plugin:build` is PASS before the first
  repo mutation (OWNER choices, apply, repair, the waiting marker, the version
  stamp). Not PASS ⇒ blocked with the verifier's own detail, zero writes.
- I17 Anti-downgrade. When `.claude/rice-version` holds a SemVer higher than
  the running build's version, every mutation path MUST fail closed before
  writing — including the stamp of a standalone final verification. Missing
  ⇒ not compared, never blocks. Malformed (not `MAJOR.MINOR.PATCH`, the
  running build's version included) ⇒ NOT_VERIFIED ⇒ blocked. Equal or lower
  ⇒ normal. The comparison is the existing SemVer shape and a three-integer
  tuple; no state, file, migration table or version history is added.
- I18 Staleness context. The SessionStart context MUST add exactly one
  actionable line — shown to the OWNER by Claude Code itself
  (`systemMessage`), and in the agent's context marked as theirs to type —
  when, and only when, rice is installed here, both versions
  are SemVer, the running build is newer than `.claude/rice-version`, and the
  waiting marker is absent. In every other case — not a rice repo, after
  uninstall, missing or malformed version, equal versions, a repo newer than
  the build, any error — it MUST add nothing. No marker, dismiss state,
  recurrence logic or polling MAY exist for it.
- I19 Pin semantics (documentation). The README MUST state that an
  Environment pinned to `@release/<x.y.z>` stays on that release until the
  OWNER changes the pin, that unpinned is the default and follows the latest
  release, and how a repo is brought up to date (I15).
- I24 Save before an update (RICE-UPDATE-PRESAVE-001). When bootstrap finds
  the repo finalized by an older rice than the running build, and only after
  I16 and I17 pass, it MUST save the OWNER's work before its first write:
  `git add -A` (`.gitignore` applies) — less rice's own pending writes from
  an earlier run of the same update, which are rice's commit to make — a
  commit when that staged anything, then a plain push of the current branch,
  unpushed commits included, to the remote git itself would push to
  (`branch.<b>.pushRemote`, else `remote.pushDefault`, else its upstream's,
  else `origin`) and always under its own name, setting the upstream for a
  branch never pushed. The OWNER running `/rice:bootstrap` to update is the
  authorization; nothing is asked. A first install, a same-version re-run, a
  newer or malformed repo, and the Stop continuation save nothing. Any
  failing step stops the update with nothing of rice's written: a detached
  or unborn HEAD, a merge, rebase, cherry-pick or revert in progress,
  unmerged entries, or an upstream of another name (git's own
  `push.default=simple` refuses that push too) before anything is staged;
  add, commit or push after. The work stays in the tree, with the OWNER's
  own index given back exactly, or in the local commit, and a re-run saves
  again. A repo with no remote at all cannot be saved, so it is not updated
  until one is added. Never a force push, history rewrite, branch deletion,
  stash, pull or merge; no setting, state file or git framework is added.

---

## 2. Exact lifecycle — convergence

Trigger: the OWNER's explicit request (e.g. 「合併回主枝」, "merge this back to
main"). Anything less — a finished task, a green test run, a closing message —
MUST NOT trigger it.

Preconditions the agent MUST check first; any failure ⇒ report and stop:

- The repo is a git work tree with remote `origin`.
- The default branch is readable (glossary).
- The current branch is a session branch (not the default branch). If the
  session is already on the default branch, convergence is a no-op: report it.
- The working tree is clean (`git status --porcelain` empty). Uncommitted work
  is the OWNER's to commit or discard; rice MUST NOT auto-commit here.

Steps, in this order, each one gated on the previous:

1. **Worktree.** If the session is inside a worktree Claude Code created, leave
   it with the native lifecycle first (`ExitWorktree` keep/remove). The
   remaining steps run in the primary checkout. Rice MUST NOT delete a
   worktree itself.
2. **Switch.** `git switch <default>`.
3. **Fetch.** `git fetch origin`.
4. **Fast-forward.** `git merge --ff-only <session>`. A non-fast-forward or a
   conflict MUST stop everything here, leave both branches as they are, and
   report the exact git output to the OWNER.
5. **Push.** `git push origin <default>`. (A push on the default branch does not
   match any rice deny rule; Auto mode MAY ask the OWNER once — that is the
   platform's prompt, not rice's.)
6. **Prove.** `git merge-base --is-ancestor <session> origin/<default>` MUST
   succeed. If it does not, stop; nothing is deleted.
7. **Cleanup remote.** Only now:
   - PR path: if the OWNER merged through a pull request and GitHub's
     *Automatically delete head branches* is on, the branch is already gone;
     verify with `git ls-remote --heads origin <session>` and report.
   - Otherwise: `python3 <plugin root>/scripts/delete_remote_branch.py <repo> <session>`.
     Its existing refusals (default branch, `main`, `master`, invalid name,
     unreadable origin) stand. An already-absent branch is success.
   - A session branch that was never pushed has no remote ref: skip and report.

What convergence MUST NOT do:

- MUST NOT delete the local session branch or any worktree. Cloud VMs discard
  them; local sessions use Claude Code's exit-time keep/remove and
  `ExitWorktree`. (`Bash(git branch *)` stays denied; no new helper.)
- MUST NOT use `--force`, `-D`, rebase, or any denied command.
- MUST NOT run any step while a previous one has failed.

Reporting: on success, name the default-branch SHA the push produced and the
cleanup outcome. On failure, quote the failing command and its output; name the
step; state that no branch was deleted.

Where the order lives: in `rice.md` (the OWNER's working rules), as a numbered
procedure. It is agent behaviour over existing git commands and the existing
helper; it is not a new script.

---

## 3. The guard — hook behaviour

One plugin-level PreToolUse hook, registered in `hooks/hooks.json` with matcher
`Edit|Write|NotebookEdit|Read|Grep|Glob|Bash`, implemented as one script under
`scripts/`. It receives the hook payload on stdin (`cwd`, `tool_name`,
`tool_input`, `transcript_path`) and prints at most one JSON decision.

Common rules:

- G1 If `reconcile.rice_installed_here(cwd)` is false ⇒ print nothing, exit 0.
- G2 Paths are resolved against `cwd` (which follows the agent into worktrees).
  "Targets X" means the resolved path equals X or lies under X.
- G3 A decision is `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": <reason>}}`.
  The guard MUST NOT emit `allow` (it never widens permissions), MUST NOT emit
  `ask`, and MUST NOT emit `updatedInput`.
- G4 Any exception inside the guard ⇒ print nothing, exit 0 (I13). The guard
  MAY write one line to stderr.
- G5 The guard MUST evaluate both rules below on every call and deny if either
  says so; it MUST NOT keep state between calls.

### 3.1 CLAUDE.md governance

Applies when the tool is Edit, Write or NotebookEdit and `file_path` targets
`<repo>/CLAUDE.md`, or when the tool is Bash and the command text names
`CLAUDE.md` as the target of a write (a redirection `>`/`>>` or `tee` whose
operand resolves to `<repo>/CLAUDE.md`; `rtk`-rewritten forms count the same).

- C1 Deny — if and only if bootstrap completion holds (`.claude/rice-version`
  present under `cwd`'s repo). Before that the rule is inert: Rice's own
  import line and Matt's `## Agent skills` block are written during bootstrap,
  before the file exists.
- C2 Reason text (normative content, wording MAY vary):
  "rice: CLAUDE.md is routing only. Do not write status, progress, reports,
  decisions, hand-off or working memory here. Put the content in an ordinary
  project file that Claude Code does not load automatically — choose the file
  from the content. Never: CLAUDE.md, any @import of it, .claude/rules files
  without `paths:`, auto-memory MEMORY.md, AGENTS.md. If the OWNER wants
  CLAUDE.md itself changed, the OWNER edits it directly."
- C3 Reads of `CLAUDE.md` are never affected.
- C4 Rice's own writes to `CLAUDE.md` (reconcile.apply, repair) are Python
  subprocesses, not tool calls; the guard never sees them either way.
- C5 Known consequence, accepted by the OWNER: re-running Matt's setup on a
  completed repo (e.g. switching issue trackers) is an agent write to
  `CLAUDE.md` and is denied like any other. The OWNER edits the block directly,
  or runs `/rice:rice-uninstall` and bootstraps again. Rice MUST NOT add a
  bypass for this.

### 3.2 session-history gate

Applies when the tool is Read, Grep or Glob and `file_path`/`path`/`pattern`
targets `<repo>/session-history/**`, or when the tool is Bash and the command
text contains the path segment `session-history` (any form: `cat`, `head`,
`sed`, `rtk read`, `cd session-history && …`, `grep … session-history`).

- H1 Deny unless the key is present (§5).
- H2 Reason text (normative content): "rice: session-history/ is cold
  historical evidence and is closed by default. If this task genuinely needs
  it, ask the OWNER to invoke /rice:history; do not work around this."
- H3 Repo-wide Grep/Glob/`grep -r` whose arguments do not name
  `session-history` are NOT matched. The OWNER has accepted that such searches
  MAY surface individual matching lines or filenames from the archive. Rice
  MUST NOT add filtering, `updatedInput` globs, `.ignore` files or any other
  mechanism for this.
- H4 Subprocess reads that do not name the path (e.g. a Python one-liner) are
  outside the guard's reach by design. Rice MUST NOT add a sandbox
  configuration, OS-level deny, or any other mechanism for this.
- H5 The archive hook's own reads and writes of `session-history/` are hook
  subprocesses, not tool calls; unaffected.

---

## 4. Activation boundary (replaces the former Matt exception)

The CLAUDE.md rule (§3.1) is active exactly when bootstrap completion holds.

- A1 Evidence: `<repo>/.claude/rice-version` exists. The guard reads existence
  only; it MUST NOT parse or compare the version, MUST NOT consult any other
  file, and MUST NOT create, touch or remove anything.
- A2 Sequence this yields on a fresh repo: `/rice:bootstrap` writes the import
  line (Python) → Matt's setup writes `## Agent skills` (Edit tool; guard
  inert, file absent) → Stop continuation passes Gate B → verifier writes
  `.claude/rice-version` → guard active from the next tool call on.
- A3 `/rice:rice-uninstall` removes `.claude/rice-version` → guard inert again.
- A4 The history rule (§3.2) is NOT tied to A1; it applies in every rice repo
  (`rice_installed_here`), before and after completion.
- A5 No new marker, state file, settings key or Gate item MAY be introduced for
  this. `.claude/rice-version` keeps its existing single writer and single
  remover.

Rice's verifier keeps requiring the `## Agent skills` heading in `CLAUDE.md`
(`evidence.MATT_HEADING`); nothing in this spec changes Matt's workflow or
that check.

---

## 5. History Skill scope

- S1 The skill is `skills/history/SKILL.md`, invoked as `/rice:history`. It is
  the key and nothing else: it MUST NOT run scripts, MUST NOT ask the OWNER for
  keywords, MUST NOT produce an index, summary, ranking or reader. Its body
  tells the agent: history access is open for this `/clear` segment; decide
  from the original task what to search and read in `session-history/`; use
  what you find for that task; do not summarize the archive unless asked.
- S2 Key detection (in the guard): the current `/clear` segment contains a
  user message that starts with the typed command and carries
  `<command-name>/rice:history</command-name>` (compaction summaries and
  meta records excluded). The skill sets `disable-model-invocation: true`, so
  the model cannot invoke it; a `Skill` tool_use naming it can only be a
  refused model attempt that still lands in the transcript, and MUST NOT
  count as a key. Unqualified names (`/history`, `history`) MUST NOT count:
  another skill may own them. Detection reads the transcript at
  `transcript_path` only; it MUST NOT read or write any marker file.
  (Clarified in implementation from probe evidence: an OWNER invocation is
  recorded as the command message, never as a Skill tool_use.)
- S3 Scope: from the invocation to the next genuine `/clear` boundary. A new
  session, a `/clear`, or a transcript without the invocation ⇒ closed. Cloud
  `/clear` starts a new transcript and therefore closes the key by
  construction.
- S4 While open, every Read/Grep/Glob/Bash that targets `session-history/**` is
  allowed (the guard simply prints nothing). No OWNER approval is requested per
  read.
- S5 Invoking the skill in a repo without rice is harmless: the guard is silent
  there anyway (G1).

---

## 6. Ownership

| Artifact | Created by | Repaired by | Verified by | Removed by |
| --- | --- | --- | --- | --- |
| guard script + `hooks.json` entry | the plugin build | n/a (plugin file) | tests only; no Gate item | uninstalling the plugin |
| `skills/history/SKILL.md` | the plugin build | n/a | tests only | uninstalling the plugin |
| `rice.md` policy lines | `reconcile.apply` (whole-file, existing) | `repair` (existing drift convergence) | Gate A `rice.md` (existing) | `/rice:rice-uninstall` (existing) |

Nothing new is written into a target repo. `OWNED_FILES`, `RICE_DENY_ENTRIES`,
`OWNER_DEFINED_KEYS` MUST NOT change. `GATE_A` (and therefore `GATE_B`) gains
exactly the one item §14 names; nothing else. The export allowlist gains
exactly the guard script and the skill file.

---

## 7. Failure behaviour

- Convergence: first failing step stops the sequence; nothing is deleted; the
  OWNER gets the failing command and its output. Rice MUST NOT retry, MUST NOT
  fall back to `--no-ff`, rebase or force, and MUST NOT delete on a later turn
  without the OWNER asking again.
- Guard: a denied write/read returns the reason to the model as a tool error;
  the model proceeds under that instruction. A guard crash is silent (G4). An
  unreadable transcript ⇒ treat the history key as absent (fail closed on the
  privilege, fail open on the session).
- Archive: unchanged — report to stderr, never block.

---

## 8. Interaction with bootstrap / repair / uninstall / verify

- `/rice:bootstrap`: unchanged. `reconcile.apply` writes the updated `rice.md`
  on a fresh repo; on an already-bootstrapped repo the old `rice.md` is a Gate A
  drift that `repair` converges when bootstrap is re-run. The history gate is
  active from the moment the new build is loaded; the CLAUDE.md rule is active
  in every repo that already carries `.claude/rice-version` (i.e. every
  finalized repo) from that same moment, with nothing to migrate.
- `repair`: unchanged (converges `rice.md` as any whole-file drift).
- `verify`: unchanged rubric; no new items (the rubric invariant that every
  produced item is gated stays trivially true).
- `/rice:rice-uninstall`: unchanged subtraction; there is nothing new in the
  repo to subtract. `remnants` reports clean exactly as before.
- Stop continuation, SessionStart context, archive hooks: unchanged; the new
  hook is an additional entry in `hooks.json`, not a modification of existing
  entries.
- Matt setup: unchanged; it runs before completion, when the CLAUDE.md rule is inert (§4).

---

## 9. Implementation Decisions

- One new hook script implements both §3.1 and §3.2 as pure decision functions
  over the parsed payload plus a transcript reader; the CLI entry only does I/O.
  It imports `reconcile.rice_installed_here`, reads completion through the
  verifier's existing `VERSION_FILE` constant, and reuses
  `archive_hook.clear_boundary_ends` for segmenting; it MUST NOT re-implement
  any of them and MUST NOT parse CLAUDE.md content.
- The History Skill is prose only.
- `rice.md` gains: the convergence procedure (§2, numbered); "platform session
  branches/worktrees are workspaces, never invent a branch"; "CLAUDE.md is
  routing only; working state goes to ordinary project files"; "session-history
  is cold; do not read it unless /rice:history was invoked".
- `session_context.py`'s SessionStart context keeps its helper line as is and
  MAY add the single staleness line of I18, under exactly its conditions;
  nothing else.
- Version: one plugin release carries all of the above. CHANGELOG entry
  required. README MAY gain one line each for `/rice:history` and 「合併回主枝」;
  it MUST stay short.

---

## 10. Testing Decisions

Good tests here exercise external behaviour only: a hook payload in, a decision
(or silence) out; a git repo in, branch/ref state out. No test inspects the
guard's internals.

Seams (existing ones preferred; one new one):

- **Hook payload → stdout** — the seam every rice hook already uses
  (`tests/test_bootstrap_continue.py`, `tests/test_delete_remote_branch.py::TestSessionContext`).
  Feed JSON payloads with a temp repo and a fixture transcript; assert the
  exact decision or empty output.
- **Transcript fixture** — a small JSONL with and without the key / Matt
  invocation, before and after a genuine `/clear` boundary. Prior art:
  `tests/test_archive_hook.py` transcript fixtures.
- **Temp git repos with a bare origin** — prior art:
  `tests/test_delete_remote_branch.py::_make`. Used to assert the convergence
  procedure's git preconditions as documented in `rice.md` and the helper's
  behaviour at step 7 (already covered; extend only if the helper changes —
  it MUST NOT).

Coverage the ticket MUST include:

- Guard silent in a non-rice repo; silent on malformed payload; silent on
  internal exception (mocked).
- CLAUDE.md: with `.claude/rice-version` present — Edit/Write/NotebookEdit
  denied with the §3.1 reason; Bash `>>`/`tee`/`rtk` forms denied; Read of
  CLAUDE.md untouched; other files untouched. Without it — every one of those
  writes passes silently (bootstrap-phase behaviour, Matt's block included).
  After `uninstall.uninstall` — silent again.
- History: Read/Grep/Glob/Bash forms targeting `session-history` denied with
  the §3.2 reason; allowed after the OWNER's typed `/rice:history` in the
  segment, never on a (refused) `Skill` tool_use or an unqualified `history`
  (§5 S2); closed again after a genuine `/clear`; repo-wide Grep not matched.
- Regression: `RICE_DENY_ENTRIES` still 28 and byte-identical; `GATE_A` /
  `GATE_B` unchanged except for the one item §14 names; `OWNED_FILES` unchanged; `hooks.json` existing entries
  unchanged and the new entry present; export allowlist includes the two new
  files; adapters still carry no lifecycle details (existing scan test).
- `rice.md` text contains the convergence steps in order and the three policy
  lines; uninstall round-trip still `RICE-UNINSTALLED` on a rice repo.

---

## 11. Fresh repo E2E acceptance criteria

A new repo, new Cloud Environment with rice installed at user scope:

1. `/rice:bootstrap` → `RICE-VERIFIED`, Matt hand-off; `CLAUDE.md` holds only the
   import line.
2. OWNER runs Matt setup → `## Agent skills` block written (guard inert: no
   `.claude/rice-version` yet); Stop hook finishes → `FINAL-VERIFIED` writes
   `.claude/rice-version`, pushed to the session branch; the CLAUDE.md rule is
   active from here.
3. During work the agent tries to append a status line to `CLAUDE.md` → denied;
   no OWNER prompt; the agent writes the status into an ordinary project file it
   chose; `CLAUDE.md` unchanged.
4. The agent greps the repo → a history line may appear; the agent tries to
   `Read` the archive file → denied with the history reason; no prompt.
5. `/clear` → archive file + INDEX row + local commit on the session branch;
   next `SessionStart(clear)` syncs only archive commits.
6. OWNER invokes `/rice:history` and asks a question that needs the archive →
   the agent reads under `session-history/` freely; no per-read prompt.
7. `/clear` → the agent's next attempt to read the archive is denied again.
8. OWNER says 「合併回主枝」 → steps 2–7 of §2 run in order; `origin/main`
   contains the session commits (ancestor proof); the remote session branch is
   gone (GitHub auto-delete or helper); local branch untouched.
9. A second convergence request on the now-default branch is reported as a
   no-op; nothing deleted.
10. `/rice:rice-uninstall` → `RICE-UNINSTALLED`; the repo carries nothing from
    this spec.

None of the above may produce: a permission prompt caused by rice, an
always-loaded file growing, a dirty working tree left by rice, a deleted branch
without ancestor proof, a lost archive commit, or an uninstall residue.

---

## 12. Out of Scope

- Any change to the 28 deny rules, the verifier rubric, the archive lifecycle,
  the Stop continuation, the release model, or the install model.
- Local branch / worktree deletion by rice (native lifecycle only).
- A `finish_branch` helper, merge automation, PR creation, or any convergence
  trigger other than the OWNER's words.
- `permissions.ask` / `permissions.deny` rules for `CLAUDE.md` or
  `session-history`; `updatedInput` redirects; a fixed status file.
- Filtering repo-wide search results; sandbox or OS-level read denies.
- Any Auto Memory integration; any memory generation, compression, summary or
  database; `.claude/rules` content shipped by rice.
- Retroactive cleanup of existing `CLAUDE.md` content.
- Any actor- or content-based exception to the CLAUDE.md rule (Matt or otherwise).
- Archiving on `SessionEnd` reasons other than `clear`.
- Changing Matt Pocock's skills or where they write.

## 13. Further Notes

- Evidence behind the frozen decisions: PreToolUse deny+reason redirects the
  model with no prompt (probe); `updatedInput` on Edit copies the source file
  into the target (probe, rejected); `Read(...)` deny rules are bypassed by
  RTK's `cat → rtk read` rewrite and never filter repo-wide Grep/Glob (probes),
  which is why the gate is a command-text hook and not a permission rule;
  `/clear` in the OWNER's Cloud environment fires `SessionEnd(clear)` and the
  archive (transcript evidence); a Cloud session can `git switch main` (probe).
- Enabling GitHub's *Automatically delete head branches* on the OWNER's repos is
  a one-click repo setting outside rice; it is the preferred PR-path cleanup.

---

## 14. Update safety (patch RICE-UPDATE-SPEC-001)

Source: RICE-UPDATE-RESEARCH-001 (`docs/research/`), RICE-UPDATE-GRILL-001.
Normative: I15–I19. This section says how they land; it adds no state.

### 14.1 Facts the design rests on

- Official Claude Code: `claude plugin update` loads the new build only in
  the next session or after the OWNER's `/reload-plugins`; a plugin's
  manifest `version` is the version, so nothing moves until it is bumped;
  a marketplace added with `@ref` updates to that ref only; the old cache
  directory is kept 14 days, so a running session keeps its build. Install
  or update lifecycle hooks do not exist. Whether a plugin may update itself
  from inside a session is undocumented.
- Rice: `plugin:build` compares the installed version with the running
  build and depends on no repo state. `verify.run` is read-only except the
  Gate B stamp. The verified version is always `build_version()`, which the
  release gates force to equal `plugin.json` = CHANGELOG = `release/<v>`;
  therefore every stamped `.claude/rice-version` is a three-integer SemVer.
- Probe (research round): re-running bootstrap with a newer build on a
  finalized repo asks nothing, converges `rice.md` through repair, and
  restamps the version; settings and `CLAUDE.md` are untouched. With an
  installed build that differs from the running one, bootstrap blocks on
  `plugin:build` — but only after the OWNER-choice and apply writes.

### 14.2 Bootstrap order (I16)

The lifecycle engine keeps its canonical flow. Two read-only preconditions
are consumed from the first verifier report, before the first write:

1. verifier report (read-only)
2. stop: `plugin:installed` not PASS (existing)
3. stop: install is not the canonical user-scope one (existing)
4. stop: `plugin:build` not PASS (new consumption of an existing item)
5. stop: `rice-version:not-newer` not PASS (new item, §14.3)
6. on an update only: save the OWNER's work — add, commit, push (I24, §14.8);
   any failure stops here, before rice's first write
7. OWNER choices → apply → Gate A with repair → waiting marker → Gate B
   with stamp (unchanged)

The Stop continuation keeps its two silent guards (not waiting; Matt not
started) and then applies 4–5 to its own first verifier report: not PASS ⇒
a blocked outcome with the verifier's detail, and repair MUST NOT run. That
case already reported blocked every turn; the mutation disappears, and the
message the Stop hook relays becomes the blocked directive (what blocks,
"nothing was written", report to the OWNER) instead of the repair report
and the instruction to re-run final verification. Whether 4–5 hold is the
verifier's judgement (its report names every precondition item not PASS,
a missing item included); the lifecycle engine only reads it.

Neither precondition reads anything apply or repair produce, so there is no
circular dependency. Fresh repo, re-run on a finalized repo, repair path and
the waiting-for-Matt path all pass both preconditions as today.

### 14.3 The anti-downgrade item (I17)

One verifier item, kind MATCHES, in Gate A (hence Gate B):
`rice-version:not-newer`.

- No `.claude/rice-version` ⇒ PASS "nothing to compare".
- Both versions `MAJOR.MINOR.PATCH` and repo ≤ build ⇒ PASS.
- Both SemVer and repo > build ⇒ FAIL, detail naming both versions and the
  remedy: start a session that loads rice ≥ the repo's version, or change the
  Environment's pin; the repo is left untouched.
- Either side not SemVer ⇒ NOT_VERIFIED, detail naming the unreadable value.
  The OWNER repairs the file by hand or uninstalls; rice never rewrites it.

Repair treats the item as unresolved (it is not owned). Because the item is
in Gate B, a standalone `verify --final` cannot restamp a newer repo either.
The comparison reuses the existing SemVer shape; no new parser is written.

### 14.4 The staleness line (I18)

Lives in the existing SessionStart context hook, as a second line after the
helper line, printed only when all of: rice installed here (the existing
reconcile question), `.claude/rice-version` present and SemVer, running build
SemVer, build > repo, waiting marker absent. Text (wording MAY vary):
`rice: this repo was finalized by rice <repo>; this session runs rice <build>.
Run /rice:bootstrap to bring it up to date (it first commits and pushes your
work).` Any exception ⇒ silence.
RICE-FINAL-UX-REVIEW-001: as plain context the line reached no one in real
sessions — not addressed to anyone, nor when it told the agent to tell the
OWNER. The hook therefore prints, for this case only, a JSON object:
`systemMessage` is the line (Claude Code shows it at session start), and
`additionalContext` is the helper line plus the line marked as the OWNER's
to type. Every other case prints the helper line as plain text, unchanged.

Why the marker silences it: a repo mid-bootstrap is already being driven by
the hand-off directive and the Stop continuation; two voices would conflict.
Why a repo newer than the build is silent: bootstrap's blocked directive
(§14.3) says what to do at the moment it matters; a per-session line would
nag every session of a deliberately pinned Environment.

### 14.5 Documentation (I19)

README: the pin sentence gains "stays on that release until you change the
pin"; one sentence under *Bootstrap a repo* says how an update reaches a
repo (new session or `/reload-plugins`, re-run `/rice:bootstrap`), how a
machine updates (`claude plugin marketplace update rice-bootstrap`, then
`claude plugin update rice@rice-bootstrap`: in a real session the update
alone answered "already at the latest version"), and that rice's working
rules take effect after `/clear` or in a new session. CHANGELOG entry for the
release carrying this. The README release-contract gate is unaffected (it
only requires that the sole release branch named is the build's own).

### 14.6 Failure modes, as they land

| Case | Outcome |
| --- | --- |
| old plugin, newer repo | blocked before any write (14.3); context silent |
| new plugin, older repo | context line; re-run bootstrap ⇒ work saved (I24) ⇒ FINAL-VERIFIED new |
| update, save fails (commit or push) | blocked before rice's first write; the work stays in the tree (index as it was) or the local commit; re-run saves again |
| update, merge/rebase in progress or no remote | blocked before anything is staged; the OWNER finishes it or adds a remote |
| same version | idempotent re-run, no questions, silent; the OWNER's work-in-progress is not committed |
| no rice-version | not compared; silent (fresh or waiting repo) |
| malformed rice-version | NOT_VERIFIED ⇒ blocked before any write; silent |
| plugin updated, not reloaded | old hooks run; `plugin:build` FAIL ⇒ both entry points block before any write |
| repair fails midway | unchanged: partial owned state, converged by the next run; 14.2–14.4 add no writes |
| Gate B incomplete | unchanged: marker stays, Stop continuation retries; context silent while the marker exists |
| after uninstall | not a rice repo ⇒ silent; version file already removed |
| Cloud unpinned / pinned | unpinned: every new container is the latest; pinned: stays until the pin changes (README says so) |

### 14.7 Out of scope for this patch

An `/rice:update` verb; rice running `claude plugin update` or
`/reload-plugins`; migration scripts, version history, downgrade or rollback
tooling; an uninstall by an older build of a repo finalized by a newer one
(no repo file was added since 0.1.6, so there is nothing newer to subtract
yet); prerelease or build-metadata version formats; any change to the
release model.

### 14.8 Save before an update (I24)

Lives in the lifecycle engine (`rice.py`), between the preconditions and the
first write, using only native git through the archive sync's existing
helpers. The steps, each stopping the update on failure:

1. `version_order` says older → newer; otherwise nothing happens.
2. The current branch (detached or unborn HEAD stops; so do a merge,
   rebase, cherry-pick or revert in progress, unmerged entries, and an
   upstream whose branch has another name — all before anything is
   staged). `git write-tree` records the OWNER's index to give back on any
   later failure.
3. `git add -A`, then unstage rice's own pending writes
   (`reconcile.only_rices_change`: `.claude/rice.md` at this build's
   content, the pending marker, or `CLAUDE.md` / `AGENTS.md` /
   `.claude/settings.json` whose project-owned part — uninstall's projection
   — is unchanged since HEAD). A new session's first run writes and then
   stops for the git-deny probe; without this, the re-run would save rice's
   half-written update as the OWNER's work (a real session did). When
   something is still staged, `git commit -m "rice: save work before
   updating rice <old> -> <new>"` (a failed commit gives the index back).
4. When `@{push}..HEAD` is not empty (or does not resolve): `git push
   <remote> HEAD:refs/heads/<branch>`, with `-u` for a branch never pushed;
   60 s and no credential prompt, so it fails inside the bootstrap command.
   The stop message keeps git's own reason (hints dropped) and the next
   step (with no remote: `git remote add origin <url>`).

The upgrade matrix's sandboxes track a bare origin and commit each older
release's hop, as the agent does; any commit the candidate's update then
makes is rice's own writes saved as the OWNER's work, a harmful finding.

The OWNER sees one line before the usual directive, naming the commit and
the push — also on a re-run of the same update, whose HEAD is still that
save commit (its subject names both versions). The agent's own commit after
FINAL-VERIFIED stays as it was: rice's paths by name, never `git add -A` —
on an update, nothing else is left by then.

---

## 15. Upgrade compatibility (patch RICE-UPGRADE-MATRIX-001)

Source: the RICE-UPGRADE-MATRIX-001 and RICE-UPGRADE-PATH-001 audits -- every
public `release/<semver>` branch (0.1.2-0.1.6) bootstrapped by its own shipped
CLI and Stop hook, then upgraded to the candidate by re-running
`/rice:bootstrap`, directly and along its historical path through every later
release, compared with a fresh candidate install and uninstalled; plus the
three `rice 0.1.1` builds public `main` carried before release branches
existed (ec66f54, 07c4891, 7df1741), checked separately by the steps their
own skill documented (they had no CLI). Normative: I20-I23. Corrected by
RICE-OWNERSHIP-FIX-001: the subagent model is the project's (I21).

- I20 Convergence. A repo bootstrapped by any published release, re-bootstrapped
  by this build, MUST equal a fresh install of this build on the same project
  (settings compared as JSON); bootstrapping it once more MUST change no byte;
  and uninstall MUST then leave exactly the project. FINAL-VERIFIED while a
  difference remains is a defect, never a pass.
- I21 Provable take-back only. A lifecycle MAY remove a value only when a
  released build's declaration proves it rice's, at the exact shape that
  build wrote, AND the value means nothing but a rice install rice itself
  now forbids -- a project-scope declaration of rice is rice's by
  definition (RICE-FIX-001), whoever typed it. Everything else is the
  project's, and bootstrap, update, repair and uninstall MUST neither
  delete nor rewrite it, nor fail an item over it. Taking a leaf back also
  takes back every container that removal emptied -- the rule uninstall's
  projection already applies, and the one exception to the rule above (a
  project's own empty container that the declaration then filled; known
  limits). Today exactly one thing is taken back: the 0.1.2/0.1.3
  project-scope declaration, at exactly the shape those builds wrote --
  `extraKnownMarketplaces.rice-bootstrap` = `{"source": {"source":
  "github", "repo": <rice's public repo>, "ref": <string>}}` and nothing
  more, and `enabledPlugins.rice@rice-bootstrap` = `true` beside it or with
  no marketplace entry -- plus the containers that removal empties. A
  `false`, an entry with a key the project added, or the enablement of a
  same-named marketplace that is not rice's, is the project's.
  `env.CLAUDE_CODE_SUBAGENT_MODEL` is the project's, whatever its value
  (RICE-OWNERSHIP-FIX-001). No release branch ever wrote it; the value the
  pre-release 0.1.1 builds wrote, "sonnet", is one a project sets for its
  own reasons, so nothing proves a given repo's copy rice's. No take-back,
  no verifier item, no marker: rice does not record provenance (I12).
- I22 Release gate. `release.py check` runs `upgrade:matrix` against the
  export. Rows: every `release/<semver>` on the public repository,
  enumerated at run time, upgraded directly (release → candidate) and, for
  every release but the newest, along its historical path (release → each
  later release in order → candidate, each hop that release's own
  lifecycle; RICE-UPGRADE-PATH-001), plus `fresh`; each row runs on a bare
  and a rich project. HARMFUL RESIDUE, PROJECT DATA CHANGED, BLOCKED, or an
  unreadable public repository, block the release. No minimum supported
  upgrade version exists; `upgrade_matrix.releases()` is where one would go.
- I23 Benign legacy residue. A row MAY be BENIGN LEGACY RESIDUE, which does
  not block, only for a leftover that (1) no build can prove rice's,
  (2) has no runtime effect, and (3) this section records as a known limit.
  Today that is exactly one thing: an empty `extraKnownMarketplaces` or
  `enabledPlugins` object -- created by 0.1.2/0.1.3 for their declaration,
  emptied but not removed by 0.1.4-0.1.6's migration, indistinguishable
  from one the project wrote (`upgrade_matrix.BENIGN_HUSKS`) -- and only
  when it was already empty when the candidate arrived. The same object
  emptied by the candidate's own migration is the candidate's to remove;
  left behind, it is harmful. Benign residue is reported by name and never
  deleted by guessing; anything else is harmful.

Known limits, deliberately not migrated (no historical declaration proves
them rice's, and rice does not guess project-owned state):

- Empty `extraKnownMarketplaces` / `enabledPlugins` objects that 0.1.4-0.1.6
  already left in repos they migrated from 0.1.2/0.1.3 (multi-hop). Inert;
  the OWNER may delete them by hand. The matrix's historical rows from
  0.1.2 and 0.1.3 report exactly this, as BENIGN LEGACY RESIDUE (I23), on a
  project that had neither object of its own.
- The public-`main` 0.1.1 builds owned `permissions.deny` as a whole list, so
  in a repo with its own deny rules their Gate A stopped on a conflict and
  rice's occurrences were never added. After an update, a project rule
  identical to a rice rule cannot be told apart from rice's, and a later
  uninstall takes it. Not reachable from any release branch.
- A project's own pre-existing `outputStyle: "rice:老弟"` is taken by
  uninstall (the same value rice writes); unchanged here.
- `env.CLAUDE_CODE_SUBAGENT_MODEL: "sonnet"` written by the pre-release
  0.1.1 builds on public `main` (ec66f54 / 07c4891) stays in the repos they
  bootstrapped: no lifecycle removes it, uninstall included (0.1.2-0.1.6's
  uninstall removed that value; this build does not). It keeps pinning
  subagents to Sonnet there until the OWNER deletes it by hand. Not
  reachable from any release branch.
- A project's own pre-existing empty `extraKnownMarketplaces` or
  `enabledPlugins` object that 0.1.2/0.1.3 then filled with rice's
  declaration is removed together with that declaration (an emptied
  container is read as rice's, the rule uninstall has always applied).
- Uninstall leaves the blank line apply put before the import line when
  Matt's block follows it (whitespace only; fresh installs alike).
