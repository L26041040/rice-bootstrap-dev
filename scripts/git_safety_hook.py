#!/usr/bin/env python3
"""Ticket #17: the one narrow PreToolUse hook Git safety needs.

Native permission `deny` rules (see settings_template.py, ticket #15)
handle every destructive git operation and every branch-creation form
that carries a distinguishing FLAG. The one thing permission patterns
cannot express reliably is bare `git branch <name>`: a positional
argument, not a flag, decides whether this creates a branch or just
lists/inspects them, and Claude Code's own permission-matching is
documented as fragile for exactly this class of case (see #13's
"Git safety" section). This hook's job is exactly that one check --
nothing broader. Every other git subcommand is out of scope here.

Wired as a PreToolUse hook (matcher: Bash) in the plugin's hooks.json.
Reads the standard PreToolUse payload on stdin and writes a permission
decision to stdout, per Claude Code's hook protocol.
"""
import json
import re
import sys

# Recognized "this is inspection/listing/deletion/rename, not creation"
# flags for `git branch`. Deletion/rename (-d/-D/-m/-M) are allowed BY
# THIS HOOK -- -D is separately denied by the native permission rule in
# #15; -d/-m/-M aren't destructive enough to deny at all. This hook only
# decides creation vs. everything else.
_NON_CREATING_BRANCH_FLAGS = re.compile(
    r"^(-a|-r|-v|-vv|--list|--all|--remotes|--show-current|--help|"
    r"-d|-D|-m|-M|--delete|--move)"
)


def decide(command: str) -> str:
    """"allow" or "deny" for a raw shell command string."""
    m = re.match(r"^\s*git\s+branch(\s+(.*))?\s*$", command)
    if not m:
        return "allow"  # not `git branch` at all -- not this hook's concern
    rest = (m.group(2) or "").strip()
    if not rest:
        return "allow"  # bare `git branch` -> listing
    if _NON_CREATING_BRANCH_FLAGS.match(rest):
        return "allow"
    if rest.startswith("-"):
        # An unrecognized flag: not a bare name, so not the creation case
        # this hook exists to catch. Leave it be -- native permission
        # rules or the platform's own error handling cover anything else.
        return "allow"
    return "deny"  # bare positional name -> creates a branch


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "")
    decision = decide(command)
    if decision == "deny":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "rice: bare 'git branch <name>' creates a branch. "
                    "Claude may not create branches on its own -- ask the "
                    "OWNER to create it, or use an existing branch."
                ),
            }
        }))
    # Silence (no output) = allow; nothing to add for the allow case.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
