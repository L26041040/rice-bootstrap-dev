# RICE-BOOTSTRAP-UX-FIX-001 — real-session evidence

Status: acceptance evidence for the first-bootstrap fixes (SPEC-001 I25,
I26, §16). Date: 2026-10-12. Real interactive Claude Code 2.1.296 sessions
(Sonnet 5.5, auto permission mode), driven through tmux, same method as
`RICE-FINAL-UX-REVIEW-001-real-sessions.md`: an isolated config per build,
rice installed from a local git marketplace holding the candidate export,
Matt's plugin from its local clone, and a fresh git repo per run whose
`origin` fetches from GitHub and pushes to a local bare repo. The session
PATH had Claude Code on the web's `gh` client removed.

## Runs

| Build | Repo / origin | Observed |
|---|---|---|
| `7c34109` | fresh, `L26041040/rice-bootstrap-dev` | Matt chose GitHub with the default vocabulary. Repair created `needs-triage`, `needs-info`, `ready-for-human` from Matt's file, with Matt's descriptions (14 → 17 labels); the two already there were left alone. `FINAL-VERIFIED 0.1.7`. Bootstrap report, Matt summary and finalization each a conversation number plus one code block. |
| `7c34109` | same repo, new session | 0 questions, `FINAL-VERIFIED`, still 17 labels, one code block. |
| `753a781` | fresh, `L26041040/rice-bootstrap-dev` | Three formal reports, each one code block; labels already present, nothing created (17). The shipped `matt:labels` item, run with no `gh` on PATH: `PASS -- all 5 triage labels present`. |
| `753a781` | fresh, `octocat/Hello-World` (no access) | GitHub answered 403: nothing created, the version file not written, the finalization one code block naming the three ways to grant access, once. **But the Matt summary was written outside a code block.** Defect, fixed in `1d19419`. |
| `1d19419` | two fresh repos, `L26041040/rice-bootstrap-dev` | Both runs: bootstrap report, Matt summary and finalization each a conversation number plus one code block; `FINAL-VERIFIED 0.1.7`, committed and pushed; still 17 labels. The new UserPromptSubmit context fired on the Matt command only, never on `/rice:bootstrap`. |
| `1d19419` | repo finalized by the real 0.1.6, uncommitted work | 0 questions; save commit (`README.md`, `notes.md`), then rice's commit (`rice.md`, `rice-version`); one code block; `FINAL-VERIFIED 0.1.7`. |

## Found and fixed during the review

| Observed | Fix |
|---|---|
| The agent's summary of Matt's setup ignored rice's output rules (one run in three), though the bootstrap command had handed them over at the hand-off. | While the bootstrap waits for Matt, a UserPromptSubmit hook (`session_context.py --prompt`) adds the rules to each OWNER prompt; silent at any other time. |

## Not verified here

- A session with no `gh` at all: Claude Code on the web puts its own `gh`
  client back on the agent's PATH. Rice never called it (the transcripts
  show no `gh` call; the credential was GH_TOKEN), and the unit tests pin
  the path with `gh` absent.
- Label creation in a real session on the final build: the labels already
  existed on the only repository this session may write to, and deleting
  them was not done. Creation was observed on `7c34109`; since then only
  where the API base comes from and how a 422 is read changed, pinned by
  `tests/test_github_labels.py` (including a full GitHub-tracker lifecycle).
- Interim progress lines the agent writes between tool calls are not
  formal reports and appear outside code blocks; Matt's draft for approval
  is Matt's own interaction.
