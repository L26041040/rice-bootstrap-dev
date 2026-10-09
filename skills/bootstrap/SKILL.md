---
name: bootstrap
description: "One-instruction bootstrap for a repo: installs rice's own configuration (git safety, session archiving, an OWNER-chosen output style, the OWNER's working rules), runs the mechanical verifier, then hands off to the OWNER for official Matt Pocock setup. Safe to re-run on an already-bootstrapped repo."
---

# rice bootstrap

You are the adapter for one command. Every decision belongs to
`scripts/rice.py`. Your job: collect the answers it still needs, run the
command, pass its words on.

## 1 — Ask the OWNER's choices still unanswered

```
python3 "<plugin root>/scripts/rice.py" questions <repo_root>
```

It prints the choices this repo has not answered yet, one per line
(`archive`, `output-style`). Ask exactly those, with the native choice UI
(`AskUserQuestion`), batched into one prompt. If it prints nothing, ask
nothing and run step 2 with no flags.

- **Session archive** — Enabled (recommended) → `--archive 1`; Disabled →
  `--archive 0`. A repo with its own `/clear` archive hook: the OWNER removes
  that hook first.
- **Output style** — offer exactly:
  1. `rice:老弟` (recommended) → `--output-style "rice:老弟"`
  2. Claude default → `--output-style none` (the key stays absent; never a
     placeholder value)
  3. An existing style → options are exactly what
     `python3 "<plugin root>/scripts/rice.py" styles <repo_root>` prints
  4. A custom style → the OWNER describes it; write a valid output style to
     `.claude/output-styles/<name>.md` (frontmatter `name`, `description`,
     then the body), then `--output-style <name>`. Rice never rewrites it.

Cannot ask → omit the flag. Never guess.

## 2 — Run it

```
python3 "<plugin root>/scripts/rice.py" bootstrap <repo_root> \
    [--archive 0|1] [--output-style <name>|none]
```

Run nothing else before it: no repo analysis, no hand edits, no probing
commands.

On an update (this repo set up by an older rice), the command itself first
commits every change in the working tree (`.gitignore` applies) and pushes
the current branch, unpushed commits included: the OWNER running this skill
to update is their go-ahead, so ask nothing. Do not stage, commit, stash or
push anything yourself before it. If saving fails, the command stops with
nothing of rice's written; pass its words on.

## 3 — Pass its words on

The output ends with a block marked

```
--- present this to the OWNER verbatim ---
```

Reply with exactly that block and **nothing after it** — no settings detail,
no RTK line, no artifact list, no commit counts, no summary. Then do only
what it asks of *you*: commit and push when told; fix exactly what a blocker
line names and run the same command again.

Never:

- **Run, simulate, or write Matt's setup.** A missing Matt artifact is
  reported to the OWNER by name; only
  `/mattpocock-skills:setup-matt-pocock-skills` produces it.
- **Ask for a second invocation.** A fresh bootstrap costs the OWNER
  exactly one invocation of this skill. The continuation is automatic:
  `scripts/bootstrap_continue.py` runs as a `Stop` hook and finishes the flow
  once Matt's setup has landed.
- **Report bootstrap complete unless the verdict says so.**
