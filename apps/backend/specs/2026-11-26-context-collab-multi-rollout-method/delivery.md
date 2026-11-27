# MultiRollout implementation delivery

- Date: 2026-11-27; owner: Backend `context_collab` L5/L6.
- Baseline: `15463d4d457d1d3f0ed287ed810c4b6b990e831b`.
- Status: local implementation and verification; no commit/push, merge, host integration or deployment claimed.
- Intent: [spec](spec.md), [plan](plan.md); original access/relation-renaming evidence stays in [research](research.md).

## Implemented

1. Local `MultiRollout@1.0` exact profile/Kind; existing four core profiles remain
   `@0.2`. Closed typed strategies/budgets, composition codec and explicit unique
   `MultiRollout NEST Swarm` selection; no `body_method` or N copies of Swarm.
2. Versioned snapshot/controller/candidates, derived independent body Bindings and
   edges, immutable RunPlans, stable focus, true candidate progress scope and
   controller-vs-candidate aggregation. SQL remains the sole runtime authority.
3. Candidate-scoped actual Swarm/Bot execution with real attempt/session/run routes,
   isolated workspace/artifact/execution envelopes, authorization/evidence checks
   and stage-specific plan instruction/candidate projection. Successors fully rerun.
4. Bounded parallel provider IO and cross-Collet tenant admission; slots persist
   through unknown dispatch/unconfirmed cancel. Full-batch PASS-only trusted
   highest-score selection, frozen rubric, complete finite scores and ordinal ties.
5. Shared manual/autonomous proposal validation/commit, durable in-flight strategy
   operation lease/fence, pre-call charging, replay receipts, bounded waits and
   budgets/no-progress, immutable bounded patch, stop/late-delivery reconciliation.
6. Opt-in query/OpenAPI metadata, explicit worker/deadline scheduling, leased/fenced
   outbox and public unavailable DI capabilities. Finite metrics contain no raw
   feedback or credentials.

Owning reusable knowledge:
- [L5 controller/host/recovery contract](../../src/agentclaw/community/core/task/context_collab/l5_methods/MULTI-ROLLOUT.md).
- [ProductProfile configuration and integration](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md#multirollout-nest-swarm-default-creation).
- [Module navigation/boundary](../../src/agentclaw/community/core/task/context_collab/README.md).

## Verification performed

Commands run from `apps/backend/` with Python 3.12 and `DEPLOY_PROFILE=test`:

```bash
.venv/bin/python -m pytest -q \
  tests/community/core/task/context_collab/multi_rollout \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/core/task/context_collab/test_method_profiles.py \
  tests/community/core/task/context_collab/test_method_composition.py \
  tests/community/core/task/context_collab/test_network_profiles.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py
```

Final current-source run: **433 passed**, 19 warnings, 80.63 seconds.
An intermediate run before the last recovery/codec hardening passed 419 tests;
it is not substituted for the final rerun. These are actual MultiRollout and
current compatibility checks, not the earlier relation-endpoint rename run.

- Independent file-backed SQLite connections exercise SQL CAS, claims/fences,
  rollback, snapshot reconstruction and unique tenant slots.
- Blocking provider calls prove N=4/max-concurrency=2 overlap and peak. Two Collets
  compete for the same SQL tenant quota; snapshots/file/session namespaces differ.
- Tests cover mixed PASS/FAIL and all-failed batches; invalid/nonfinite/missing/
  duplicate/out-of-scope scores; stable ties; non-focus progress/replay/CAS retry.
- Manual failure→wait→patch→new plan→real changed stage input→PASS and autonomous
  failure→intent→proposal→new candidate→PASS exercise real engine/repository/worker.
- UUID replay/reconstruction/changed content/revoked actor/in-flight concurrency,
  malicious patches, no-op deadlines, strategy/rollout/dispatch limits, delegation
  revocation, runtime failure classification and resource revocation are covered.
- Fault injection covers outbox/receipt rollback, unknown start/no blind resend,
  stop-vs-selection, stop-vs-late-delivery and unconfirmed/confirmed cancellation.
- HTTP creation/query/session metadata and published response schema exercise the
  real service; default DI strategy/isolation/notification capabilities reject use.

### Architecture/API fast gates and hygiene

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest -q \
  tests/community/architecture \
  tests/community/framework/test_coverage_gate.py \
  tests/community/adapters/http/openapi_v1/test_explicit_user_id.py \
  tests/community/adapters/http/openapi_v1/test_schema_docs.py \
  tests/community/core/bot_config_surface/test_no_handler_only_checks.py
```

Result: **524 passed**, 45 warnings, 137.40 seconds.

- `black --check` passed for all 41 changed/new Python files; Ruff's full configured
  rules passed on new Python files and `--select F` passed on all changed Python.
- All 29 changed/new Python source files are within 1,000 lines (largest: 631).
- Markdown target check: 8 changed/new documents, **0 newly introduced** missing
  targets; 19 pre-existing missing targets remain outside this change's scope.
- The owning ProductProfile example executes and round-trips parallel, interactive
  and autonomous compositions through the actual configuration codec.
- AST comparison confirms all five Service API parameter lists match baseline;
  progress still has independent request_id/attempt/expected_revision fields.
- `git diff --check` passed. No mandatory architecture/coverage gate was weakened.

### Failures found and fixed during development

Early runs were **not clean**. Fixes included missing runtime-checkable DI ports,
closed-codec direct-call errors, terminal transitions with an empty outbox not
being saved, dispatch-budget termination with no cancellation intent, and
unnecessary controller revision writes contending with selection reservation.
Architecture regression also caught the response-schema filename convention and
the now-outdated exact-four-MethodKind assertion. Their contracts were updated
without weakening architecture/coverage gates.

A later concurrent provider-overlap regression also exposed a **torn read**:
reading the runtime snapshot and admission rows in separate SELECTs could straddle
another valid CAS commit under SQLite legacy SELECT transactions or READ COMMITTED.
They now read in one correlated SQL statement, while keeping strict admission
consistency validation. The main overlap journey runs three independent repetitions,
and explicit admission corruption is still rejected. Cancel reconciliation now also
recognizes an actual terminal provider observation as settled external occupancy,
without fabricating a cancellation business event.

Final codec hardening rejects non-object runtime roots, null control records and
arrays of key/value pairs where stored objects are required. The focused snapshot
check (`test_recovery.py -k snapshot`) passed **20 tests** (10 deselected).

### Complete Backend CI

Full CI is run through `scripts/ci_test.sh`, with four workers and the mandatory
coverage gates unchanged. A temporary **Git tree**, not a branch commit or staged
user index, includes all modified/new source for changed-line coverage against
baseline HEAD; this avoids falsely measuring only already-committed code.

```bash
PATH="$PWD/.venv/bin:$PATH" BACKEND_CI_PYTEST_WORKERS=4 \
  bash scripts/ci_test.sh --base HEAD --head "$validation_tree"
```

The final CI validation tree is `ab39f22db15cf36d00006fa6cbdb21d79119fc0c`.
A byte comparison confirms all 31 executable source/DDL files in this change
match that tree. Markdown-only clarifications do not change the measured code.

Final current-source CI completed successfully in **694.72 seconds**:

| Check | Actual result |
| --- | --- |
| Pytest | **35,537 passed**, 41 skipped, 634 warnings; **13 subtests passed** |
| Case pass rate | **100.00%** (required 100%) |
| Overall line coverage | **90.80%** (required 75%) |
| Changed-line coverage | **88.91% — 1,259/1,416** (required 80%) |
| Script outcome | **`backend CI gate passed`**, exit status 0 |

This run includes the final atomic snapshot/admission read, terminal-observation
cancellation settlement, strategy actor-scoping and closed-object codec hardening.
No intermediate CI count is used as the final-source result. Skipped/external-service
checks are not claimed as executed integration evidence; warnings remain visible
in the actual report.

The JUnit and coverage artifacts are generated under `apps/backend/pytest_report/`
(ignored runtime reports, not repository deliverables). The actual Git index remains
unstaged; the validation tree did not commit, switch branches or push anything.

## DDL and external integration boundaries

- [Current bootstrap DDL](../../src/agentclaw/community/core/task/context_collab/sql/2026_11_26_product_runtime_v2.sql)
  is checked against SQLAlchemy MySQL compilation, including admission and intent indexes.
- [Additive upgrade DDL](../../src/agentclaw/community/core/task/context_collab/sql/2026_11_27_multi_rollout.sql)
  is provided for existing v2 installations. Stop writers/workers before applying
  it once; preserve unknown external attempts/slots and reconcile before rollback.
- **Not executed:** a real MySQL upgrade/concurrency/rollback exercise, enterprise
  strategy Agent, real external workspace/evidence provider, remote cancellation
  settlement and production deployment. SQLite plus recording external ports do
  not establish those integrations.
- Public defaults deliberately fail closed. Hosts must bind strategy/evaluator,
  isolation/safe-effects and idempotent notification capabilities, supply current
  tenant quota/delegation, and schedule worker **and** deadline checks explicitly.
- Unknown start without query/idempotency reconciliation is diagnosed and fenced,
  not resent. Known failed/unknown outbox strategy work requires explicit host
  recovery; recomputation is charged again. No hidden background retry loop.
- ACCS source document was not accessible; no claim of full ACCS compliance or a
  new official RFC number. This implementation is the repository-local extension.

## 2026-11-27 follow-up: product-owned creation defaults

Baseline: `510a068bdb67ae295900bd12432c8ca6d8d5d263` (the initial implementation
was committed and pushed). The verification above records that earlier delivery,
not evidence for this subsequent creation-contract change. This follow-up is local
work; no new commit/push, merge, host integration or deployment is claimed.

### Contract and behavior

- `create_product_profile(name, stages)` removes caller `composition` entirely
  from both the Service API and HTTP body. The stored/returned ProductProfile
  retains its generated composition. Unknown/removed HTTP fields fail with 422;
  old Python positional/keyword invocations fail rather than silently overriding
  the product default.
- [The owning creation contract](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md#multirollout-nest-swarm-default-creation)
  now fixes MultiRollout@1.0 NEST Swarm@0.2, interactive feedback and the bounded
  product-owned defaults. The Swarm graph is the existing static YAML workflow
  (`SwarmStagesPolicy`), not another authored legacy workflow graph or dynamic
  planning Agent.
- Trusted acceptance failure or confirmed execution failure waits for human
  feedback. Only then does the installed adaptation Agent propose RE_DO,
  OPTIMIZE, DO_NOTHING or SHUTDOWN. No feedback means no Agent invocation or
  successor rollout. Unknown dispatch keeps its fence/slot and cannot become a
  human-authorized blind resend. Autonomous runtime failures still terminate.
- Existing stored serial, parallel and autonomous Profiles are not rewritten.
  Their compatibility tests explicitly seed those immutable configurations through
  the repository; new public-creation tests exercise the new Service API instead
  of adding a hidden custom-composition creation path.
- No schema/DDL changes. Hosts still must install the exact execution, evidence,
  adaptation Agent, safe isolation and notification capabilities. Public missing
  capabilities fail closed before profile persistence.

### Verification performed for this follow-up

From `apps/backend/` with `DEPLOY_PROFILE=test`:

```bash
.venv/bin/python -m pytest -q \
  tests/community/core/task/context_collab/multi_rollout \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/core/task/context_collab/test_method_profiles.py \
  tests/community/core/task/context_collab/test_method_composition.py \
  tests/community/core/task/context_collab/test_network_profiles.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py \
  tests/community/endpoints/test_endpoint_runner.py \
  -k 'context_collab or collaboration/task/products'
```

Result: **458 passed**, 2,091 deselected, 19 warnings, 74.06 seconds. The selector
intentionally excludes unrelated endpoint cases and the standalone HTTP-layer
architecture check; the latter is covered by the complete architecture gate.
The new default suite covers actual SQL/engine/worker execution, fixed dependency
order, both failure kinds, all four Agent decisions, UUID replay, unknown dispatch,
removed Python arguments, invalid YAML and unavailable host capabilities.

The full architecture/API command recorded above was rerun against this change:
**524 passed**, 45 warnings, 90.03 seconds. Black checks all 18 changed/new Python
files; Ruff F rules pass on those files and the configured full rules pass on the
two new files. All changed/new source files stay below 1,000 lines. The repository
SAST block scan, plaintext-secret scan and `git diff --check` pass. Markdown checks
introduced no missing file targets; pre-existing missing links are not claimed fixed.

### Complete Backend CI for the follow-up

The unchanged `scripts/ci_test.sh` ran with four workers, against the temporary
validation tree `53c70c959c7cafd3c8addb72f3de1ed951b2224d` and baseline HEAD
`510a068bdb67ae295900bd12432c8ca6d8d5d263`. This tree includes all 18 changed/new
Python files (including tests); it was created with a temporary index and did
not stage or commit the user's work. Subsequent changes are Markdown-only.

```bash
PATH="$PWD/.venv/bin:$PATH" BACKEND_CI_PYTEST_WORKERS=4 \
  bash scripts/ci_test.sh --base HEAD \
  --head 53c70c959c7cafd3c8addb72f3de1ed951b2224d
```

| Check | Actual result |
| --- | --- |
| Pytest | **35,557 passed**, 41 skipped, 645 warnings; **13 subtests passed**, 629.01 seconds |
| Case pass rate | **100.00%**, required 100% |
| Overall line coverage | **90.80%**, required 75% |
| Changed executable-line coverage | **100.00% — 4/4**, required 80% |
| Outcome | **`backend CI gate passed`**, exit status 0 |

The small changed-line denominator is the coverage producer's executable-line
intersection, not all lines of the nested declarative default or a replacement
for the behavior tests. No coverage threshold, exclusion or gate was changed.
Reports are the ignored `apps/backend/pytest_report/TEST-junit.xml` and
`TEST-cov.xml`. Skipped/external-service checks are not integration evidence.
Live adaptation Agent, actual external isolation/notification/provider services,
Singlebox E2E, enterprise deployment and production behavior were **not verified**
in this follow-up; those boundaries remain as documented above.
