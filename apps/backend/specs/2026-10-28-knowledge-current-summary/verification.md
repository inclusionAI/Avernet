# Current knowledge summary verification

Date: 2026-10-28 (Asia/Shanghai).

## Scope and implementation

User-approved follow-up to PR #3177 Head `181b3d4281d0726816f3e5e395ac070515d51bff`.
Final source commit: `e0d457f3b3a07643c644fe32055726922e08aaab`.
Earlier implementation commits are superseded by capacity, collation and
100-ID key-seek corrections; their gates are historical, not final acceptance.
Only the two performance findings are fixed; legacy status-route findings are
explicitly excluded by the user. No new tables/columns/indexes, workers or native
status/operation rules. No production SQL, push, merge, deploy or enablement.

Existing common-config unique key stores one reserved current result for the
immutable source PK/entity/business Bot/environment, not an Owner-specific
history. JSON payload independently checks tenant/current Owner/space. Native
identity hashing only fits the existing config key's width; no external UUID
contract is introduced. Actual successful workflow IDs remain the watermark.

Completion locks/reads the summary by unique key after the existing Bot/native
locks. It updates only previous/new bounded KB keys and flushes the summary in
the native CAS transaction. Unresolved source callbacks can persist UNKNOWN
against native identity but never write guarded bindings; previous materialized
keys survive for later safe cleanup. Corrupt/missing summaries fail closed with
no publication-history or whole-binding fallback. A trusted republication can
initialize an absent summary. Pre-summary untracked physical flags are not
scanned for cleanup and are never effective without current summary membership.

Online flag SQL includes the exact current summary fence; MySQL casts to BINARY
so a case-insensitive TEXT collation cannot equate two different snapshots.
Generic common-config set/create/upsert/batch/update/delete reject the reserved
key; ordinary keys keep their behavior and operators can still read records.

## Tests

Red-green: new acceptance tests failed on the prior implementation (six failures
and one incidental pass) before implementation. New bounded-work tests reject
both historical JSON scans and non-keyed clear SQL.

- First integrated gate (before the final MySQL bytewise fence): 2667 passed,
  43 warnings, 0 skipped.
- Final focused gate including current summary, VM-work probe and reserved-key
  management protection: 36 passed, 17 warnings, 0 skipped.
- Final pinned integrated gate: **2714 passed, 43 warnings, 0 skipped**,
  in 176.32 seconds, on the final source above.
- Independent Spec closure: reserved-key alias reproduction now rejected on a
  real SQLite NOCASE table; injected 100-row batch failure rolled back native
  state, summary and flags, and retry installed exactly the intended IDs. No
  remaining evidenced P2+ findings in this approved closure scope.
- Independent Standards closure: original multibyte capacity issue closed;
  SQL compiled for SQLite/MySQL/PostgreSQL, native model/boundary checks pass,
  and final bounded patch has no consequential Standards P2+ finding.
- Independent Performance closure: PASS WITH RISKS; independently ran the
  100-ID probe and substituted old completion functions as a negative control.
  The new test failed with the old functions, demonstrating defect detection.
  No remaining evidenced performance finding; live DB risks remain below.
- Ruff, repository blocking Flake8 rules and `git diff --check`: passed.
- Native publish, Bot/common-config, knowledge binding and task queue ORM models
  byte-identical to the prior Head. SQL scripts and legacy status handler unchanged.
- All production files modified in this follow-up are <=1000 lines (largest931).
  The older PR's existing oversized-service waiver is neither changed nor extended.

Command (from apps/backend):

```sh
PYTHONDONTWRITEBYTECODE=1 DEPLOY_PROFILE=test \
PYTHONPATH="src:/tmp/tc-knowledge-1027-public-deps:/Users/qiaoyifan/Antcode/ocb/src/backend/.venv/lib/python3.12/site-packages" \
/Users/qiaoyifan/Antcode/ocb/src/backend/.venv/bin/python -S -m pytest \
  tests/community/repository/publishing tests/community/core/bot_knowledge \
  tests/community/core/common_config tests/community/repository/config \
  tests/community/core/service_bot/services \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/endpoints/test_bot_common_config_router.py \
  tests/community/architecture tests/community/di \
  -q --disable-warnings --tb=short -p no:cacheprovider
```

## Bounded local SQL evidence

A local query-plan capture (raw artifact intentionally not carried in this PR)
recorded actual emitted SQL/EXPLAIN plans and SQLite VM steps from
`test_knowledge_summary_cost.py`. Each stage ADDS 1, 1000, 10000
historical publications and disabled bindings to the same DB. Including native
current publications, cumulative publication rows are 3 / 1004 / 11005 and
binding rows are 3 / 1003 / 11003. One live KB is selected; the final two pages
are full and the first page has only three rows.

| Cumulative publications / bindings | Enabled reader steps | Page steps | Completion steps |
| --- | ---: | ---: | ---: |
| 3 / 3 | 343 | 374 | 676 |
| 1004 / 1003 | 248 | 751 | 676 |
| 11005 / 11003 | 248 | 751 | 676 |

Each reader/page call emits four statements; completion emits18 in this fixture,
including native/scoped reads, the SQLite Bot write reservation and final native
re-read. Config lookup uses the existing full unique key. Completion resolves KB keys
through `(avernet_tenant, env, bot_pk, kb_id)` without space/flag predicates,
validates current locked scope, and writes by PK equality with all explicit
scope/prior-flag predicates. No JSON history sort, publication range scan or
all-bindings clear occurs. The bounded assertions allow fixed planner variance,
not elapsed-time or production-latency inference. Terminal replay remains a
no-write native no-op under the retained lifecycle tests.

### Maximum 100-ID boundary and review corrections

A singleton-ID probe alone was insufficient: the independent reviewer reproduced
an O(B) plan at 100 IDs even with `IN (...)`. Resolving without space/enabled
filters and using PK-equality driver batches closes that defect. Plans for
executemany use a representative parameter set of the exact shared statement,
not an incorrectly skipped UPDATE.

| Added disabled bindings | Replace 100 old with 100 new IDs | Complete unchanged 100 IDs |
| --- | ---: | ---: |
| 1 | 17644 VM steps | 5921 VM steps |
| 1000 | 17644 VM steps | 5921 VM steps |
| 10000 | 17644 VM steps | 5921 VM steps |

The replacement uses two driver batches of 100 PK-equality updates; unchanged
selection issues zero binding UPDATEs. Batches are **not** a claim of two remote
network round trips. All SQL plans use existing full unique-key resolution or
INTEGER PRIMARY KEY writes in SQLite. Maximum selected/materialized sets remain
100 each; at most 200 binding rows change per completion. No new timers, workers
or background scans are introduced.

The reviewer also found duplicate applied-ID encoding exceeded TEXT capacity
for valid multibyte IDs. Applied state now stores IDs once and reconstructs
materialized IDs on decode; UNKNOWN retains the prior set explicitly. Actual
100x128-character CJK/emoji completion+read round trips pass without reducing
the domain limit or changing schema. Writer rejects actual oversize output
before any config mutation. Generic key protection covers collation aliases,
actual DB-matched rows and atomic conflict-write preservation.

## Release boundaries

The existing common-config table and unique key must exist before an opted-in
completion or service online consumer uses this implementation. Code presence
and a fresh local schema do NOT prove it is provisioned in any deployed database.
No automatic CREATE/ALTER/backfill or runtime history scan is added. Publication
opt-in is still not a kill switch for explicitly requested online reads.

Evidence is local controlled SQLite/real repository transactions and dialect SQL
compilation, not live MySQL/OceanBase transaction/lock/plan or multi-pod E2E proof.
Actual plans, indexes, deployment history, read cadence, lock waits and latency
remain pre-rollout checks. No real BaaS/OSS/ECB acceptance or remote CI success is
claimed. Previous 2026-10-27 history-linear probe remains historical, superseded
by this current-summary evidence rather than relabelled as bounded.
