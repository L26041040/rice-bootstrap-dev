#!/usr/bin/env python3
"""Deterministic public export: empty staging dir -> allowlist copy -> audit.

The private repository is the development source of truth; the public
repository is a verified release distribution surface. This script never
mirrors, never copies the repo root and subtracts, and never carries git
history: it copies exactly the files in ALLOWLIST into an empty directory
and then audits the result for anything that must not be there.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_REPO = "L26041040/rice-bootstrap"

# The complete public surface. Order is irrelevant; every entry must exist.
ALLOWLIST = (
    ".claude-plugin/marketplace.json",
    ".claude-plugin/plugin.json",
    "skills/bootstrap/SKILL.md",
    "skills/rice-uninstall/SKILL.md",
    "skills/history/SKILL.md",
    "hooks/hooks.json",
    "scripts/settings_template.py",
    "scripts/uninstall.py",
    "scripts/reconcile.py",
    "scripts/evidence.py",
    "scripts/verify.py",
    "scripts/rice.py",
    "scripts/bootstrap_continue.py",
    "scripts/repair.py",
    "scripts/archive_hook.py",
    "scripts/output_styles.py",
    "scripts/delete_remote_branch.py",
    "scripts/session_context.py",
    "scripts/guard.py",
    "scripts/github_labels.py",
    "output-styles/laodi.md",
    "resources/rice.md",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    ".gitignore",
)

# Anything matching these must never appear in an export, whatever the
# allowlist says (defence in depth against a future allowlist mistake).
FORBIDDEN_PATTERNS = (
    r"^session-history(/|$)",
    r"^\.claude(/|$)",
    r"^CLAUDE\.md$",
    r"^docs(/|$)",
    r"^scripts/release\.py$",
    r"^scripts/export_public\.py$",
    r"^scripts/upgrade_matrix\.py$",
    r"^tests(/|$)",
    r"^dist(/|$)",
    r"\.pyc$",
    r"(^|/)__pycache__(/|$)",
)

# Credential shapes (same families the archive hook redacts) and
# owner-specific data. No path is exempt: tests/ and its fixtures are not
# part of the public export (see ALLOWLIST), so nothing shipped needs a
# credential-scan exemption.
SECRET_PATTERNS = (
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255}")),
    ("openai-key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
OWNER_DATA_PATTERNS = (
    ("session-url", re.compile(r"claude\.ai/code/session_[A-Za-z0-9]+")),
    ("session-id", re.compile(r"\bsession_01[A-Za-z0-9]{20,}\b")),
    # anthropic.com (attribution) and the RFC 2606 reserved domains (test-only,
    # can never be owner data) are the only exemptions.
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@(?!anthropic\.com\b|example\.(?:com|net|org)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)


def is_forbidden(rel: str) -> str | None:
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, rel):
            return pat
    return None


def export(dest: Path, source_root: Path = SOURCE_ROOT) -> list[str]:
    """Empty `dest`, copy the allowlist, return the relative paths copied.
    Raises on any missing allowlisted file (an export must be complete)."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    copied = []
    for rel in ALLOWLIST:
        src = source_root / rel
        if not src.is_file():
            raise FileNotFoundError(f"allowlisted file missing in source: {rel}")
        if is_forbidden(rel):
            raise ValueError(f"allowlist entry matches a forbidden pattern: {rel}")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        copied.append(rel)
    return copied


def audit(dest: Path) -> list[str]:
    """Problems found in an export dir; empty list means clean."""
    dest = Path(dest)
    problems = []
    present = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    for rel in present:
        if rel not in ALLOWLIST:
            problems.append(f"not allowlisted: {rel}")
        pat = is_forbidden(rel)
        if pat:
            problems.append(f"forbidden ({pat}): {rel}")
    for rel in ALLOWLIST:
        if rel not in present:
            problems.append(f"allowlisted but absent: {rel}")
    for rel in present:
        try:
            text = (dest / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pat in SECRET_PATTERNS:
            if pat.search(text):
                problems.append(f"credential-shaped string ({name}): {rel}")
        for name, pat in OWNER_DATA_PATTERNS:
            if pat.search(text):
                problems.append(f"owner-specific data ({name}): {rel}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rice public export (allowlist copy + audit)")
    ap.add_argument("--dest", required=True)
    args = ap.parse_args(argv)
    copied = export(Path(args.dest))
    problems = audit(Path(args.dest))
    for rel in copied:
        print(f"exported {rel}")
    for p in problems:
        print(f"AUDIT FAIL: {p}")
    print(f"export: {len(copied)} files, {len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
