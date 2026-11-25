# Space membership persistence isolation — local verification

Date: 2026-10-26. Implementation base: PR #3091 pinned at
`7b73c4f73e0e672a8852d3e0594f252f93eac030`. Work is local on the isolated
`codex/pr3091-p2-repair` branch. This document is not remote-CI, merge, migration,
corporate integration or deployment evidence.

## Mandatory compatibility result

**PASS locally:** a never-enabled deployment works without
`ac_space_knowledge_sync`, with automatic schema creation disabled. Tests create
only the existing application schema and explicitly verify the sync table stays
absent before and after execution. They assert persisted business outcomes, not
just success responses. No missing-table exception is swallowed.

The 20 focused cases cover TEAM/PERSONAL/Skill Space creation, ordinary and batch
membership/role changes, rename/delete, manual/legacy/AUTO approvals, complete
HTTP lifecycle and original response/auth semantics, and singleton policy wiring
with no constructor DB I/O. The original implementation first failed 15 cases;
the pilot and all-writer migrations then made them green.

## Fresh final runs

Commands use Python 3.12, with `PYTHONPATH="$PWD/src:$PWD"` from `apps/backend`
and `PYTHONDONTWRITEBYTECODE=1`. Dependencies were reused from an existing
isolated development environment; source resolution was explicitly pinned to
this worktree, including subprocess tests. All test commands use
`python -m pytest -q -p no:cacheprovider`.

| Scope | Result |
| --- | --- |
| `tests/community/architecture`, core Spaces/Work Orders/repository implementations/Space Knowledge, HTTP Space Knowledge/maintenance, Skill repositories, Space Knowledge/Membership protocol contracts | **1448 passed** |
| Registered endpoint framework runner, `-k 'spaces or work-orders'` (including all six knowledge cases) | **143 passed**, 1481 unrelated cases deselected |
| Entire `tests/community/core/skill_center` | **1309 passed**, 10 skipped |
| Focused missing-table + zero-I/O/singleton wiring rerun | **20 passed** (included in the broad run; do not double count) |
| Changed-file Ruff and repository `python_sast_local.sh apps/backend 1 --base 7b73c4f73 --head HEAD` | PASS |
| Changed Python production source size | PASS, maximum 983 lines; none exceeds 1000 |
| `git diff --check` | PASS |

The three non-overlapping main runs total **2900 passed, 10 skipped**. This is
not the entire Backend test suite or full PR CI. Existing deprecation warnings
remain; skipped tests are not successes.

## AC traceability

| AC | Evidence and boundary |
| --- | --- |
| AC1 Missing-table availability | Real operator-managed SQLite and full DI/HTTP lifecycle; table absent throughout. Legacy business data persisted. |
| AC2 Inactive isolation | SQL assertions exclude outbox reads/capture no-op UPDATE; no Sink/Provider/queue effects or repair task. Default effects can record/reserve with no database at all. Neutral source locks remain. |
| AC3 Explicit assembly | Strict config rejection, eager all-profile resolution, no DB I/O at construction; one lifecycle/effects/producer-guard singleton shared by all four concrete writer repositories. Enabled capture with missing/incompatible schema fails. |
| AC4 Atomic enabled behavior | File-SQLite activation/member races across connections/processes; transaction rollback, 2000-member and UTF-8 size admission, no-op revision stability, FIFO, hash/envelope acknowledgement, corrupt/missing-head worker failure. |
| AC5 Pause/restart/history | Full graph records add/role/remove/rename/disabled revisions while Sink paused, restarts and drains all revisions in order. Skill creator avoids retired IDs. Any-app same-environment history forbids disabled capture; library producer and writer bypasses fail before commit/Sink. |
| AC6 Bounded dispatch | RED performance tests detect old aggregate and payload reads; new indexed seeks cap discovery at 32 Spaces with continuation. Metadata SELECTs exclude payload; exact delivery reads one next payload. |
| AC7 Existing boundaries | Skill business and permissions unchanged, 1309 Skill Center cases plus repository/approval/HTTP regressions pass. Finite AST guard catches direct, aliased and relative knowledge imports; existing architecture gates are not waived. |
| AC8 Honest validation | RED→pilot→GREEN, independent group reviews, fresh final tests and real Singlebox attempt reported separately. External runtime boundaries below remain unverified. |

## Performance evidence and trade-off

Actual SQLite discovery VM instructions for one Space:

| Pending revisions | Old aggregate probe | New seeks |
| ---: | ---: | ---: |
| 1 | approximately 103 | 113 |
| 1000 | approximately 10000 | 90 |
| 100000 | approximately 1000000 | 90 |

Tests assert the existing covering index is used without a temporary sort.
Discovery is at most 32 seeks, plus two scalar progress reads per candidate
(up to 96 outbox SELECTs). The read-only performance reviewer also traced the
real queue: idle 1 statement/no writes; one candidate 6 statements; full page
160 statements including 32 INSERT attempts. A repeated identical page creates
no new queue rows, but duplicate INSERT attempts still cost I/O (pre-existing).
The loop is serial and sleeps 30 seconds after completion. Deployment instance
count and RTT multiply this cost; no fleet capacity claim is made.

Ack compares the delivered full envelope/status/canonical hash to the locked
immutable row. Dispatch may enqueue a corrupt-but-present pointer, but the real
worker returns Retry with no Sink call or ack. Missing predecessors are not
queued. Source no-op equality still reads its previous full snapshot.

Independent Group A/B/C reviews finished with no confirmed blocking bugs.
Group C Performance is PASS WITH RISKS for unknown remote DB plans/RTT and
instance count, not a claim that bounded extra round trips are free.

## Unified Singlebox — NOT PASSED

Executed the default all-module `singlebox/ci/singlebox_coverage.sh` on the
current implementation, without altering thresholds or denominators. It exited
1 during BCSFuse dependency setup:

- `grpcio==1.80.0` wheel download/extraction failed due to network timeout
  (`UV_HTTP_TIMEOUT=30s`).
- The product stack and acceptance/coverage phase did not start.
- The standard `verify_singlebox_coverage_artifacts.py --reports-dir
  singlebox/.dependencies/coverage/singlebox/reports` also exited 1: canonical
  summary/acceptance/Backend/BaaS/BCS artifacts are missing. Missing reports are
  **not zero measured coverage** and are not a passing gate.

Raw logs are local diagnostic evidence under `/tmp/pr3091-intrusion-audit/`,
not canonical CI reports. No old-Head coverage or isolated unit metrics are
substituted for this failed unified gate.

## Delivery / rollout boundaries

- Corporate OCB composition roots/HTTP adapters: not verified or edited here;
  direct constructors must adopt the required persistence policy/producer guard.
- MySQL/OceanBase concurrency and query plans, remote receiver, SSO/gateway,
  deployment, online DDL: not verified; local SQLite does not prove them.
- Remote PR CI: not rerun or claimed repaired by this local work.
- No push, PR state change, merge, deployment, online SQL or new tables/indexes.
- Never-enabled deployment defaults to capture=false and needs no knowledge DDL.
  First enablement requires the original table migration and **all writers** on
  capture=true before any activation. Paused delivery must keep capture=true;
  rollback with history must preserve capture or pause affected writes.

## Post-integration conflict verification (2026-10-26)

Merged `dev@cf9d2a0184fd0845afcad5a863a0ecb129d5cdfc` into the feature
branch after `1ee9d746956ae7f8e1ebbbe5f90de03c73548422`, preserving all existing
feature commits (no rebase/force push). This is feature-branch integration, not
merging PR #3091 into dev.

Only two files required manual resolution: API and repository Context Boundary
READMEs. They had adjacent additions, not incompatible policies. Preserve both
knowledge capture declarations and upstream Owner Handoff contracts/repository
exports/dependencies, plus upstream B5 stable-entity ownership notes. Owner
Handoff production sources match dev; knowledge capture production sources match
the pre-merge feature. The automatically merged DI container retains both modules
and both eager configuration checks. No business behavior or schema was invented
to resolve these documentation conflicts.

Fresh integrated-tree validation:

- **1528 passed**: the previous architecture/Space/approval/repository/knowledge
  regression scope plus B5, B6, owner-handoff service/delivery/resource suites.
- **153 passed**: registered endpoint runner `-k 'spaces or work-orders or
  owner-handoff'`, including knowledge and upstream Owner Handoff commands;
  1483 unrelated cases deselected.
- **1309 passed, 10 skipped**: full Skill Center core tests.
- Non-overlapping total: **2990 passed, 10 skipped**. Missing-table real DB/HTTP
  regressions remain in the first run, not counted twice.
- Changed-PR Python Ruff, both-side boundary-entry preservation assertions and
  `git diff --check origin/dev` pass. PR production-source maximum is now
  **1000 lines** (`adapters/http/app.py` after upstream integration), within the
  unchanged limit. No new size exemption.

The earlier full-merge diff includes whitespace warnings already present in
upstream-only files; they are absent from the PR-versus-dev diff and are not
cleaned as part of this focused resolution. Full Backend CI, Singlebox and
corporate/remote DB deployment evidence were not rerun here; the earlier
Singlebox dependency blocker remains an unverified gate, not a success.
