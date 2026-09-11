---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, Sonnet subagent default, an OWNER-chosen output style, the OWNER's working rules), runs the mechanical verifier, then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

You are an adapter between the OWNER and one command. This skill holds no
steps of its own: the lifecycle lives in `scripts/rice.py`
(RICE-BOOTSTRAP-044), and the only authority on whether this repo is
verified is the deterministic verifier it runs.

```
python3 "<plugin root>/scripts/rice.py" bootstrap <repo_root> \
    --ref <SHA> --archive 0|1 --output-style <name>|none
```

That one command writes the OWNER's answers (only if absent), reconciles
rice-owned state, and runs the canonical flow — verify, deterministic
repair on a FAIL, verify again — reporting the verifier's own lines. Only
those lines may say `RICE-VERIFIED <build>` or `FINAL-VERIFIED <build>`, and
only a Gate B pass writes `.claude/rice-version`. There is nothing for you
to weigh, waive, or interpret: quote the verdict, and act on exactly what
the remaining lines say.

Everything is idempotent, so re-running on a partly or fully bootstrapped
repo is always safe. Re-derive where you are from the command's output each
time; never assume a first run.

**You are the last step in the flow, never the first.** Do not analyse the
repo, hand-edit files, or try commands before that command has run. What it
still lists afterwards is what no script can repair — those, and only
those, are yours.

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

Run `python3 "<plugin root>/scripts/verify.py" <repo_root>` once. If
`plugin:installed` is PASS, rice is already loaded — the normal case on the
recommended architecture. Go to Step 1.

If `plugin:installed` is FAIL, **stop and hand off** (this is the one step
before Matt that needs the OWNER — on the recommended Cloud architecture it
is a once-per-Environment fix, not a once-per-repo or once-per-session one):

> rice isn't loaded in this environment yet. The official install is
>
>     claude plugin marketplace add L26041040/rice-bootstrap
>     claude plugin install rice@rice-bootstrap
>
> - **Local machine**: I can run those two commands now, then you start a
>   new session and ask for the bootstrap there.
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

## Step 1 — The OWNER's three answers

These become the three flags of the one command. Each is written only if
the key is absent, so an answer the OWNER already gave is never overwritten.

- `--ref` — the **delivery mechanism only**: `L26041040/rice-bootstrap`'s
  `main` branch's exact commit SHA (`git ls-remote
  https://github.com/L26041040/rice-bootstrap.git main`), never a tag name
  and never the floating branch name `main` itself. If upstream has no
  commits yet, say so and pass the literal string `main` as a stopgap. This
  value is never a verified version; `.claude/rice-version`
  (verifier-written) is. No git tag or GitHub release is used anywhere in
  rice's lifecycle (RICE-BOOTSTRAP-030). Derive it mechanically — it never
  goes through the card UI below.
- `--archive` and `--output-style` are **bounded OWNER choices** — each a
  small fixed set of options — so ask them together via Claude Code's
  native interactive choice UI (e.g. `AskUserQuestion`), batched into as few
  native prompts as the UI allows, rather than free-text questions (see
  `resources/rice.md`'s "Questions to the OWNER"). Skip a card whose key
  already exists (the command's output says so), and omit the flag.
  - **Session archive** — Enabled (recommended, `--archive 1`) / Disabled
    (`--archive 0`).
  - **Output style** — offer exactly:
    1. `rice:老弟` — **recommended** → `--output-style "rice:老弟"`.
    2. Claude default → `--output-style none`; the key stays absent. Never
       write a placeholder value to mean "default".
    3. An existing style → list what
       `python3 "<plugin root>/scripts/rice.py" styles <repo_root>` prints
       — built-ins, project `.claude/output-styles/`, user styles, installed
       plugins' styles as `<plugin>:<name>` — as the card's options. The
       verifier confirms the choice resolves; don't offer a name the list
       doesn't contain.
    4. A custom style → the OWNER describes it in natural language (this
       one option leads to a follow-up, not a fixed card); write a valid
       Claude output style to `.claude/output-styles/<name>.md`
       (frontmatter `name: <name>`, `description:`, then the instructions
       body), then pass `--output-style <name>`. That file is
       project-owned: rice never rewrites it and `rice-update` preserves
       the choice.

Never touch `.claude/rice-version` — it is not yours to write, in any step.

## Step 2 — Run the bootstrap

```
python3 "<plugin root>/scripts/rice.py" bootstrap <repo_root> \
    --ref <SHA> [--archive 0|1] [--output-style <name>|none]
```

Read its last lines and take exactly one of three branches:

**`FINAL-VERIFIED <build>`** — this repo already had the official Matt
setup, so the bootstrap finished inside this one invocation. The verifier
has written `.claude/rice-version`. Commit and push, quote that line
verbatim, and do **not** print the Matt hand-off: never ask the OWNER for a
setup they have already done.

**`RICE-VERIFIED <build>`** — Gate A passed and the repo is now waiting for
the OWNER's Matt setup (the command's output ends with a `waiting:` line).
Commit and push the rice-owned changes, then go to Step 3.

**A FAIL line** — repair has already had its turn; what is left is yours.
Fix exactly what each remaining line says — its detail names the fix — then
run the same command again. Two cases are the OWNER's, not yours:
`plugin:*` means the wrong or missing build (Step 0), and `git-deny:behaves`
means you must run **exactly** `git branch --show-current` through Bash in
this repo. It should be denied; under normal Claude Code execution the
harness records that denial in the session transcript and the verifier reads
that record — your own statement that it was denied counts for nothing. If
the command *runs*, the deny rules are not in effect; say so.

## Step 3 — Hand off to Matt

Reply **tersely**. This is the OWNER's hand-off, not a status report: quote
the verifier's line, say what is left, give the command. No settings detail,
no RTK line, no list of which Matt artifacts are missing, no commit counts,
no summary of what bootstrap did. Roughly:

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
is automatic; see Step 4.

**Never report bootstrap as complete while the repo is waiting for Matt.**

## Step 4 — The continuation happens by itself

`scripts/bootstrap_continue.py` is wired as a `Stop` hook: at the end of
every turn it asks the same lifecycle engine whether there is anything to
continue. It stays silent until the OWNER's Matt setup has left something
behind, and from then on runs the same flow through Gate B and hands the
result to you (RICE-BOOTSTRAP-028, -042, -044). Neither you nor the OWNER
runs this by hand, and an incomplete Matt setup is never left silently
pending.

Gate B needs two things only the OWNER and the environment provide:
`matt:artifacts` (the OWNER ran Matt setup) and `rtk`. Both are
**required** — without them there is no `FINAL-VERIFIED`.

Matt's artifacts are the one thing repair restores only from this repo's own
history (a doc tracked at HEAD but missing from the working tree; a
`## Agent skills` section present in HEAD's `CLAUDE.md` but gone from the
working copy). Every Matt file encodes the OWNER's answers to the official
setup, so anything the repo never committed is reported as *unresolved*, by
name. When you get such a line: never write Matt content yourself, never
copy a template, never simulate the setup — tell the OWNER exactly which
artifact is missing and that only `/mattpocock-skills:setup-matt-pocock-skills`
can produce it.

When the continuation reports `FINAL-VERIFIED <build>`, the verifier has
already written `.claude/rice-version` and cleared the pending marker; commit
and push the bootstrap, Matt and finalization changes, and quote that line
verbatim. When it reports blockers, the verified-version file is untouched:
fix what the remaining lines name and finish in that same turn as the hook's
message says. Either way, never ask the OWNER to re-invoke this skill.

A real `/clear` is **not** required to reach `FINAL-VERIFIED`
(RICE-BOOTSTRAP-032): archive registration is a Gate A fact
(`archive-hook:exists`, `archive-sync-hook:exists`,
`continuation-hook:exists` — wired in this build, not exercised). Whether a
produced archive commit has actually reached this branch's upstream is a
separate, explicit diagnostic in `scripts/reconcile.py`, read from git
state — deliberately not part of the verifier's rubric, since it gates
nothing.

## Re-running this skill

Re-running is safe but should never be *necessary* on a fresh bootstrap. If
the OWNER does invoke it again, run the Step 2 command first: its output
tells you which branch you are in.
