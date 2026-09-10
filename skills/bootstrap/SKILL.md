---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, Sonnet subagent default, an OWNER-chosen output style, the OWNER's working rules), runs the mechanical verifier, then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

This skill describes *how to operate* rice. It decides nothing. The only
authority on whether this repo is verified is the deterministic verifier:

```
python3 "<plugin root>/scripts/verify.py" <repo_root>          # Gate A
python3 "<plugin root>/scripts/verify.py" <repo_root> --final  # Gate B
```

Only its output may say `RICE-VERIFIED <build>` or `FINAL-VERIFIED <build>`,
and only its Gate B pass writes `.claude/rice-version`. If it prints a
FAIL line, the repo is not verified — there is nothing for you to weigh,
waive, or interpret; fix what the listed item says and run it again.

Everything below is idempotent (`reconcile.apply` is create-if-missing /
no-op-if-correct / conflict-if-drifted), so re-running on a partly or fully
bootstrapped repo is always safe. Re-derive where you are from the verifier
output each time; never assume a first run.

The order is fixed (RICE-BOOTSTRAP-042) and you are the *last* step in it,
never the first:

```
normal path -> verify -> FAIL -> deterministic repair -> verify again -> FAIL -> you
```

`scripts/repair.py <repo_root> [--final]` is that whole middle: it runs the
verifier, and only on a FAIL applies the known lossless repair for every
failure the verifier named, then runs the same verifier again. It has no
verdict of its own. Do not analyse, hand-edit, or try commands before the
verifier and `repair.py` have both had their turn; when `repair.py` still
prints a FAIL, fix exactly what its remaining lines say and nothing else.

Runtime facts (ticket #18, four real Cloud sessions plus local probes;
RICE-BOOTSTRAP-035 reconfirmed with two fresh-Environment E2E rounds):
declaring the marketplace/plugin in `.claude/settings.json` does not install
it, and a Cloud session never trusts the folder so that declaration is
ignored there; the official install is two non-interactive commands that
touch only `~/.claude`. On the recommended architecture those two commands
live in a Cloud Environment's setup script (run once, at container build),
so `rice:bootstrap` is already available in **every** fresh session started
from it — no per-session install, no `/reload-plugins`. On a local machine,
a newly installed plugin loads in the next session. The plugin's bundled
output style is addressed as `rice:老弟`; using it is the OWNER's choice,
not a requirement.

## Step 0 — Is rice loaded here?

Run the verifier once. If `plugin:installed` is PASS, rice is already
loaded — the normal case on the recommended architecture. Go straight to
Step 1.

If `plugin:installed` is FAIL, **stop and hand off** (this is the one step
before Matt that needs the OWNER — on the recommended Cloud architecture it
is a once-per-Environment fix, not a once-per-repo or once-per-session one):

> rice isn't loaded in this environment yet. The official install is
>
>     claude plugin marketplace add L26041040/rice-bootstrap
>     claude plugin install rice@rice-bootstrap
>
> - **Local machine**: I can run those two commands now, then you start a
>   new session and say "bootstrap this repo with rice" again.
> - **Claude Code on the web**: the fix belongs at the Environment level,
>   not this session — add those two commands to a Cloud Environment's setup
>   script (a dedicated rice Environment, or an existing one you already use
>   alongside `mattpocock-skills`/RTK), then start a new session from it.
>   Every session from that Environment onward already has rice loaded, no
>   further action needed. Running the two commands only inside this
>   session is a one-off recovery, not the normal flow — it will not survive
>   to the next container on its own.

No per-session workaround in Cloud: an install that only ran inside the
current session is not loaded until a new session/container starts, and
does not persist past this one.

Before enabling the session archive: if this repo already had its own
`/clear` archive hook or script before rice, the OWNER removes that old
hook/script first; an existing `session-history/` can stay as it is.

## Step 1 — The two owner-defined settings

Read `.claude/settings.json` (create `{}` if absent). Set, only if missing:

- `extraKnownMarketplaces.rice-bootstrap.source.ref` — the **delivery
  mechanism only**: `L26041040/rice-bootstrap`'s `main` branch's exact
  commit SHA (`git ls-remote https://github.com/L26041040/rice-bootstrap.git
  main`), never a tag name and never the floating branch name `main`
  itself. If upstream has no commits yet, say so and use the literal
  string `main` as a stopgap. This value is never a verified version;
  `.claude/rice-version` (verifier-written) is. No git tag or GitHub
  release is used anywhere in rice's lifecycle (RICE-BOOTSTRAP-030). This
  one is derived mechanically, not asked — it never goes through the card
  UI below.
- `env.RICE_SESSION_ARCHIVE` and `outputStyle` are **bounded OWNER
  choices** — each a small fixed set of options — so ask them together via
  Claude Code's native interactive choice UI (e.g. `AskUserQuestion`),
  batched into as few native prompts as the UI allows, rather than
  free-text questions (see `resources/rice.md`'s "Questions to the OWNER").
  Ask only once per repo: skip a card entirely if its key already exists,
  or (for `outputStyle`) if it is deliberately absent and
  `.claude/rice-version` already exists.
  - **Session archive** — Enabled (recommended, writes `"1"`) / Disabled
    (writes the release default instead of asking, if genuinely unclear
    which the OWNER wants).
  - **Output style** — offer exactly:
    1. `rice:老弟` — **recommended** → write `"outputStyle": "rice:老弟"`.
    2. Claude default → write **nothing**; the key stays absent. Never
       write a placeholder value to mean "default".
    3. An existing style → list what `output_styles.available(repo_root,
       plugin_probe)` (`scripts/output_styles.py`) returns — built-ins,
       project `.claude/output-styles/`, user styles, installed plugins'
       styles as `<plugin>:<name>` — as the card's options. The verifier
       confirms it resolves; don't offer a name the list doesn't contain.
    4. A custom style → the OWNER describes it in natural language (this
       one option leads to a follow-up, not a fixed card); write a valid
       Claude output style to `.claude/output-styles/<name>.md`
       (frontmatter `name: <name>`, `description:`, then the instructions
       body) and write `"outputStyle": "<name>"`. That file is
       project-owned: rice never rewrites it and `rice-update` preserves
       the choice.

Never overwrite any of these if present (owner-defined). Never touch
`.claude/rice-version` — it is not yours to write, in any step.

## Step 2 — Reconcile rice-owned state

```
import sys; sys.path.insert(0, "<plugin root>/scripts"); import reconcile
reconcile.apply(repo_root)          # never is_rice_update=True here
```

Creates every rice-defined settings leaf, copies `.claude/rice.md`, ensures
the `@.claude/rice.md` line in `CLAUDE.md`, and ensures the rice pointer
block in `AGENTS.md` (created if absent). Project-owned content in either
file is left alone.

Three files, one policy (RICE-BOOTSTRAP-042): `.claude/rice.md` is the only
place the working rules are written. `CLAUDE.md` is Claude's adapter — the
`@.claude/rice.md` line is a real import for Claude Code, and Matt's
`## Agent skills` section lives there too. `AGENTS.md` is the entry point for
every other coding agent (Codex and any AGENTS.md reader): those tools
concatenate the file verbatim and have no import syntax, so rice keeps only a
marker-delimited instruction there to read `.claude/rice.md` — never a copy
of it.

## Step 3 — Gate A: verify, repair, verify, then you

Run `verify.py <repo_root>`. `RICE-VERIFIED <build>` → go to Step 4. On
`RICE-GATE-A FAIL`, run once:

```
python3 "<plugin root>/scripts/repair.py" <repo_root>
```

It repairs, from this build's canonical state, every rice-owned failure the
verifier named — `settings:*` for rice-defined keys (missing *or* drifted),
`rice.md`, `CLAUDE.md:import-line`, `AGENTS.md:rice-pointer` — and re-runs
the verifier. Its last lines are the verifier's own. `RICE-VERIFIED` → Step
4. Only if it still prints `RICE-GATE-A FAIL` do you act, on exactly the
remaining lines — these are the ones no script can repair:

- `settings:*` for an owner-defined key → the Step 1 choice is missing;
  ask it (or derive `ref`) and write it.
- `plugin:*` / `output-style:bundled` / `*-hook:exists` → Step 0 (wrong or
  missing build; hooks are part of the build, not the repo).
- `output-style:choice` → the configured `outputStyle` points at nothing that
  exists; redo the Step 1 choice (or create the custom file it names).
- `git-deny:behaves` → run **exactly** `git branch --show-current` through
  Bash in this repo. It should be denied; under normal Claude Code
  execution the harness records that denial in the session transcript,
  and the verifier reads that record — your own statement that it was
  denied counts for nothing. If the command *runs*, the deny rules are not
  in effect; the verifier will keep failing; report that.

When the verifier prints `RICE-VERIFIED <build>`: commit and push the
rice-owned changes. Quote that line verbatim in your report. Nothing else
counts as verified.

## Step 4 — Hand off to Matt

First look at what the Gate A run already told you about `matt:artifacts`. If
it is **already PASS** (a re-bootstrap, or a repo that already had the official
Matt setup), there is nothing to hand off: call
`bootstrap_state.enter_waiting_for_matt(repo_root)` and then run
`python3 "<plugin root>/scripts/verify.py" <repo_root> --final` yourself in
this same turn, and report per Step 5. Never ask the OWNER for a setup they
have already done, and never print the hand-off below and a `FINAL-VERIFIED`
line in the same reply.

Otherwise `matt:artifacts` is FAIL, and this is the hand-off. Call
`bootstrap_state.enter_waiting_for_matt(repo_root)`, then reply **tersely**.
This is the OWNER's hand-off, not a status report: quote the verifier's line,
say what is left, give the command. No settings detail, no RTK line, no list
of which Matt artifacts are missing, no commit counts, no summary of what
bootstrap did. Roughly:

> `RICE-VERIFIED <build>`
>
> 還差 Matt repo setup；完成後 Rice 會自動續接 final verification。
>
> /mattpocock-skills:setup-matt-pocock-skills

The last line is exactly that command, with nothing after it.

Rice never invokes or simulates Matt's setup. Do **not** ask the OWNER to
run `/rice:bootstrap`, say "bootstrap this repo with rice", or type any
other continuation prompt afterwards — a fresh bootstrap costs the OWNER
exactly one invocation of this skill (RICE-BOOTSTRAP-028). The continuation
is automatic; see Step 5.

**Never report bootstrap as complete while `WAITING_FOR_MATT` exists.**

## Step 5 — Gate B: final verification (continues by itself)

On a fresh bootstrap that is genuinely waiting for Matt, you do not run this
step by hand, and neither does the OWNER. `scripts/bootstrap_continue.py` is
wired as a `Stop` hook. It stays silent on every turn until Matt's setup has
left anything behind; from then on, at the end of each turn while
`WAITING_FOR_MATT` is pending, it runs the same pipeline as Step 3 with
`--final` — verify, deterministic repair on a FAIL, verify again — and hands
the result to you (RICE-BOOTSTRAP-028, -042). An incomplete Matt setup is
therefore never left silently pending: what repair could not restore is
reported to you, turn after turn, until it is resolved.

Matt's artifacts are the one thing repair restores only from this repo's own
history (a doc tracked at HEAD but missing from the working tree; a
`## Agent skills` section present in HEAD's `CLAUDE.md` but gone from the
working copy). Every Matt file encodes the OWNER's answers to the official
setup, so anything the repo never committed is reported as *unresolved*, by
name. When you get such a line: never write Matt content yourself, never
copy a template, never simulate the setup — tell the OWNER exactly which
artifact is missing and that only `/mattpocock-skills:setup-matt-pocock-skills`
can produce it.

The `[B]` items need things only the OWNER/environment provide:

- `matt:artifacts` → the OWNER ran Matt setup. **Required**, not optional.
- `rtk` → the environment must provide it. **Required**; Gate B fails until
  it does. Without both there is no `FINAL-VERIFIED`.

A real `/clear` is **not** required to reach `FINAL-VERIFIED`
(RICE-BOOTSTRAP-032). Archive registration is checked mechanically in
Gate A instead (`archive-hook:exists`, `archive-sync-hook:exists`,
`continuation-hook:exists` — are the archive, sync and continuation hooks
wired in this build, not has any of them actually run). Whether a produced
archive commit has actually reached this branch's upstream is a separate,
explicit diagnostic (`reconcile._archive_behaves_item`, read from git
state) — deliberately not part of the standard verifier rubric, since it
gates nothing; call it directly if you need that answer.

When the continuation reports blockers, repair has already had its turn:
fix exactly what the remaining lines name (an environment item like `rtk`
is the OWNER's; a Matt line is the OWNER's, see above) and run
`verify.py <repo_root> --final` in that same turn, because the hook
deliberately stays quiet for the rest of a turn it has already spoken in.

When the continuation reports `FINAL-VERIFIED <build>`, the verifier has
already written `.claude/rice-version` and removed `WAITING_FOR_MATT`; commit
and push the bootstrap, Matt and finalization changes, and quote that line
verbatim to the OWNER. When it reports blockers, the verified-version file is
untouched. Either way, never ask the OWNER to re-invoke this skill.

## Re-running this skill

Re-running is safe but should never be *necessary* on a fresh bootstrap. If
the OWNER does invoke it again, run the verifier first: its output tells you
which step you are at.
