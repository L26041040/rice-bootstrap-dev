#!/usr/bin/env python3
"""rice's privileged remote-branch deletion (RICE-BRANCH-001).

Rice denies the agent `git branch *`, `git push --delete *` and `git push -d *`
outright, and keeps denying them. When the OWNER explicitly asks for a remote
branch to be deleted, this script is the one deterministic way to do it: the
agent runs it, and it -- not the agent -- issues the push.

    python3 <plugin root>/scripts/delete_remote_branch.py <repo_root> <branch>

Refuses: a name `git check-ref-format --branch` rejects; origin's default
branch; `main`; `master`; an origin whose default branch cannot be read.
Never forces, never touches a local branch -- the branch may well be the one
checked out locally; only its ref on origin goes. A branch already absent on
origin is a success. After the push, origin is read again and the branch
must be gone, or this exits non-zero.

Only the line `RICE-BRANCH-DELETED origin/<branch>` (or
`RICE-BRANCH-ABSENT origin/<branch>`) means it happened; anything else is a
refusal, with its reason on stderr and a non-zero exit.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REMOTE = "origin"
PROTECTED = ("main", "master")


class Refused(Exception):
    """Why nothing was deleted."""


def _git(repo_root, *args, timeout=120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, timeout=timeout)


def default_branch(repo_root) -> str | None:
    """origin's default branch, read from the remote itself (falling back to
    the local `origin/HEAD` symref); None when neither can say."""
    r = _git(repo_root, "ls-remote", "--symref", REMOTE, "HEAD")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "ref:" and parts[2] == "HEAD" and parts[1].startswith("refs/heads/"):
                return parts[1][len("refs/heads/"):]
    r = _git(repo_root, "symbolic-ref", "--quiet", f"refs/remotes/{REMOTE}/HEAD")
    prefix = f"refs/remotes/{REMOTE}/"
    if r.returncode == 0 and r.stdout.strip().startswith(prefix):
        return r.stdout.strip()[len(prefix):]
    return None


def remote_has(repo_root, branch: str) -> bool | None:
    """Does origin have refs/heads/<branch> right now? None when origin
    could not be read."""
    r = _git(repo_root, "ls-remote", "--heads", REMOTE, f"refs/heads/{branch}")
    if r.returncode != 0:
        return None
    return any(line.split("\t")[-1].strip() == f"refs/heads/{branch}"
               for line in r.stdout.splitlines() if line.strip())


def delete(repo_root, branch: str) -> str:
    """Delete refs/heads/<branch> on origin. Returns the one line that says
    what happened; raises Refused with the reason otherwise."""
    repo_root = Path(repo_root)
    r = _git(repo_root, "rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != "true":
        raise Refused(f"{repo_root} is not a git work tree")
    r = _git(repo_root, "check-ref-format", "--branch", branch)
    if r.returncode != 0 or r.stdout.strip() != branch:
        raise Refused(f"{branch!r} is not a valid branch name")
    if branch in PROTECTED:
        raise Refused(f"{branch!r} is protected; rice never deletes {' or '.join(PROTECTED)}")
    default = default_branch(repo_root)
    if default is None:
        raise Refused(f"could not read {REMOTE}'s default branch; deleting nothing")
    if branch == default:
        raise Refused(f"{branch!r} is {REMOTE}'s default branch; deleting nothing")
    present = remote_has(repo_root, branch)
    if present is None:
        raise Refused(f"could not read {REMOTE}; deleting nothing")
    if not present:
        return f"RICE-BRANCH-ABSENT {REMOTE}/{branch} (already gone; nothing to delete)"
    r = _git(repo_root, "push", REMOTE, "--delete", branch)
    if r.returncode != 0:
        raise Refused(f"git push {REMOTE} --delete {branch} failed: {(r.stderr or r.stdout).strip()}")
    if remote_has(repo_root, branch) is not False:
        raise Refused(f"{REMOTE}/{branch} is still there after the delete, or {REMOTE} could not be re-read")
    return f"RICE-BRANCH-DELETED {REMOTE}/{branch}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice: delete one branch on origin, on the OWNER's explicit request")
    ap.add_argument("repo_root")
    ap.add_argument("branch")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] not in ("-h", "--help"):
        argv = ["--", *argv]  # a name like `-x` is a branch name to validate, not an option
    args = ap.parse_args(argv)
    try:
        print(delete(args.repo_root, args.branch))
        return 0
    except Refused as exc:
        print(f"rice: refused -- {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"rice: could not run git -- {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
