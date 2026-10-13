# rice-bootstrap

Rice configures a repo for Claude Code in one command: git-safety permission
rules, a session archive, an output style, the OWNER's working rules, and a
hand-off to Matt Pocock's skill setup.

## Requirements

- Claude Code
- Matt Pocock skills (`claude plugin marketplace add mattpocock/skills`,
  `claude plugin install mattpocock-skills@mattpocock`)
- RTK (`rtk` on PATH)

## Install once

```
claude plugin marketplace add L26041040/rice-bootstrap
claude plugin install rice@rice-bootstrap
```

User scope, one time per machine. On Claude Code on the web, put these two
lines in the Cloud Environment's setup script (an OWNER-only step in the web
UI); every new session from that Environment then has the latest release.
To pin a version instead: `claude plugin marketplace add L26041040/rice-bootstrap@release/0.1.8`.
A pinned Environment stays on that release until you change the pin; unpinned
is the default and follows the latest release.

## Bootstrap a repo

```
/rice:bootstrap
```

Run it once per repo. It ends with `RICE-VERIFIED <version>` and a hand-off
to `/mattpocock-skills:setup-matt-pocock-skills`; after that setup, rice
finishes on its own and reports `FINAL-VERIFIED <version>`, recorded in
`.claude/rice-version`. Re-running is safe. Rice's working rules take effect
after `/clear` or in a new session.

To update, run `/rice:update` once: it refreshes the marketplace, updates the
plugin, then updates this repo with the new build, asking nothing. First it
commits and pushes your working tree (running it is your go-ahead; `.gitignore`
applies) and updates nothing if that save fails. The session's own hooks load
the new build after `/reload-plugins` or in a new session. Without
`/rice:update`: `claude plugin marketplace update rice-bootstrap`, `claude
plugin update rice@rice-bootstrap`, then `/rice:bootstrap` in a new session.

If Matt's setup chose GitHub Issues, rice creates Matt's triage labels before
`FINAL-VERIFIED`: that needs a valid GitHub `origin` and a credential allowed
to manage its issues (`GH_TOKEN`/`GITHUB_TOKEN`, `git` or `gh` signed in, or
the web session's GitHub connection). Without one rice stops and says so once;
`gh` itself is not required.

Work happens on the session's branch; say 「合併回主枝」 and the agent
fast-forwards it onto the default branch, proves it landed, then removes the
remote branch. Past sessions in `session-history/` stay closed until you
invoke `/rice:history`.

## Uninstall from a repo

```
/rice:rice-uninstall
```

Removes exactly what rice added to this repo; the plugin stays installed.

## What rice configures

- `.claude/settings.json`: `permissions.deny` rules against force-pushes,
  history rewrites and branch creation; `env.RICE_SESSION_ARCHIVE`;
  `outputStyle` (your choice, `rice:老弟` recommended)
- `.claude/rice.md`: the OWNER's working rules, imported by `CLAUDE.md`
  and pointed at from `AGENTS.md`
- `session-history/`: an archive of each session on `/clear`, when enabled

Everything a repo already had is left as it was.

## License

MIT
