# RICE-UPDATE-RESEARCH-001 — `/rice:update` update lifecycle research

Status: research only. No spec, no tickets, no code changes. Date: 2026-10-07.
Base: rice 0.1.6, plus the unreleased RICE-SPEC-001 work on `claude/rice-bootstrap-public-release-tl5fa2`.
Local CLI: Claude Code 2.1.292.

Conclusion: **SIMPLE UPDATE PATH EXISTS.** Updating means the platform updates the plugin, a
session boundary passes (or the OWNER runs `/reload-plugins`), and the OWNER re-runs the existing
`/rice:bootstrap`. That last step is already the repo update: a probe confirmed it (§3). It needs
zero new code primitives. What remains is whether to add a downgrade guard and a staleness hint,
plus one line of documentation (§7).

Source abbreviations:

- [LOAD] = https://code.claude.com/docs/en/plugins/loading.md
- [CLI] = https://code.claude.com/docs/en/plugins/cli-reference.md
- [HELP] = local `claude plugin update --help` / `claude plugin marketplace update --help`
- [CODE] = this repo's code, cited by file and function
- [PROBE] = the scratch probe in this round (§3); it is not committed

---

## 1. Official primitives

| Fact | Source |
| --- | --- |
| `claude plugin update <plugin>` updates to the latest version the marketplace offers. "The new version loads in your next session, or after you run `/reload-plugins` in a running one." | [CLI] §plugin update |
| CLI help: "Update a plugin to the latest version (restart required to apply)". | [HELP] |
| With `name@marketplace`, install and update refresh that marketplace before the lookup. This does not depend on autoUpdate or `DISABLE_AUTOUPDATER`. | [LOAD] §When Claude Code refreshes a marketplace before an install |
| The version is resolved in this order: manifest `version` > marketplace entry `version` > source SHA. Once a manifest pins `version`, nothing moves until the string changes, no matter how many commits are pushed ("already at the latest version"). | [LOAD] §How Claude Code computes the version |
| The cache directory is named by version: `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. The old version gets an `.orphaned_at` marker and is deleted after 14 days, "so a session that already loaded the old version keeps running." | [LOAD] §Cleanup of previous versions |
| Auto-update: in an interactive session, after a random delay of up to 10 minutes it refreshes marketplaces that have auto-update on. "The running session keeps the versions it loaded … the new versions load on your next launch." Third-party marketplaces have it off by default. | [LOAD] §When auto-update runs; Matt README "off by default for marketplaces outside Anthropic's" |
| After a mid-session update, hooks, MCP and LSP keep the old path until `/reload-plugins` switches hooks, skills, MCP and LSP to the new path. | [LOAD] §When a copied plugin updates mid-session; [CLI] §/reload-plugins |
| `/reload-plugins` is a slash command the OWNER types. It also works in `-p` and the desktop app (v2.1.260+). | [CLI] §/reload-plugins |
| A marketplace added with `owner/repo@ref`, after `marketplace update`, "updates to the latest commit of that ref, not the repository's default branch". | [CLI] §plugin marketplace update, add source table |
| `${CLAUDE_PLUGIN_DATA}` survives updates. | [LOAD] §Find plugins on disk |
| Not documented: install, update or migration lifecycle hooks; whether a plugin can safely run `claude plugin update` on itself from inside a session. | (subagent search of official docs) |

What this means for Rice:

- **[CODE]** Rice's `plugin.json` pins `version`, and the marketplace source is `"./"` (a relative path inside the git-hosted marketplace). So `plugin update` sees a new release only when the version is bumped. `release.py` already forces a bump: version consistency is enforced, and publishing a `release/<v>` that already exists fails closed.
- **[CLI]** A `@release/x.y.z` pin points at an immutable branch, so a `marketplace update` stays on that version forever. Changing the pin means re-adding the marketplace with a new ref. Whether that requires `marketplace remove` first is **unverified**.
- **[README]** Cloud: the setup script installs the plugin on every new container. If it is not pinned, it follows public `main`, so every new session already runs the latest release. In Cloud the step "update the plugin" does not exist; the platform has already done it.

## 2. Matt / mature pattern

Matt's skills (local cache 1.3.1):

- **There is no update command.** Code updates are left entirely to the platform marketplace ("a managed, read-only bundle that updates when Anthropic's marketplace picks up my releases"). README: "Stuck on an old version? `claude plugin list` …"
- **The per-repo setup (`setup-matt-pocock-skills`) is idempotent.** If an `## Agent skills` block already exists, "update its contents in-place rather than appending a duplicate"; "re-running this skill is only necessary if they want to switch issue trackers or restart from scratch." (SKILL.md L82, L116)

**The mature pattern:** the platform updates the code, and the repo-level configuration is idempotent setup that is simply run again. Nobody writes an updater. Rice already has both halves: user-scope install, and a `/rice:bootstrap` that is "Safe to re-run" (skills/bootstrap/SKILL.md).

## 3. Rice current-state compatibility

**[PROBE]** Steps:

1. Build a FINAL-VERIFIED repo.
2. Simulate one finalized by an older build: replace `.claude/rice.md` with old content and set `.claude/rice-version` to `0.1.5`.
3. Re-run `rice.bootstrap(repo)` with the current build, without passing any OWNER answers.

Results:

```
re-run stage: finalized | verdict: FINAL-VERIFIED 0.1.6
rice.md restored: True              (repair: rice.md: updated from canonical state)
rice-version: 0.1.6 | build: 0.1.6  (version evidence restamped)
marker left: False
settings unchanged: True | CLAUDE.md unchanged: True
```

Then simulate "installed 9.9.9 but this session runs 0.1.6":

```
stage: blocked | plugin:build: installed ['9.9.9'] but this session is running build 0.1.6 -- they must be the same build
rice-version after mismatch: 0.1.6   (untouched)
```

So the flow the OWNER sketched maps onto existing parts:

| Sketched step | Existing equivalent | Reusable? |
| --- | --- | --- |
| refresh marketplace + update plugin | Cloud: the setup script, unpinned. Local: `claude plugin update rice@rice-bootstrap`, which refreshes the marketplace itself | Yes, platform primitive |
| use the new reconcile/repair | `/rice:bootstrap` → `reconcile.apply` (rice.md is "overwrite-on-update") → `_pipeline` repair | Yes, **but only after the session switches to the new build** |
| verify | `verify.run(final=True)` (Gate B) | Yes |
| update version evidence | `verify._stamp`, written only on a Gate B pass | Yes |

**Can it run in one session? No.** The new code does not load mid-session [LOAD][CLI]. `plugin:build` also deliberately blocks the case "installed ≠ running" [CODE evidence.py `_plugin_items`]. So after the update there must be a new session, or an OWNER-typed `/reload-plugins` (the docs say it switches skills and hooks; **not probed with Rice**).

Bootstrap-specific behaviour to watch in a re-run (all from [CODE]):

1. **The two OWNER questions.** `write_owner_choices` writes only absent keys, so a re-run does not overwrite anything. One exception: a repo that chose "Claude default" has no `outputStyle` key. If the adapter asks again and the OWNER answers differently, the new answer gets written. That is not a migration problem, only an extra question.
2. **The waiting marker is written briefly.** `enter_waiting_for_matt` writes it after Gate A passes; a Gate B pass removes it. If Gate B fails (RTK missing, Matt artifacts gone), the repo is left in this state: new rice.md + marker + old rice-version. From then on the Stop hook retries Gate B every turn.
3. **`git-deny:behaves`** needs a denial record in this repo's transcripts. A new Cloud container has no older transcripts, so a re-run first returns a blocker that asks the model to run `git branch --show-current`. That costs one extra round, the same as a fresh bootstrap.
4. **Matt hand-off conflicts with the guard.** If a finalized repo has lost its Matt artifacts, re-bootstrapping hands off to Matt. Because `.claude/rice-version` exists, Matt's write to CLAUDE.md is denied by the guard. This is the consequence spec C5 already accepts: the OWNER edits it directly.
5. **apply runs before `plugin:build`.** `bootstrap()` writes `reconcile.apply` after checking only `plugin:installed` and the scope; `plugin:build` is checked in Gate A after that (rice.py `bootstrap`). An old-build session therefore first writes the old-build rice.md into the repo, then blocks.

Nothing else needs migrating: hooks, the guard, and the History Skill are all plugin-level, so they take effect as soon as the plugin version switches. No repo state was added (RICE-SPEC-001 I12).

## 4. Minimal candidate flow

```
(A) The plugin goes to the new version (platform primitive; Rice writes nothing)
    Cloud, unpinned:  nothing to do. A new session is a new container and installs the latest.
    Cloud, pinned:    OWNER changes @release/x.y.z in the setup script (OWNER-only UI).
    Local machine:    claude plugin update rice@rice-bootstrap
                      (pinned: re-add the marketplace at the new ref first)
(B) Cross a session boundary: new session, or the OWNER types /reload-plugins
(C) In each repo to update: /rice:bootstrap (existing; asks nothing on a re-run)
    → apply + repair → Gate B → FINAL-VERIFIED <new> restamps .claude/rice-version
```

The minimum new primitives are **zero lines of code**: one README line saying "更新 = 新 session 後重跑 `/rice:bootstrap`".

If the OWNER wants a command named `/rice:update`, it could at most be a prose alias of bootstrap. It is not needed. It would also conflict with the code comment "There is no update verb (RICE-FINAL-001)" (rice.py docstring), so the OWNER would have to amend RICE-FINAL-001.

## 5. What NOT to build

- **Do not let rice run `claude plugin update` on itself.** It is undocumented [LOAD]. It cannot take effect in the current session, because a restart or an OWNER-typed `/reload-plugins` is required. In Cloud the update dies with the container, and the setup script already does it. The `plugin:build` gate would block the reconcile in that same session anyway.
- Do not build an updater state machine, migration scripts, a version comparison table, or an "update pending" marker. Rice.md is overwritten whole-file and repair already converges drift.
- Do not use install or update lifecycle hooks; officially they don't exist.
- Do not write `${CLAUDE_PLUGIN_DATA}` state. Nothing needs to be remembered.
- Do not build a scanner that bulk-updates multiple repos. Each repo is updated when the OWNER re-runs bootstrap in it; this matches the RICE-SPEC-001 I3 spirit (no background cleanup).
- Do not build a rollback mechanism. Release branches are immutable, so rollback means pinning back to the old `release/x.y.z` and re-running bootstrap.

## 6. Failure modes (facts, no design)

| Situation | What happens today | Source |
| --- | --- | --- |
| Update fails midway (network, marketplace refresh) | `install` reports `marketplace not refreshed` and continues from the cached catalog; `update` stays on the old version. The repo is untouched. | [LOAD] |
| Plugin updated, repo not re-bootstrapped | Hooks, guard and History Skill are already new. Repo rice.md and rice-version stay old. Nothing fails automatically, because nothing runs verify on its own. **There is no signal that the repo is stale.** | [CODE] session_context.py, rice.py |
| Re-bootstrap's Gate B fails | rice.md is new, rice-version is old, the marker remains, and the Stop hook retries Gate B every turn. The blocker directive names what is missing. | [CODE] verify `_stamp`, rice `continue_after_matt` |
| Repo is new, plugin is old (another machine, or a Cloud environment pinned to an old release) | Nothing happens automatically. If someone re-runs bootstrap with the old plugin, Gate B can pass (installed == running): **rice.md is silently downgraded and rice-version is restamped to the old version.** | [CODE] rice `bootstrap`, verify `_stamp` |
| Rollback | Pin the old `release/x.y.z`, reinstall, re-bootstrap. This is the same path as the previous row; no new mechanism is needed. | [README] [CODE] release.py |
| Plugin is updated while the session runs the old version | The session keeps using the old cache (the old directory is kept for 14 days). Running bootstrap in it → `plugin:build` FAIL, blocked, rice-version untouched. Before that, apply has already written the old-build rice.md (§3-5). | [LOAD] [PROBE] [CODE] |
| Multiple repos | The plugin is user scope and installed once per machine [LOAD §7]. One update changes the plugin-level behaviour of every repo at once. Each repo's rice.md and rice-version lag behind independently until it is re-bootstrapped. Repos do not affect each other. | [LOAD] [CODE] |
| Private/public source | Users only install the public `L26041040/rice-bootstrap`. The private dev repo is used via `--plugin-dir` and never takes part in updates. Every publish forces a new version and a new immutable branch, so "same version, different content" cannot happen. The only trap: if a machine also adds the private repo as a marketplace, its marketplace name is also `rice-bootstrap` and would collide (not probed). | [CODE] release.py, .claude-plugin/marketplace.json |

## 7. Remaining OWNER decisions

1. **Do we need a command named `/rice:update` at all?** Recommendation: no. Use one README line ("新 session 後重跑 `/rice:bootstrap`"); RICE-FINAL-001 then stays as it is. If the OWNER wants the name, RICE-FINAL-001 has to be amended explicitly.
2. **Downgrade protection.** Should bootstrap refuse to restamp when `rice-version` is newer than the running build (§6 row "repo is new, plugin is old")? It would be one comparison and adds no state. Recommendation: yes, it is the only real data regression found. Moving `plugin:build` ahead of apply (§3-5) addresses the same root cause.
3. **Staleness hint.** When `rice-version` ≠ build, should the existing SessionStart context add one line, "this repo is at x, run /rice:bootstrap to bring it to y"? The context hook is currently required "MUST NOT grow" (RICE-SPEC-001 §9). Doing this would need the OWNER to change that rule.
4. **Whether the Cloud setup script stays unpinned** (it follows main: the latest every session) or gets pinned (manual bump). This only affects step (A), not the design.
