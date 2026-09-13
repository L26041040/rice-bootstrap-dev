---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, an OWNER-chosen output style, the OWNER's working rules), runs the mechanical verifier, then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

You are the OWNER-facing adapter for one command. This skill holds no
lifecycle steps: every decision — whether rice is loaded, what to write,
when to verify, when to repair, whether this repo still needs Matt's setup
— belongs to `scripts/rice.py` (RICE-BOOTSTRAP-044B). Your whole job is to
collect two answers, run the command, and pass its words on.

## 1 — Ask the OWNER's two bounded choices

Both are small fixed option sets, so ask them through Claude Code's native
interactive choice UI (e.g. `AskUserQuestion`), batched into as few native
prompts as the UI allows, rather than free-text questions (see
`resources/rice.md`'s "Questions to the OWNER"). They become flags; each is
written only if that setting is absent, so an answer the OWNER already gave
is never overwritten, and a re-run needs no questions at all.

- **Session archive** — Enabled (recommended) → `--archive 1`; Disabled →
  `--archive 0`. If this repo already had its own `/clear` archive hook or
  script before rice, the OWNER removes that old hook/script first; an
  existing `session-history/` can stay as it is.
- **Output style** — offer exactly:
  1. `rice:老弟` — **recommended** → `--output-style "rice:老弟"`.
  2. Claude default → `--output-style none`; the key stays absent. Never
     write a placeholder value to mean "default".
  3. An existing style → the card's options are exactly what
     `python3 "<plugin root>/scripts/rice.py" styles <repo_root>` prints;
     don't offer a name that list doesn't contain.
  4. A custom style → the OWNER describes it in natural language (this one
     option leads to a follow-up, not a fixed card); write a valid Claude
     output style to `.claude/output-styles/<name>.md` (frontmatter
     `name: <name>`, `description:`, then the instructions body), then pass
     `--output-style <name>`. That file is project-owned: rice never
     rewrites it.

If the OWNER expressed no preference and you cannot ask, omit the flag
rather than guessing: an unanswered choice is written later, not wrongly now.

## 2 — Run it

```
python3 "<plugin root>/scripts/rice.py" bootstrap <repo_root> \
    [--archive 0|1] [--output-style <name>|none]
```

That is the entire bootstrap. It checks that rice is loaded at the
canonical user scope (never installing or declaring the plugin itself —
that is the Environment's job, done once), writes the OWNER's answers if
absent, reconciles rice-owned state — migrating away a 0.1.3-era
project-scope plugin declaration if this repo still carries one
(RICE-FIX-001) — and runs the canonical flow — verify, deterministic repair
on a FAIL, verify again — printing the verifier's own lines.

Do not run anything else first. Do not analyse the repo, hand-edit files,
or try commands before this command has spoken: you are the last step in
the flow, never the first.

## 3 — Pass its words on

The output ends with a block marked

```
--- present this to the OWNER verbatim ---
```

Reply with exactly that block's content and **nothing after it**. It is the
whole reply: no settings detail, no RTK line, no list of missing artifacts,
no commit counts, no summary of what bootstrap did. Whatever it says is
what this repo actually needs — a Matt hand-off, an install the OWNER must
do, a verdict to quote, or the remaining blockers.

Then do only what that block asks of *you*: when it says to commit and push,
commit and push the changes in the working tree; when it lists blockers, fix
exactly what each line's detail says and run the same command again.

Two things are never yours, whatever happens:

- **Matt's setup.** Never run, simulate, or write its content — not a
  template, not a reconstruction. A missing Matt artifact is reported to the
  OWNER by name, and only
  `/mattpocock-skills:setup-matt-pocock-skills` can produce it.
- **A second invocation.** Never ask the OWNER to run `/rice:bootstrap`,
  say "bootstrap this repo with rice", or type any other continuation
  prompt. A fresh bootstrap costs the OWNER exactly one invocation of this
  skill (RICE-BOOTSTRAP-028).

**Never report bootstrap as complete unless the verdict says so.**

## Why there is no step 4

The continuation is automatic: `scripts/bootstrap_continue.py` is wired as a
`Stop` hook and asks the same engine, at the end of every turn, whether
there is anything to continue. It stays silent until the OWNER's Matt setup
has left something behind, then runs the same flow to the end and hands the
result to you — including, when repair could not restore an artifact
without guessing, exactly which one the OWNER must produce. Neither you nor
the OWNER runs that by hand, and an incomplete Matt setup is never left
silently pending (RICE-BOOTSTRAP-042).

Re-running this skill is safe but should never be *necessary*. If the OWNER
does invoke it again, just run the command in step 2: its output says where
this repo is.
