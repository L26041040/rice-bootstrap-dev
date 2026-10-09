# RICE-UPDATE-PRESAVE-001 — real-session evidence

Status: acceptance evidence for the update save (SPEC-001 I24, §14.8).
Date: 2026-10-09. Real interactive Claude Code 2.1.295 sessions, driven
through tmux, same method as `RICE-FINAL-UX-REVIEW-001-real-sessions.md`:
an isolated config per run, rice installed from a local git marketplace
(public `release/0.1.6`, then the candidate as 0.1.7) through the real
version cache, repos finalized by the real 0.1.6 and tracking a bare
origin on a pushed session branch. Auto permission mode; Sonnet 5.5.

## Found and fixed during the review

| Observed | Fix |
|---|---|
| A new session's update runs twice (the git-deny probe round). The second run saved rice's own half-written `.claude/rice.md` as a second "save work" commit and told the OWNER "committed your changes" about it. | Rice's own pending writes are given back before the commit (`reconcile.only_rices_change`); a re-run names the earlier save. The upgrade matrix now flags any commit a candidate's update makes. |

## Final build (`3392ca0`), each case one real `/rice:bootstrap`

| Case | Questions | Result |
|---|---|---|
| Dirty repo (edit, new file, ignored `.log`) | 0 | One save commit (`README.md`, `notes.md`), pushed; then rice's own commit (`rice.md`, `rice-version`); `debug.log` never staged; `FINAL-VERIFIED 0.1.7`. |
| Clean repo in sync | 0 | No save commit; `FINAL-VERIFIED 0.1.7`. |
| Unpushed local commit | 0 | Pushed, no save commit. The OWNER is not told when that push happened in the probe round's first run (see below). |
| Push rejected by origin | 0 | Stopped: git's own reason shown, work committed locally, nothing of rice's written, version still 0.1.6. After the OWNER fixed origin, the re-run pushed that commit (no second save) and finalized. |
| Same-version re-run with new work | 0 | No commit; the new file left uncommitted. |
| Installed 0.1.8, session still 0.1.7 | 0 | Refused on `plugin:build` before any save; HEAD, origin and the work untouched. |
| Repo finalized by 0.1.8, session 0.1.7 | 0 | Refused on `rice-version:not-newer` before any save; untouched. |
| First install with work in the tree | 2 (one prompt) | No save commit; the work left as it was, beside rice's files, at the Matt hand-off. |

## Known limits

- When the only thing saved is a push of earlier commits, and it happened in
  the first run of a probe round, the re-run's report does not mention it
  (nothing records it; the push itself is done).
- A repo with no remote is not updated until one is added.
- A 0.1.6 session has no save step (and no anti-downgrade item).
