---
name: rice-uninstall
description: "Remove rice from THIS repo cleanly, on the OWNER's explicit request only (e.g. 'uninstall rice', '移除 rice-bootstrap', '把 rice 從這個 repo 拿掉'). Exact subtraction of rice-owned state, mechanically checked; project data, session history, Matt artifacts and the user-level plugin are never touched. Never trigger on vague or future-tense talk about removing rice."
---

# rice-uninstall

Trigger rule: the OWNER expresses **explicit, present** intent to remove
rice from this repo. Wondering whether to remove it later is not a trigger;
answer the question, run nothing.

This skill operates one script. It decides nothing:

```
python3 "<plugin root>/scripts/uninstall.py" <repo_root>
```

The script snapshots the project-owned view of the repo, removes rice's
state by exact subtraction, then checks the result. Only its last line
`RICE-UNINSTALLED` means the repo is clean. `RICE-UNINSTALL FAIL (...)`
lists what is still there; your own reading of the tree counts for nothing.

## Flow

1. Confirm the trigger (explicit, present, this repo). Then run the script.
2. Quote its last line verbatim. On `RICE-UNINSTALLED`: commit and push the
   removal. On FAIL: report the listed items; do not hand-fix around the
   script and do not claim success.
3. **Stop here.** Plain repo-scoped intent ("移除 rice-bootstrap", "uninstall
   rice", "把 rice 從這個 repo 拿掉") means *this repo only* — do not
   proactively ask about removing the user-level `rice@rice-bootstrap`
   plugin or the `rice-bootstrap` marketplace. Other repos in this
   environment may still use them; that is the default assumption, not a
   question to raise (RICE-BOOTSTRAP-034: an earlier version of this
   skill asked this unconditionally after every repo uninstall — that was
   unnecessary and introduced cross-repo risk).

## Removing rice globally (a different, separate intent)

Only a separately explicit OWNER request — "remove rice globally",
"completely uninstall rice from this environment", "remove the user-level
rice plugin/marketplace", or equivalent, phrased distinctly from repo-scoped
uninstall intent — reaches this. It is not part of the flow above and is
never triggered by it. When it is the OWNER's actual request: confirm, then
run the official mechanism directly —
`claude plugin uninstall rice@rice-bootstrap` then
`claude plugin marketplace remove rice-bootstrap`. No wrapper skill or new
subsystem for this; those two commands are the whole mechanism.

## What the script removes (exact matches only)

`.claude/rice.md`; the `@.claude/rice.md` line in `CLAUDE.md` (the file
itself only if rice created it with nothing but that line); the
marker-delimited rice pointer block in `AGENTS.md` (the file itself only if
rice created it with nothing but that block);
`.claude/rice-version`; `WAITING_FOR_MATT`; the `rice-bootstrap`
marketplace entry; `enabledPlugins.rice@rice-bootstrap`;
`env.RICE_SESSION_ARCHIVE`; `env.CLAUDE_CODE_SUBAGENT_MODEL` only while it
still holds the `sonnet` rice used to write there before that pin was
retired (RICE-BOOTSTRAP-046); one occurrence of each of rice's own
`permissions.deny` rules, so a repo that already had the same rule keeps its
own copy in its own position (an entry already gone is a no-op, and an entry
rice never declared is the project's); `outputStyle` only when it is exactly
`rice:老弟`. Containers rice
emptied are dropped; `.claude/` stays whenever anything project-owned is
left in it.

## What it never touches

`session-history/**`, custom output styles and any other `outputStyle`,
`permissions.allow` / `permissions.ask`, deny rules rice did not add, other
plugins and marketplaces, project env vars and hooks, project-owned
`AGENTS.md` content, Matt artifacts (`docs/agents/**`, CLAUDE.md sections),
RTK, every other repo file, git
history, and the user-level plugin install (see step 3).

Rice only removes rice. If the repo had a different `/clear` archive
implementation before rice, that is the OWNER's to handle.
