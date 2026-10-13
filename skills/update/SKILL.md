---
name: update
description: "Update rice and this repo to the latest release in one command: refreshes the marketplace, updates the plugin, then brings this repo up to date with the new build, saving the OWNER's work first. OWNER-invoked only."
disable-model-invocation: true
---

# rice update

You are the adapter for one command; every decision is `scripts/update.py`'s.

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

## 2 — Finish what it asks of you, then report

The output ends with a block marked

```
--- present this to the OWNER verbatim ---
```

It may ask *you* to commit and push (by name, as it says), or to run the
probe it names and then the same command again: do that **first**. Then
reply with your conversation number and exactly one fenced code block,
**nothing outside it**, holding the block's facts for the OWNER — the plugin
lines, the verdict line, anything it names as left for them — plus what you
committed and pushed. Leave out its instructions to you.

Never report an update the block does not say happened, and never type
`/reload-plugins` or `/rice:bootstrap` for the OWNER: both are theirs.
