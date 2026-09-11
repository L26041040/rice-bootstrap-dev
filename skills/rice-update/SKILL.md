---
name: rice-update
description: "The sole entrypoint for moving this repo to a newer rice-bootstrap release. OWNER-invoked only -- Claude cannot start this on its own, by design (see ticket #26 / RICE-BOOTSTRAP-016)."
disable-model-invocation: true
---

# rice-update

`disable-model-invocation: true`: the harness hides this skill from the
model and refuses programmatic invocation (confirmed against
`setup-matt-pocock-skills`, RICE-BOOTSTRAP-014, and observed for this skill
in #18's probes). Only an explicit OWNER-typed `/rice:rice-update` reaches
it.

You are the OWNER-facing adapter for one command. This skill holds no
lifecycle steps: what upstream is, whether this repo is already there,
whether the delivery ref must move, whether the install must be updated,
whether a new session is required before anything can be verified, and when
to converge and verify — all of that belongs to `scripts/rice.py`
(RICE-BOOTSTRAP-044B).

## Run it

```
python3 "<plugin root>/scripts/rice.py" update <repo_root>
```

Run it once, and run nothing else first — no upstream lookup, no install
command, no verifier. The command does whichever of those this repo
actually needs, and only that.

## Pass its words on

The output ends with a block marked

```
--- present this to the OWNER verbatim ---
```

Reply with exactly that block's content, then do only what it asks of
*you* — commit and push a ref change, report a failure, fix the blockers it
lists and run the same command again, or tell the OWNER to start a new
session and invoke `/rice:rice-update` there.

An update legitimately spans two sessions (ticket #19): a session keeps its
cached build until the install is updated, and only a new session loads the
new one, so verification in the old session would run the old build's
verifier against the new desired state. When the block says to start a new
session, that is why — say so plainly and stop. Do not try to continue in
this session, and do not describe the repo as "on" the new version until a
verdict says it is.

`.claude/rice-version` is never yours to write, in any step. Only the
verifier writes it, and only when its own verdict says so; on any failure it
is byte-for-byte unchanged, and nothing is "updated".

## What this deliberately does not do

No rollback, no migration framework, no git-history inference, no
transaction engine. No git tag or GitHub release exists anywhere in this
lifecycle (RICE-BOOTSTRAP-030): upstream identity is `main`'s exact commit
SHA, read by the command itself. A failed update is reported as failed with
the verifier's own list; fixing forward is the OWNER's call.
