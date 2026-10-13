---
name: update
description: "Update rice and this repo to the latest release in one command: refreshes the marketplace, updates the plugin, then brings this repo up to date with the new build, saving the OWNER's work first. OWNER-invoked only."
disable-model-invocation: true
---

# rice update

You are the adapter for one command. Every decision belongs to
`scripts/update.py` and the lifecycle engine it runs.

## 1 — Run it

```
python3 "<plugin root>/scripts/update.py" <repo_root>
```

`<plugin root>` is the directory two levels above this skill's base
directory; `<repo_root>` is the git top-level of the working directory. It
can take a few minutes (a marketplace refresh, the update, then the repo):
give the command a timeout of 600000 ms and let it finish. Run nothing else
before it: no `git`, no `claude plugin ...`, no probing. It asks the OWNER
nothing and saves their work itself.

## 2 — Pass its words on

The output ends with a block marked

```
--- present this to the OWNER verbatim ---
```

Reply with your conversation number, then exactly that block inside one
fenced code block, and **nothing outside it**. Then do only what it asks of
*you*: commit and push when told; run the same command again after a
blocker line says what was fixed.

Never report the plugin or the repo updated unless the block says so, and
never type `/reload-plugins` or `/rice:bootstrap` for the OWNER: both are
theirs, and the block says whether either is needed.
