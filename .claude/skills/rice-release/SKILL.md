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
FAIL is `RELEASE BLOCKED`: no tag, no publish, no "looks fine".

## Flow

1. **Metadata.** Update `CHANGELOG.md` (top `## [x.y.z]` entry = the
   version being released; note the date), `.claude-plugin/plugin.json`
   `version` if it changes, and `README.md` if the OWNER asked. Commit.
2. **Gates.** `python3 scripts/release.py check --version <x.y.z>`. Read
   every `FAIL` line, fix exactly what it names, commit, re-run. Repeat
   until the last line is `RELEASE-CHECKS PASS <x.y.z>`. The gates are:
   full unit tests, verifier tests, manifests valid (`claude plugin
   validate`, no warnings), version consistency (plugin.json = CHANGELOG =
   requested), README agent-install contract, export allowlist complete,
   forbidden private paths absent, credential/owner-data scan, and the
   export working as a standalone plugin (manifests validate from the
   export, hook/skill paths resolve inside it, shipped tests pass from it).
3. **Private tag.** Ask the OWNER to create tag `v<x.y.z>` on this
   repository's release commit (this environment cannot push tags).
4. **Publish.** `python3 scripts/release.py publish --version <x.y.z>
   --public-repo <url>`. The script re-runs every gate, exports the
   allowlisted surface into an empty staging dir as a fresh single-commit
   history, tags it `v<x.y.z>`, pushes `main` and the tag, and verifies the
   tag is visible on the public repository. If no public repository is
   configured it prints `WAITING_FOR_PUBLIC_REPO` and stops — report that
   verbatim and tell the OWNER the one next action: create the empty public
   distribution repository and provide its URL / push credential.
5. **Report.** Quote the script's last line verbatim. If it is not
   `RELEASED rice <x.y.z>`, the release did not happen — say so.

## Never

No git mirror, no private history to public, no copy-then-blacklist, no
auto-publish on commit, no rollback framework, no waiver of a FAIL gate,
no substituting README/spec text for a mechanical check.
