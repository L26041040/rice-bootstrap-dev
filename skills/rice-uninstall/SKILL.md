---
name: rice-uninstall
description: "Remove rice from THIS repo cleanly, on the OWNER's explicit request only (e.g. 'uninstall rice', '移除 rice-bootstrap', '把 rice 從這個 repo 拿掉'). Exact subtraction of rice-owned state, mechanically checked; project data, session history, Matt artifacts and the user-level plugin are never touched. Never trigger on vague or future-tense talk about removing rice."
---

# rice-uninstall

Trigger: the OWNER's **explicit, present** intent to remove rice from this
repo. Wondering about it later is not a trigger — answer, run nothing.

```
python3 "<plugin root>/scripts/uninstall.py" <repo_root>
```

The script decides everything. Only its last line `RICE-UNINSTALLED` means
the repo is clean; `RICE-UNINSTALL FAIL (...)` lists what is still there.

1. Confirm the trigger, run the script.
2. Quote its last line verbatim. On `RICE-UNINSTALLED`: commit and push.
   On FAIL: report the listed items; never hand-fix around the script,
   never claim success.
3. **Stop.** Repo-scoped intent means this repo only. Do not ask about the
   user-level `rice@rice-bootstrap` plugin or the `rice-bootstrap`
   marketplace — other repos may still use them.

## Removing rice globally

A separate, separately explicit request ("remove rice globally", "remove the
user-level rice plugin"). Never triggered by a repo uninstall. Confirm, then
run `claude plugin uninstall rice@rice-bootstrap` and
`claude plugin marketplace remove rice-bootstrap`.

## Scope

Removed (exact matches only): `.claude/rice.md`; the `@.claude/rice.md`
line in `CLAUDE.md` and the pointer block in `AGENTS.md` (either file itself
only if rice created it with nothing else); `.claude/rice-version`; the
pending marker; a 0.1.3-era project-scope plugin declaration if still
present; `env.RICE_SESSION_ARCHIVE`; one occurrence of each of rice's own
`permissions.deny` rules; `outputStyle` only when exactly `rice:老弟`.

Never touched: `session-history/`, custom output styles, `permissions.allow`
/ `ask`, deny rules rice did not add, other plugins and marketplaces,
project env vars and hooks, project-owned `CLAUDE.md` / `AGENTS.md` content,
Matt artifacts, RTK, git history, the user-level plugin install.
