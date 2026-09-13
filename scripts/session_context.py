#!/usr/bin/env python3
"""rice's SessionStart context (RICE-BRANCH-001): one line, only in a repo
rice has bootstrapped, telling the agent where the privileged remote-branch
deletion lives -- as the path of THIS loaded build, so an already
bootstrapped repo needs nothing re-run and the agent guesses no plugin path.
Silent in every other repo, and silent on any error: a context hook must
never disturb a session.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402

HELPER = Path(__file__).resolve().parent / "delete_remote_branch.py"


def context(repo_root) -> str:
    return (
        "rice: when the OWNER explicitly asks to delete a remote branch, do not use the denied "
        "`git branch` / `git push --delete`; run rice's privileged helper instead: "
        f'python3 "{HELPER}" "{Path(repo_root).resolve()}" <branch> -- only the branch the OWNER named.'
    )


def _cwd() -> Path:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        payload = {}
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return Path(cwd) if cwd else Path.cwd()


def main() -> int:
    try:
        repo_root = _cwd()
        if HELPER.exists() and reconcile.rice_installed_here(repo_root):
            print(context(repo_root))
    except Exception:  # noqa: BLE001 - never disturb a session
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
