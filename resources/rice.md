## Control hierarchy

When deciding how to enforce a rule, ask in order: can this be a **Setting**
(native config Claude Code already reads)? Otherwise a **Permission** rule
(settings.json allow/deny/ask)? Otherwise a deterministic **Skill/Script**?
Otherwise does it need a **Hook** (lifecycle interception)? Only if none of
those apply, and only if the rule is genuinely useful and can't simply be
deleted, does it become policy here. Once a rule is hard-enforced by
something to the left, its prose restatement does not belong in this file —
the mechanism's own configuration is the documentation.

## Workflow depth and delegation

Match workflow depth to the task's actual complexity and uncertainty. A
simple, well-understood task can go straight to implementation. A task can
go to `to-tickets` without first going through `to-spec` when that's
appropriate. Matt's skills (research, wayfinder, grilling, to-spec,
to-tickets, ...) are tools to reach for as needed, not a fixed sequence
every task must pass through — forcing small tasks through the whole
pipeline is as much a failure as skipping it for genuinely large ones.

Large or independent research should run in an isolated subagent context,
reporting back only a synthesis — not inline in the main conversation.

## Trust verification, don't assume it

`rice`'s `check` reports PASS, FAIL, or NOT VERIFIED for each item it
covers, plus a one-line summary. Never claim something is done, correct, or
installed without that report having actually run and said so. A false
claim of success is a quiet failure — it doesn't surface itself the way a
hard error does, so there's no substitute for actually checking.

## PROJECT_STATE.md

@../PROJECT_STATE.md

`PROJECT_STATE.md` describes the project's **current real state** — it is
not a history or a log. rice governs these rules; the project's own agent
owns the content. Update it when, and only when, one of these happens:

1. the phase or objective actually changes;
2. the next step actually changes;
3. a blocker appears or clears;
4. a ticket or milestone completes that changes where the project stands;
5. right before `/clear` or a formal handoff.

If nothing above has changed, leave it alone — never update it for the
sake of updating. Nothing enforces this mechanically; the file's whole
value (a new session orienting instantly) depends on it being true.
`rice-update` never rewrites existing content.
