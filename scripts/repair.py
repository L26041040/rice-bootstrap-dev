#!/usr/bin/env python3
"""RICE-BOOTSTRAP-042/-044: the single deterministic repair boundary.

One question only: *given the failures the verifier just named, what can be
restored losslessly, and what cannot?* This module never decides whether a
repo is correct, never evaluates a gate, and never declares a pass -- it is
handed a `verify.Report`, acts on `report.failing`, and the caller
(`rice.py`) runs the same verifier again for the word that counts.

What is mechanically repairable here, and from where:

- anything `reconcile` owns -- the item says so itself (`ItemResult.owned`),
  so this module keeps no second list of what rice owns
  (RICE-BOOTSTRAP-045). One `reconcile.apply(converge_drift=True)` converges
  every one of them from this build's canonical state; the verifier has
  already declared the drift a FAIL, so converging is the one repair that
  exists.
- Matt's setup artifacts -- only from this repo's own history: a doc that
  is tracked at HEAD but missing from the working tree is checked out
  again; a `## Agent skills` section present in HEAD's CLAUDE.md but gone
  from the working copy is appended back, byte for byte. Nothing else:
  every Matt file encodes answers the OWNER gave the official setup, so a
  file the repo never committed cannot be rebuilt without guessing, and
  guessing is exactly what this module refuses to do. Such an artifact is
  reported as unresolved, for the model to explain to the OWNER.

- Matt's triage labels on GitHub (`matt:labels`) -- created from the
  vocabulary Matt's setup wrote, only the ones the repository lacks
  (`github_labels.create_missing`); a refusal is unresolved, with the one
  authorization the OWNER has to give.

Everything else the verifier can fail (`plugin:*`, `output-style:*`, the
hook registrations of the build, `git-deny:behaves`, owner-defined settings
keys, `rtk`) needs the environment, the OWNER, or a real execution; it is
passed through as unresolved with the verifier's own detail.
"""
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence  # noqa: E402
import reconcile  # noqa: E402

MATT = "matt:artifacts"
LABELS = "matt:labels"


@dataclass
class RepairResult:
    applied: list[str] = field(default_factory=list)     # what was restored, and from where
    unresolved: list[str] = field(default_factory=list)  # what this module refuses to guess at

    @property
    def lines(self) -> list[str]:
        return ([f"repair: {a}" for a in self.applied] or ["repair: nothing mechanically repairable"]) \
            + [f"unresolved: {u}" for u in self.unresolved]


# --------------------------------------------------------------------------
# Matt artifacts: restore from this repo's history, never from a template
# --------------------------------------------------------------------------

def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)


def _head_text(repo_root: Path, rel: str) -> str | None:
    r = _git(repo_root, "show", f"HEAD:{rel}")
    return r.stdout if r.returncode == 0 else None


def _section_from(text: str, heading: str) -> str | None:
    """The `heading` section of a markdown text: from its heading line up to
    the next heading of the same or higher level, or the end."""
    lines = text.splitlines(keepends=True)
    level = len(heading) - len(heading.lstrip("#"))
    start = next((i for i, l in enumerate(lines) if l.strip() == heading), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        s = lines[i].lstrip()
        if s.startswith("#") and 0 < len(s) - len(s.lstrip("#")) <= level:
            end = i
            break
    return "".join(lines[start:end])


def _restore_matt(repo_root: Path, plugin_probe) -> tuple[list[str], list[str]]:
    applied, unresolved = [], []
    for label, present in evidence.matt_presence(repo_root, plugin_probe).items():
        if present:
            continue
        if label == evidence.MATT_HEADING_LABEL:
            head = _head_text(repo_root, "CLAUDE.md")
            section = _section_from(head, evidence.MATT_HEADING) if head else None
            if section:
                dest = repo_root / "CLAUDE.md"
                current = dest.read_text(encoding="utf-8") if dest.exists() else ""
                sep = "" if (not current or current.endswith("\n")) else "\n"
                dest.write_text(f"{current}{sep}\n{section}" if current else section, encoding="utf-8")
                applied.append(f"{MATT}: restored the `{evidence.MATT_HEADING}` section of CLAUDE.md from HEAD")
            else:
                unresolved.append(f"{MATT}: {label} is not in this repo's HEAD; only the official Matt setup can write it")
            continue
        if _head_text(repo_root, label) is not None and _git(repo_root, "checkout", "HEAD", "--", label).returncode == 0:
            applied.append(f"{MATT}: restored {label} from HEAD")
        else:
            unresolved.append(f"{MATT}: {label} is not in this repo's HEAD; only the official Matt setup can write it")
    return applied, unresolved


# --------------------------------------------------------------------------
# the repair boundary
# --------------------------------------------------------------------------

def repair(repo_root, report, *, plugin_probe=None, github_probe=None, **_unused) -> RepairResult:
    """Apply the known repair for every failure in `report.failing` -- the
    items the verifier required for the gate it just evaluated and did not
    get as PASS. Returns what was done and what could not be; says nothing
    about whether the repo now passes. Run the verifier for that."""
    repo_root = Path(repo_root)
    plugin_probe = plugin_probe or evidence._default_plugin_probe
    failing = report.failing
    result = RepairResult()
    converged = {}
    if any(i.owned for i in failing):
        for a in reconcile.apply(repo_root, converge_drift=True):
            converged[a.name] = a
            if a.state in ("created", "updated", "removed"):
                result.applied.append(f"{a.name}: {a.state} from canonical state" + (f" ({a.detail})" if a.detail else ""))
    for i in failing:
        if i.owned:
            # owned means apply is the repair -- not that it succeeded. An
            # artifact apply could not converge losslessly (a `conflict`) is
            # reported, never silently counted as handled.
            a = converged.get(i.name)
            if a is not None and a.state in ("created", "updated", "removed", "noop"):
                continue
            state, detail = (a.state, a.detail) if a is not None else (i.state, i.detail)
            result.unresolved.append(f"{i.name}: {state}" + (f" -- {detail}" if detail else ""))
            continue
        if i.name == MATT:
            applied, unresolved = _restore_matt(repo_root, plugin_probe)
            result.applied += applied
            result.unresolved += unresolved
        elif i.name == LABELS:
            import github_labels  # local: only this repair talks to GitHub
            created, repo, why = github_labels.create_missing(repo_root, github_probe)
            if created:
                result.applied.append(f"{LABELS}: created {', '.join(created)} on {repo}")
            if why:
                result.unresolved.append(f"{LABELS}: {why}")
        else:
            result.unresolved.append(f"{i.name}: {i.state}" + (f" -- {i.detail}" if i.detail else ""))
    return result
