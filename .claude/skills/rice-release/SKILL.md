---
name: rice-release
description: "Cut a release of rice-bootstrap from this private repository: update README/CHANGELOG/version, run the deterministic release gates (scripts/release.py), export the allowlisted public surface, publish to the public repository, verify. Use ONLY when the OWNER states explicit release intent for this version (e.g. 這版可以 release / 發布這版 / 更新 README 然後發布). Never on vague or future-tense release discussion."
---

# rice-release (private repository only)

Trigger rule: the OWNER expresses **explicit, present** release intent for
this version — "這版可以 release", "發布這版", "更新 README 然後發布", "ship
0.1.1", or equivalent. Musing about whether or when to release later is not
a trigger; ask nothing, do nothing, just answer the question.

This skill orchestrates. It decides nothing. `scripts/release.py` decides:
only its `RELEASE-CHECKS PASS <version>` line allows export/publish, and
only its `RELEASED rice <version>` line means a release happened. Any gate
FAIL is `RELEASE BLOCKED`: no publish, no "looks fine".

Private release identity needs no git tag. `release.py` mechanically
records it at the start of every run (`SOURCE rice=<version>
changelog=<date> head=<sha>`) from three things alone:
`.claude-plugin/plugin.json` version, the CHANGELOG top entry's version and
date, and the private repository's exact HEAD SHA. That SHA is stamped into
every public release commit and tag message (`rice <version> (source
<sha>)`) as the sole provenance link back to this repository — a legacy
private `v0.1.1` tag may still exist from before this changed; it is not
read, required, or maintained by anything below.

## Flow

1. **Metadata.** Update `CHANGELOG.md` (top `## [x.y.z]` entry = the
   version being released; note the date), `.claude-plugin/plugin.json`
   `version` if it changes, and `README.md` if the OWNER asked. Commit.
2. **Gates.** `python3 scripts/release.py check --version <x.y.z>`. Read
   every `FAIL` line, fix exactly what it names, commit, re-run. Repeat
   until the last line is `RELEASE-CHECKS PASS <x.y.z>`. The gates are:
   full unit tests, verifier tests, manifests valid (`claude plugin
   validate`, no warnings), version consistency (plugin.json = CHANGELOG =
   requested), README agent-install contract, export allowlist complete
   (public distribution only — manifests, runtime scripts, skills, hooks,
   output style, rice.md, README/CHANGELOG/LICENSE; `tests/` never ships),
   forbidden private paths absent, credential/owner-data scan (no
   exemptions — nothing shipped needs one), and the export working as a
   standalone plugin proven entirely from the private side against the
   staging tree (manifests validate from the export, hook/skill paths
   resolve inside it, every shipped runtime script import-smoke-tests
   clean) rather than by shipping tests to prove it.
3. **Publish.** `python3 scripts/release.py publish --version <x.y.z>
   --public-repo <url>`. The script re-runs every gate, exports the
   allowlisted surface, and pushes it deterministically:
   - public repo has no `main` yet → fresh single-commit history, tag
     `v<x.y.z>`, push `main` and the tag.
   - public `main` already exists → fetch it, reconcile its working tree to
     exactly the new export (anything dropped from the allowlist does not
     linger), commit a normal next release commit on top, fast-forward push
     `main`, push the new tag.
   Never a force push, an orphan/unrelated history, or a rewrite of public
   history — the public repo stays one linear distribution history. If no
   public repository is configured it prints `WAITING_FOR_PUBLIC_REPO` and
   stops — report that verbatim and tell the OWNER the one next action:
   create the empty public distribution repository and provide its URL /
   push credential.
4. **Report.** Quote the script's last line verbatim. If it is not
   `RELEASED rice <x.y.z>`, the release did not happen — say so.

## Never

No git mirror, no private history to public, no copy-then-blacklist, no
auto-publish on commit, no rollback framework, no waiver of a FAIL gate, no
substituting README/spec text for a mechanical check, no shipping `tests/`
to the public repo to prove it works, no GitHub allow-secret bypass.
