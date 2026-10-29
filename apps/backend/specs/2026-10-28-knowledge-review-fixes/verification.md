# Verification: Knowledge publication review fixes

Date: 2026-10-28 (Asia/Shanghai).

## Pinned scope

- PR #3177 repair base: `7e5454c5d8c50db378566b735e686ab23974bc1a`.
- Final production/test source: `960f46d0a9bb5887c77250d67fc4b31fce0da14d`.
- This user-approved scope includes legacy status admission; the older
  current-summary verification's explicit exclusion is historical, not this repair's scope.
- Working selection remains shared by draft/verification; production is separate.
  No new Plugin, worker, table, column, index, DDL or package dependency.

## Red/green evidence

- Root configuration: 5 malformed-policy cases failed to reject before the fix;
  real YAML quoted string and environment interpolation now reject at root DI.
  A valid disabled policy discovers and bootstraps the real publish lifecycle,
  registering `PROGRESS_POLL_TASK`.
- Original SQLite cost witnesses failed both legacy same-name source and disabled-
  history restore bounds before the keyed query corrections.
- Four subthreshold-artifact/max-selection combinations (ASCII and UTF-8, artifact
  before/after admission) failed before complete-ext budgeting; all now complete
  with the exact intended production set. Upload failure leaves DRAFT/no snapshot.
- Generic success writers/legacy handler failed refusal checks before the observed
  completion guard. Explicit observed completion still applies the frozen set;
  disabled native status writes retain their behavior.
- Independent Spec review found three further defects despite the first 2,817-test
  green run: result-size growth could create an unstaged marker; retrying supplied
  ext could erase concurrent metadata; shrinking a selection with a stored marker
  could break exact ext CAS. All three were reproduced as failing positive regressions
  before fixing them. The final artifact suite also proves rollback on protected-
  metadata growth after preflight: **17 passed**.
- Real normal TEST DI resolves `BotPublishServiceProtocol` to its configured
  concrete singleton, using the normal repository binding and enabled policy.
  Only the external DatabasePlugin boundary is supplied with real fixture SQLite;
  the Service/Repository are not hand-rebound for this configured-provider witness.

## Final integrated gate

**2,822 passed, 44 warnings, 0 failed, 0 skipped**, 187.68 seconds, on the final
source above. The earlier 2,817 pass and an interrupted intermediate rerun are not
claimed as final acceptance of the healed source.

From `apps/backend` with the existing local Python environment:

```bash
DEPLOY_PROFILE=test SERVER_ENV=dev PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest \
  tests/community/core/bot_knowledge \
  tests/community/repository/publishing \
  tests/community/core/service_bot/services \
  tests/community/core/service_bot/repository \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/endpoints/test_bot_common_config_router.py \
  tests/community/architecture tests/community/core/common_config \
  tests/community/repository/config tests/community/di tests/community/config \
  -q --disable-warnings
```

Ruff, repository blocking `singlebox/ci/python_sast_local.sh`/Flake8 against the
repair base, and `git diff --check` passed. ORM model and SQL files have no diff
against the repair base. The pre-existing oversized router waiver remains; no
new waiver was added. New helper is 62 lines, publish repository 962 lines.

## Cost and race boundaries

- Source PK locking retains tenant/env/native identity checks and SQLite's shared
  writer reservation. Restore uses enabled-index and exact unique-key ID seeks.
- Independent SQLite negative controls on the initial query fixes detected old
  growth: address seek 80 -> 18,494 VM steps; restore OR seek 347 -> 18,761. Keyed
  no-PUT admission and changing restore were constant at 528 and 848 VM steps in
  that controlled 2/2,048-row probe. These are not production timings.
- No new periodic work. Build recapture is capped at three attempts. Inline/no-
  artifact admission stays one short transaction; PUT or stored-marker normalization
  releases locks for remote I/O, then revalidates. Supplied ext never replays over
  a changed baseline. A final pending-key fence prevents committing an unstaged
  reference, including paths not covered by the compact-result reserve.
- Completion preflight reserves 2 KiB for compact Owner/status/reason/workflow
  metadata; canonical stored JSON and expected-ext comparison use zero reserve.
- Immutable orphan objects retain native per-record prefix cleanup. This bounds
  per-request attempts, not lock wait/OSS latency or cross-request object retention.

## Independent closure

- Standards: final incremental review found no evidenced P2+; normal-provider and
  lifecycle-bootstrap evidence confirmed. Existing router waiver was not expanded.
- Spec: the three independent boundary findings are closed at `960f46d0a`;
  reviewer independently ran their three positive regressions plus the final
  staged-key guard regression: **4 passed**, no new evidenced P2+.
- Performance: **PASS WITH RISKS**, no new evidenced P2+. Incremental independent
  witnesses confirmed marker-stays-offloaded normalization at 16 SQL/2 windows/
  2 GET/0 PUT, marker-to-inline at 16 SQL/2 windows/1 GET/0 PUT; continuous marker
  churn stays at three normalization GET and six windows (42 request-owned SQL).
  Successful marker return can add a fourth GET at the three-attempt boundary.
  All observed GET/PUT were outside the added transaction windows.
- The 2-KiB preflight reserve can upload an object that final canonical JSON does
  not reference (a 65,400-byte fixture demonstrated this). It is at most one such
  PUT per completion call and uses existing prefix deletion cleanup; no new TTL
  or cross-request retention guarantee is claimed.

## Explicit limits and operations

TEST/SQLite and controlled OSS boundaries are not deployed MySQL/OceanBase plans,
lock contention, multi-instance load, actual BaaS/OSS/ECB/MCP consumption, full HTTP
permission-wrapper or corp/pre/prod startup acceptance. Production schema/index
presence remains an operator prerequisite. No full repository/Singlebox stack,
remote CI or production acceptance is claimed. No feature enablement, remote push,
PR comment/vote/merge, deployment, permission escalation or production DDL occurred.


## Schema-verified shipped enablement follow-up (2026-10-28)

The user approved enabling this PR after a read-only ODC CLI check of the
operator-selected database, with no historical backfill. This supersedes the
prior default-off rollout decision; it does not claim a deployment occurred.
Internal connection identifiers and authenticated caller details are intentionally
not included in this public repository.

### Read-only schema evidence

ODC CLI returned success for all four metadata-only commands:
```sql
SHOW CREATE TABLE ac_bot_knowledge_binding;
SHOW CREATE TABLE ac_bot_common_config;
SHOW CREATE TABLE ac_bot_publish;
SHOW INDEX FROM ac_bots;
```
- Binding: `online_enabled tinyint(1) NOT NULL DEFAULT 0`; signed BIGINT PK,
  `revision`, Bot/space keys and utf8mb4-bin KB IDs match the portable model.
  `uk_bot_knowledge_binding(avernet_tenant,env,bot_pk,kb_id)`,
  `idx_bk_enabled(avernet_tenant,env,bot_pk,space_id,enabled,kb_id)` and
  `idx_bk_page(avernet_tenant,env,bot_pk,space_id,id)` exist.
- Common config: `config_value TEXT`, utf8mb4, soft-delete and timestamps exist;
  `uk_bot_config(bot_id,entity_id,env,config_key)` exists with full key columns.
- Publish: existing `ext VARCHAR(65535)` and
  `idx_sbp_e_s(source_bot_pk,env,status)` exist. No added native schema is needed.
- Source Bot: primary key and `uk_bot_id_entity_id_env` are present.

No application row was read or written, no permission application or DDL was
submitted, and no migration/backfill or historical repair was performed. These
checks establish metadata compatibility, not production query plans, runtime
credential write access, contention, remote provider health or end-to-end success.
Other deployment databases still require their own schema readiness checks.

### Code and validation

- Shipped `application.yaml` now explicitly enables publication integration.
  Missing-config/direct-object fallback stays false; explicit false remains
  supported. All three effective-config goldens now expect true. No business
  algorithm, ORM, SQL migration, historical UNKNOWN or consumer wiring changed.
- Before changing the YAML, the real shipped-policy bootstrap and normal
  configured service/repository witnesses failed their expected-enabled checks.
- After changing it, the affected integrated suites passed: **2,824 passed,
  44 warnings, 0 failed, 0 skipped**, 207.23 seconds. The command is the same
  affected-suite command recorded above.
- The configured-provider witness was additionally strengthened to prove actual
  observed successful completion persists the frozen production set, without
  monkeypatching the policy. Final focused config/DI/status/policy suites:
  **33 passed**, 18 warnings, 5.57 seconds.
- Singlebox coverage registration guard: **1 passed**, 0.11 seconds. The
  historical 66.32% floor is unchanged and is not relabelled as a fresh
  enabled-stack coverage measurement. No full Singlebox acceptance was run.
- Ruff and whitespace checks passed. Push must use the normal secret/SAST hook.

No merge, deployment, live config change, permission escalation or production
DDL is authorized or performed by this follow-up. Remote CI is not established.


## Failed online restart retry repair (2026-10-28)

- Repair base: `c3f841bcdc926dd8f3c213407e1b286d59f1e48b`.
- Final implementation/test source: `a3c8aee39291a736ee9ed2c046f48da8770d7214`.
- Original defect: an actual failed restart writes FAILED with source_status=success;
  retry needs to restore the stable state before issuing another restart. The generic
  SUCCESS observation guard incorrectly rejected that native admission.
- Red evidence: the real Flow/Service/Repository/operation-ledger witness produced
  **2 failures with integration enabled, 2 passes disabled**, with/without KBs.
  No new restart had been issued at the failure.
- Dedicated internal `restore_failed_online_restart(publish_id, expected_ext)`
  validates current FAILED/source_status=success, environment, complete ext and
  latest failed online restart operation. Recreate uses the native observer's
  abandoned RESTART plus matching failed FIRST_RELEASE workflow. The entry itself
  creates retry=True and restores SUCCESS; it does not accept arbitrary ext, a
  target state, a bypass flag or fabricated observed workflow.
- The normal generic SUCCESS gate and old HTTP status behavior remain protected.
  Recovery performs no KB/summary/snapshot/result changes, even for empty selection.
  Disabled policy retains native CAS behavior. Changed artifacts stage outside the
  explicit transaction; upload or commit failure precedes new restart issuance.
- Final focused suite: **223 passed**, 18 warnings, 18.91 seconds. Includes real
  retry with/without KBs, normal/recreated target, enabled/disabled policy; ledger
  state/stage/env/workflow/latest-attempt negatives, wrong source, stale ext/env,
  completion replay, commit rollback, no-KB-schema admission, artifact budget/PUT
  failure, ordinary HTTP success refusal and normal configured Service resolution.
- Final affected integrated suites: **2,848 passed, 44 warnings, 0 failed,
  0 skipped**, 211.20 seconds. Uses the same affected-suite command above.
- Ruff, blocking SAST/Flake8 and whitespace checks passed. Main publish repository
  is 985 lines, helper is 64; existing service waiver is unchanged. No model, SQL,
  configuration/default, migration, dependency, worker or HTTP route change in
  this repair. Full remote-provider/prod/Singlebox/multi-instance acceptance and
  remote CI are not established. No merge/deployment/live DB operation.

### Independent repair closure

- Standards: incremental review found no evidenced P2+ or new hard-rule violation;
  normal DI wiring and generic API separation confirmed, 43 targeted tests passed.
- Spec: original P2 closed; no new confirmed P2+. Independent original/permanent
  witness plus Flow suites: 213 passed; guard/restore suites: 32 passed (overlap).
  An additional witness kept native execute_restart and operation runner intact
  and drove normal upgrade, direct recreate and BOT_NOT_FOUND recreate through
  two consecutive failure/retry cycles: 3 passed, knowledge state unchanged.
- Those three native-delivery scenarios are now permanent tests at
  `tests/community/repository/publishing/test_knowledge_restart_retry_delivery.py`
  (test-only commit `c678a5769`). No production source changed after review.
- Performance: PASS WITH RISKS, no new evidenced P2+. No-artifact admission
  measured 4 SQL/1 transaction/1 publish UPDATE, recreate 5 SQL. No Bot/KB/summary/
  operation write. At 1 versus 10,000 operation rows, local work remained 210/277
  SQLite VM steps respectively. Replay after success: 1 SELECT/zero writes.
  A budget-triggered offload measured 6 SQL and 1 PUT/1 GET outside locks.
  Production operation plans, lock contention and multi-instance remain unknown.

### Final integrated gate including native delivery regressions

At `c678a5769b77be23a20aefbc6f2c60a4c5a09365` (same reviewed production
source plus the three permanent delivery tests), the full affected-suite command
passed **2,851 tests, 44 warnings, 0 failed, 0 skipped**, 201.84 seconds.
This supersedes 2,848 as the final integrated test count for this repair. Ruff,
blocking SAST and whitespace checks passed. Documentation closure follows this
source; normal remote push hooks remain required. No production acceptance, merge,
deployment, live configuration or DB operation is claimed.


## Legacy-Owner draft-restore compatibility closure (2026-10-29)

- Repair base: `81204810d27c7817c1db84b1106466f1c6821107`.
- Reviewed implementation/test source: `a496ad647a3720bfb6637660f231dbf1e47bd802`.
  The delivery follow-up changes verification documents only.
- Original defect: native PK-based resolution accepts legacy publication Owner
  metadata, but knowledge completion rejected it after the external draft restore
  had already succeeded, incorrectly failing the native operation ledger.
- The repair separates native source locking from knowledge snapshot trust.
  PK/Bot ID/env/tenant/live service-Bot checks, writer reservation and locked
  refresh remain. Build and online completion retain strict entity validation.
  Draft or previous-publication entity mismatch now records knowledge UNKNOWN
  with `KNOWLEDGE_PUBLISH_SOURCE_MISMATCH`, preserves all selection flags and
  revisions, and completes the native restore ledger atomically. No history
  fallback/backfill, schema, plugin, startup or shipped-policy change.
- Permanent red evidence: before the implementation, the selected regression
  cases returned **4 failed, 8 passed**; enabled native legacy-Owner restore and
  draft/previous/both metadata cases failed. The disabled native path passed.
- Final permanent regression file: **17 passed**. Focused restore/native suites:
  **62 passed**; broader affected publishing/restore suites: **317 passed**.
  Counts overlap each other and the full suite; do not add them together.
- Standards/Spec/Performance incremental reviews were completed before this
  resumed delivery, with no new evidenced P2+ reported. This resumed run keeps
  their implementation/test source unchanged. Performance remains PASS WITH
  RISKS; no production plan/locking/load acceptance is implied.
- This resumed run also reran the independent reviewer's original positive
  enabled/disabled native-PK witness: **2 passed**. The old expected-failure
  witness was intentionally deselected, not converted into a passing regression.
- First complete Backend run: **1 failed, 27,464 passed, 40 existing skipped,
  2 subtests passed**, 469.14 seconds. Its sole failure was the unchanged
  AgentSwarm native Bot creation E2E returning APPLY_FAILED, not the earlier
  timer-tick assertion. That exact test then passed in isolation (**1 passed**,
  1.76 seconds). Its root cause was not established; this failed full run is
  not green and the isolated rerun does not replace the full gate.
- Complete unchanged-runner retry at the same source passed: **27,465 passed,
  40 existing skipped, 2 subtests passed**, 496.81 seconds. Case pass rate
  **100.00%**, line coverage **90.60%**, changed-line coverage **95.14% (704/740)**;
  required floors remain 75% / 80%. The previously failed AgentSwarm case also
  passed in this full run. No source, test, skip, exclusion, timeout, worker-count
  or threshold adjustment was made between the two full runs.

  ```sh
  # Python 3.12; reuse already installed dependencies, normal four-worker runner.
  BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=4 \
  PYTHONDONTWRITEBYTECODE=1 SERVER_ENV=dev \
    apps/backend/scripts/ci_test.sh \
      --base d381ddc926e50e0a23ebea13b7792d8e4b28a1c1 \
      --head a496ad647a3720bfb6637660f231dbf1e47bd802
  ```

- Latest fetched release target remained
  `REL20261029@f14bc3043cab0aef1e3e2e3a530adde270449314`; merge-tree verification
  found no conflict (tree `42d81fcff763b53caf5b5a677898213a7c354562`).
  The local full gate tests the exact feature source based on `d381ddc926e`,
  not the combined newer release tree. Fresh remote merge-state Backend and
  Singlebox checks remain required for the delivered Head.
- Whitespace validation and source-file size checks passed (596-line helper,
  217-line regression file). Original-PR delivery must retain the normal
  plaintext-secret and blocking SAST pre-push gates. Default push hooks are
  lint-only; the complete Backend run above is separate evidence.
- Prior schema-only checks, SQLite/local boundary evidence and prior-Head CI
  do not establish current runtime DB write access, MySQL/OceanBase query plans,
  lock contention/multi-instance load, actual BaaS/OSS/ECB/MCP behavior or deployed
  acceptance. No merge, deployment, live configuration change, production DML/DDL,
  permission operation or historical backfill is performed by this repair.


## Release-target conflict resolution (2026-10-29)

- Previous delivered Head: `3391deafd37683d3a884d3330282fec8494d31f6`.
  Its applicable remote checks had all succeeded when this follow-up began;
  those checks do not validate the rebased Head.
- Rebased all 36 feature commits onto
  `REL20261029@19c10cfca422bacd4aedade8e0f04ff95c8bdcdd`.
  Rebased implementation/test tree before this documentation-only closure:
  `332e17c6154f93537fd9b440cc9551325463ec7f`.
- Two content conflicts were resolved without choosing either complete file:
  repository README retains both the target's read-only manager-sync contract
  and this PR's bounded knowledge publication persistence contract; Singlebox
  effective-config golden retains both manager-sync and knowledge publication.
  The approved knowledge publication policy remains enabled.
- Range-diff retains all 36 commits. Its changed patch contexts are confined to
  these README/golden integrations; no original production-code patch changed.
  The final tree differs from the original current-target merge-tree only in
  the two manually resolved files. Community/test/singlebox JSON comparisons
  also prove each target golden is preserved, with only the expected enabled
  knowledge publication mapping added.
- DI root automatically integrates the target's manager-sync assembly with this
  PR's early KnowledgePublicationPolicy validation. No new plugin, schema,
  dependency, worker, rollout-config change, knowledge backfill or scope expansion
  was introduced by conflict resolution. Existing online observation, native
  restart retry and legacy-Owner UNKNOWN restore protections are retained.
- Targeted configuration, DI, architecture and publishing regressions:
  **1,233 passed, 44 warnings**, 145.68 seconds.
- Complete Backend CI at rebased source `332e17c6154f93537fd9b440cc9551325463ec7f`:
  **27,755 passed, 40 existing skipped, 2 subtests passed**, 494.99 seconds.
  Case pass rate **100.00%**, line coverage **90.65%**, changed-line coverage
  **95.14% (704/740)**. Original 75% / 80% coverage floors, normal four-worker
  runner, already installed dependencies reused; no exclusions, timeout or
  threshold changes. Targeted counts overlap the complete suite.

  ```sh
  BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=4 \
  PYTHONDONTWRITEBYTECODE=1 SERVER_ENV=dev \
    apps/backend/scripts/ci_test.sh \
      --base 19c10cfca422bacd4aedade8e0f04ff95c8bdcdd \
      --head 332e17c6154f93537fd9b440cc9551325463ec7f
  ```

- Whitespace validation and conflict-marker checks passed. Original branch must
  be updated using an exact lease on the previously verified remote Head and
  normal plaintext-secret/blocking-SAST hooks. Fresh remote checks must validate
  the rewritten Head; former-Head results are historical.
- This only resolves release integration and records validation. No merge,
  deployment, live configuration or production DML/DDL operation is performed.
  Production query plans, lock contention, multi-instance load and real external
  provider/corp/prod acceptance remain outside this local CI evidence.


## Knowledge AGENTS.md release integration (2026-10-29)

- Previous delivered Head: `762f4bda79cf30433d74412a1cd8379cb0343caf`.
  Its applicable remote checks had all succeeded when this follow-up began.
- Rebased onto `REL20261029@a0ac8382e0d13b78a3a0b7aa6a023440d7d71654`,
  which includes #3259 connector-state reads and #3260 knowledge AGENTS.md sync.
  Rebased Backend implementation/test source:
  `47ac25bee642746be11e4e07efa324863a9bdd34`.
- Both conflicts concern obsolete Singlebox registration: the target already
  moved bot_knowledge into pending_modules because its SQL-seeded stories lack
  a device binding for the new identity-file projection. Preserve the target's
  manifest and pending-registration guard exactly; do not resurrect this PR's
  old 66.32% registration. The superseded CI-only rebaseline commit became empty
  and was dropped; all feature/repair behavior remains. Range-diff otherwise
  shows contextual router/README integration and removal of obsolete manifest
  commentary, not changes to the original publication implementation patches.
- Before documentation/guard cleanup, the rebased tree differs from the original
  current-target merge-tree only at the two resolved CI files. The normally
  wired enablement route retains the target's background AGENTS.md projection
  and this PR's stage-aware read API. Shipped publication enablement, observed
  completion, atomic selection bookkeeping, native failed-restart retry and
  legacy-Owner UNKNOWN restore remain unchanged.
- Corrected the module README's stale claim that bot_knowledge participates in
  default Singlebox measurement. A green default Singlebox run no longer proves
  this module's device-bound acceptance. Re-registration requires actual
  device-bound stories and a fresh complete-module measurement; historical
  percentages must not be reported as a current gate.
- Manifest/report validation initially returned 1 failed / 26 passed because
  the target's expected default-module list omitted its existing manager_sync
  module. An archive of the exact target reproduced the same assertion failure
  (1 failed). The bounded repair adds only manager_sync to that expected list;
  it does not change module selection, thresholds, exclusions or acceptance.
  Registration/manifest/report suites then passed **27 tests**, 0.36 seconds.
- Focused config, DI, knowledge selection and AGENTS.md projection, router,
  draft-restore and native-retry suites passed **731 tests, 19 warnings**,
  50.50 seconds. Counts overlap the complete Backend suite.
- Complete Backend CI at source `47ac25bee642746be11e4e07efa324863a9bdd34`
  passed **27,793 tests, 40 existing skipped, 2 subtests passed**, 517.45 seconds.
  Case pass rate **100.00%**, line coverage **90.66%**, changed-line coverage
  **95.14% (704/740)**; required floors remain 75% / 80%. Normal four-worker
  runner and installed dependencies reused, with no test exclusion, timeout,
  worker-count or threshold adjustment. Final delivery adds only this verification,
  the acceptance-scope README correction, and the separately tested one-line
  expected-module-list repair; no Backend implementation/test changes follow
  that complete run.

  ```sh
  BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=4 \
  PYTHONDONTWRITEBYTECODE=1 SERVER_ENV=dev \
    apps/backend/scripts/ci_test.sh \
      --base a0ac8382e0d13b78a3a0b7aa6a023440d7d71654 \
      --head 47ac25bee642746be11e4e07efa324863a9bdd34
  ```

- Whitespace/conflict-marker checks pass. Delivery requires normal plaintext
  secret and blocking-SAST hooks plus an exact lease on the old verified remote
  Head. Fresh remote checks must validate the new Head; they cannot inherit
  former-Head greens. Target-owned pending registration means default Singlebox
  success is not complete knowledge-module device-bound acceptance.
- No merge/deployment, live config, production DML/DDL, backfill, new Plugin,
  schema or worker. Production plans/locks/multi-instance behavior and real
  external-provider/corp/prod acceptance remain unverified.
