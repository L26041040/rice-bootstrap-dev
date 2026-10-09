# RICE-FINAL-UX-REVIEW-001 — real-session evidence

Status: acceptance evidence for 0.1.7. Date: 2026-10-09. These are
observations from real interactive Claude Code sessions, not unit tests; the
tests that pin each fix cite this note.

## Method

- Claude Code 2.1.295, interactive TUI driven through tmux (keystrokes in,
  screen and session transcript out). Default model Sonnet 5.5; manual
  permission mode for the first install, auto mode elsewhere.
- Each scenario had its own `CLAUDE_CONFIG_DIR`, so the machine's real
  user-level plugins were never touched.
- Marketplaces: the public `release/0.1.6` tree and the 0.1.7 candidate
  export, served as a git repository over local smart HTTP, so installs and
  updates went through the real version cache. Matt Pocock's marketplace
  from its local clone. RTK on PATH.
- Repos: a fresh git repo with a bare origin, and repos finalized by the
  real 0.1.6 build (the upgrade matrix's bare and rich projects).

## Observations

| # | Scenario | Observed |
|---|---|---|
| 1 | `marketplace add` + `plugin install` | 0.1.7 installed at user scope. |
| 2 | First `/rice:bootstrap` | One batched native choice prompt (archive, style); git-deny probe denied by the real deny rule; `RICE-VERIFIED 0.1.7` and the Matt hand-off, the Matt command offered as the next prompt. |
| 3 | Matt setup, then the Stop hook | Continued on its own to `FINAL-VERIFIED 0.1.7`, committed and pushed. Claude Code labels the continuation "Stop hook error" (exit 2 is how a Stop hook continues). |
| 4 | Rules in the bootstrapping session | `.claude/rice.md` NOT loaded in the session that wrote it; loaded after `/clear`. Nothing told the OWNER. **Defect, fixed.** |
| 5 | CLAUDE.md edit request | Denied by the guard; the agent told the OWNER to edit it by hand. |
| 6 | `/clear` | Session archived to `session-history/`, `INDEX.md` updated, committed and pushed by the sync. |
| 7 | History gate | Read denied by the guard even after the OWNER said "allowed this time"; open after `/rice:history`; closed again after `/clear`. |
| 8 | "收尾吧" / 「合併回主枝」 | Vague wrap-up: no merge, the agent asked. Explicit phrase: ff-only merge, push, `merge-base --is-ancestor` proof, remote branch deleted by the helper (which failed closed while origin's HEAD was unreadable). `git branch --show-current` is denied by design. |
| 9 | 0.1.6 → 0.1.7, then `/reload-plugins` | `claude plugin update` alone answered "already at the latest version"; after `claude plugin marketplace update rice-bootstrap` it updated. After `/reload-plugins` the 0.1.7 code ran and finalized, project data intact. |
| 10 | Re-run on a configured repo | Both questions asked again; the "Enabled" answer silently dropped (archive stayed `0`). **Defect, fixed.** |
| 11 | Installed 0.1.8, session still 0.1.7 | Blocked on `plugin:build`, zero bytes written. |
| 12 | 0.1.7 session on a repo finalized by 0.1.8 | Blocked on `rice-version:not-newer`, zero bytes written. |
| 13 | New session in a stale repo | Staleness line injected as context; no agent passed it on (neither the original wording nor one telling the agent to tell the OWNER). A SessionStart `systemMessage` is shown in the session. **Defect, fixed.** |
| 14 | Up-to-date and non-rice repos | No visible prompt; only the helper line in context (none outside rice). |
| 15 | `/rice:rice-uninstall` on the rich repo | `RICE-UNINSTALLED`; only `rice.md` and `rice-version` removed; project settings, deny rules and files intact. |
| 16 | Bootstrap with the OWNER's unrelated uncommitted work | The agent ran `git add -A` and pushed that work inside rice's commit. **Defect, fixed.** |

## After the fixes (same method)

- Stale repo: the line shown at session start (`SessionStart:startup says:
  rice: this repo was finalized by rice 0.1.6; ...`).
- Re-run: `rice.py questions` printed nothing, zero `AskUserQuestion`
  calls, `FINAL-VERIFIED 0.1.7`, and the block said when the rules load.
- Fresh repo: both questions, one prompt.
- The README's two update commands, run verbatim, moved 0.1.6 to 0.1.7.
- Final build, stale repo with the OWNER's uncommitted work: the line shown,
  zero questions, `FINAL-VERIFIED 0.1.7`, only `.claude/rice-version` and
  `.claude/rice.md` committed and pushed, the OWNER's two changes left
  uncommitted and named.
- Final build, fresh repo through Matt's setup and the Stop hook:
  `FINAL-VERIFIED 0.1.7`, the eight rice and Matt files staged by name,
  committed and pushed, the rules-load sentence relayed, a clean tree.

## Not verified here

- GitHub-hosted install and Cloud Environment setup scripts (local git
  marketplace instead; the public repository is never touched by a review).
- The output-style "custom style" path and Matt's GitHub issue tracker path.
- A 0.1.6 session meeting a 0.1.7 repo: 0.1.6 has no anti-downgrade item, so
  it rewrites the repo back; the first protected transition is 0.1.7 → later.
