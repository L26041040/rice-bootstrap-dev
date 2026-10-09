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
only its `RELEASE-PUBLISHED <version> -> ... release/<version> @ <sha>` line
means a release happened. Any gate FAIL is `RELEASE BLOCKED`: no publish, no
"looks fine".

**Two tracks** (RICE-FINAL-001, -002). `release/<x.y.z>` is the immutable
version authority: created once, never moved, rewritten or force-pushed, and
publishing a version whose branch already exists FAILS CLOSED, so one semver
can never name two different builds. The next version is a new branch, never
a new commit on an old one.

`main` is the latest pointer and the default install source: after the
release branch lands, publishing moves main forward to exactly that branch's
tree, as an ordinary commit on main's own history — never a force, never a
rewrite. That is why an Environment's setup line carries no version. If main
is left behind (a push that failed after the release branch landed),
`release.py promote --version <x.y.z>` moves it onto the already-published
branch; it reads the tree from that branch, never from a local export.

No git tag or GitHub release exists anywhere in this lifecycle, private or
public (RICE-BOOTSTRAP-030). Release identity is
`.claude-plugin/plugin.json` version = CHANGELOG top entry = the
`release/<version>` branch name, plus the exact private commit the export
came from. `release.py` records the private side at the start of every run
(`SOURCE rice=<version> changelog=<date> head=<sha>`) and stamps that SHA
into the public commit as provenance (`rice <version> (source <sha>)`).
Legacy `v0.1.1` tags may still exist from before this changed; none is read,
required, or maintained by anything below.

Rice never updates itself. A release reaches the OWNER through the platform:
an unpinned Environment installs it in every new session, a machine runs
`claude plugin marketplace update rice-bootstrap` then `claude plugin update
rice@rice-bootstrap`, and a pinned Environment stays put until its pin
changes. Each repo catches up when the OWNER re-runs `/rice:bootstrap` in a
new session (or after `/reload-plugins`), as the README says.

## Flow

1. **Metadata.** Update `CHANGELOG.md` (top `## [x.y.z]` entry = the
   version being released; note the date), `.claude-plugin/plugin.json`
   `version` if it changes, and `README.md`: its pin example must name
   this release (the README gate fails on any other `release/<x.y.z>`), plus
   whatever the OWNER asked. Commit.
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
   clean) rather than by shipping tests to prove it, and the **upgrade
   matrix** (`upgrade:matrix`, `scripts/upgrade_matrix.py`): every
   `release/<semver>` branch already on the public repository — enumerated
   from it on every run, never a list kept anywhere — upgraded to this
   export **directly** (release → candidate) and, for every release but the
   newest, along its **historical path** (release → each later release in
   order → candidate, every hop that release's own shipped CLI), plus a
   fresh repo; each row on a bare and a rich project. The result must be
   indistinguishable from a fresh install of this export, and uninstall
   must leave only the project. `HARMFUL RESIDUE`, `PROJECT DATA CHANGED`
   and `BLOCKED` block the release; the fix is a migration that names the
   release whose declaration it takes back, never a waiver. `BENIGN LEGACY
   RESIDUE` — exactly the inert, unprovable leftover RICE-SPEC-001 §15
   records (`upgrade_matrix.BENIGN_HUSKS`) — passes and is named in the
   gate's detail; never "fix" it by guessing what the project owns. `check`
   reads the public repository at `--public-repo <url>` (default: the
   marketplace repo), so it needs network; an unreadable repository is a
   FAIL. Every published release is in scope — there is no minimum
   supported upgrade version yet; if one is ever needed,
   `upgrade_matrix.releases()` is where it goes.
   `python3 scripts/upgrade_matrix.py --candidate <export dir>` prints the
   full matrix with every finding.
3. **Publish.** `python3 scripts/release.py publish --version <x.y.z>
   --public-repo <url>`. The script re-runs every gate, exports the
   allowlisted surface, creates `release/<x.y.z>` as a fresh single-commit
   history — no tag, no release, no private history — and then advances
   `main` to that branch's tree. If the release branch already exists on the
   remote it stops with `RELEASE BLOCKED`: report that verbatim; the answer
   is a version bump, never a force push. Success is confirmed from the
   remote, not from the local push: the release branch's SHA is what was
   pushed, main's tree is identical to it, and main declares this version. If no public repository is configured it prints
   `WAITING_FOR_PUBLIC_REPO` and stops — report that verbatim and tell the
   OWNER the one next action: create the public distribution repository and
   provide its URL / push credential.
4. **Report.** Quote the script's last line verbatim. If it is not
   `RELEASE-PUBLISHED <x.y.z> ...`, the release did not happen — say so.
   Remind the OWNER that an unpinned Environment's setup line needs no
   change: it installs from `main`, which is now this release, in every new
   session. Rice's hooks and skills change in every repo as soon as a session
   loads the release; each repo's `.claude/rice.md` and `.claude/rice-version`
   catch up only when the OWNER runs `/rice:bootstrap` there.

## Never

No git mirror, no private history to public, no copy-then-blacklist, no
auto-publish on commit, no rollback framework, no waiver of a FAIL gate, no
substituting README/spec text for a mechanical check, no shipping `tests/`
to the public repo to prove it works, no GitHub allow-secret bypass, no git
tag, no GitHub release, anywhere in this lifecycle. Never move, rewrite,
force-push or re-publish an existing `release/<x.y.z>` branch, and never
publish two different builds under one semver. `main` moves forward only, by
ordinary publication, and only onto a tree that came from a release branch.
