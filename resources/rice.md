# Working rules

Soft working rules the OWNER would otherwise repeat at the start of every
session. Anything already enforced by a setting, permission rule, script,
or hook is not restated here.

## Control hierarchy

Setting → Permission → Skill/Script → Hook → DELETE-or-Policy

- Prefer the leftmost, simplest, most reliable native mechanism that can do
  the job.
- A rule that is already enforced mechanically is not governed again in
  prose.
- If a requirement is not worth enough on its own, delete it. Do not add a
  framework for the sake of completeness.

## Subagent model

Subagents default to Sonnet: it is the right trade for search, review and
mechanical work. When a task genuinely needs a stronger model, name it out
loud when dispatching the subagent -- there is no approval gate and no
setting pinning it.

## Conversation numbering

Every reply starts with a sequential conversation number, e.g.
`［對話0001］`. The number keeps increasing across the same repo / workflow.

## Single-block output

When the OWNER asks for a formal work order, a work report, content meant to
be pasted to another agent, or a long list of questions, put the complete
content in one single code block. Do not split it into several cards, several
code blocks, or scattered output.

## Questions to the OWNER

Two different situations, two different channels — one rule doesn't cover
both:

- **Bounded configuration choices** (a small, fixed set of options — e.g.
  bootstrap's session-archive on/off, output-style pick): ask via Claude
  Code's native interactive choice UI (e.g. `AskUserQuestion`), batching
  multiple bounded choices into as few native prompts as the UI allows. A
  fixed option set doesn't need free-text parsing.
- **Long formal question sets** (a grill, wayfinder, a formal setup
  questionnaire, or any decision-making content meant to be copied
  elsewhere): list every question currently known in one complete code
  block so the OWNER can answer once. Not one question at a time, not card
  by card. Start another round only when an answer genuinely creates a new
  dependent question.

## First-use terminology

The first time a proper noun, acronym, or non-obvious technical concept
appears, add one short plain-language sentence: what it is, and what it is
used for in the current work. No need to repeat it afterwards.
