---
agent: tc-review
status: approved-for-implementation
created: 2026-11-04T22:00:00+08:00
iteration: 1
---

# Spec: Retry a failed service draft after its current binding is lost

## Requirement

A failed service Bot with no current device binding must be able to retry device allocation using the Provider of its most recent trustworthy draft binding. Caller and published runtimes belong to separate lifecycle scopes and must neither block this draft retry nor supply its Provider. The user authorized implementation, release-branch rebase, and PR creation; this task does not deploy or alter production data.

## Existing chain and change boundary

`BotService.restart_bot` validates lifecycle state, resolves the current or historical Provider, acquires the existing durable restart lock, and calls `start_bot`. The background allocation path writes the new binding and device only after successful allocation. Its failure path retains no current binding and records `FAILED`. No HTTP shape, lifecycle lock, allocation adapter, Provider routing policy, or persistence schema changes are required.

Allowed additions are a small helper owned by bot management for classifying historical binding scope, minimal wiring in the existing historical Provider resolver, and focused tests. Router, Expert Chat, Caller provisioning, published-stage selection, external SDKs, and OCB gitlinks are outside the change boundary.

## Coding Spec

### Behavior

- [ ] Preserve historical lookup's existing environment, entity ID, entity type, complete Bot ID, and newest-first pagination constraints.
- [ ] For service Bots, reject cross-identity records and recover a Provider only from a positively identified managed draft binding.
- [ ] Skip confidently identified Caller, published, and evaluation bindings before applying draft lifecycle status checks.
- [ ] Treat an unknown matching service binding scope as unavailable rather than guessing it is a draft or falling through to allocation rollout.
- [ ] Preserve existing personal-Bot historical-recovery behavior; this patch must not require new metadata on legacy personal records.
- [ ] A trustworthy latest draft in `RELEASED`, `FAILED`, or `STOPPED` may provide its historical Provider. A live draft (`ACTIVE`/`PENDING`) still blocks replacement. Missing/empty Provider still blocks recovery.
- [ ] Old history supplies only the Provider: it must not be reactivated, copied into the Bot's current binding, or upgraded in place.
- [ ] Reuse existing restart lock, Provider policy, allocation, successful new-binding writeback, and failure cleanup. Another explicit retry remains possible after allocation failure releases the lock.

### Reliable scope classification using current persisted models

The release model does not contain a universal `stage` field on every binding. Do not interpret an absent stage as `draft`, infer scope from the Provider, parse device-ID naming conventions, or match free-text application descriptions.

Use the existing writers' persisted characteristics:

1. `ExpertChatInstanceService` writes a machine-readable application reason of `caller_instance:<complete bot_id>`. An exact target Caller marker is a separate scope and is skipped, irrespective of its live or terminal status. Do not use a loose substring search.
2. Explicit non-draft stage, nonempty evaluation/default-tag metadata, and the exact Caller marker identify foreign scopes before positive draft checks. `publish_id` by itself does not identify a published-stage runtime: `BaasDeviceService._do_allocate` also writes it for managed drafts. Do not reject a managed draft merely because it contains `publish_id` or other provider operation references.
3. Managed drafts are written through `DeviceService.apply_device`. This lifecycle always persists a nonempty string `callback_token`, `entity_id`, `entity_type`, and exact `bolt_id` in `device_props`. Require this positive lifecycle identity for service recovery, with entity values matching the current immutable resource identity. Check the credential's presence/type only; never return or log its value. Current published and Caller writers do not create this managed-draft identity tuple. After the known explicit foreign markers are excluded, a complete managed-draft tuple takes precedence over generic Provider-operation metadata such as `publish_id`. A record lacking the draft tuple but carrying published operation markers (`publish_id`, `baas_publish_id`, `destroy_publish_id`) is a known foreign publication record and is skipped. A record lacking both remains unknown.
4. Repository lookup already scopes real `DeviceBindingRecord` rows by environment/entity. Keep that contract and test its exact arguments. If the helper validates record identity as well, require explicit service fixture identity rather than weakening production checks for incomplete test doubles.
5. A matching record that has neither a known foreign marker nor the positive managed-draft tuple is unknown; fail closed with an explanatory diagnostic. Do not add a publication-history scan, new repository method, new stored field, or schema migration solely for this classification.

### Key method abstraction

| Method / unit | Layer | Responsibility | Input / output | Side effects |
|---|---|---|---|---|
| Historical binding scope classifier (small helper, implementation chooses local naming) | Bot management Core | Identify managed service draft vs known foreign scope vs unknown using existing persisted metadata | Binding record and exact Bot/resource identity; classification or boolean decisions | No network or writes; diagnostic decisions contain no credentials |
| `BotService._resolve_historical_unbound_restart_provider` | Bot lifecycle application service | Page existing repository, filter foreign scopes, reject unsafe drafts/unknowns, return trustworthy original Provider | Bot type plus existing identity; `str` Provider or `None` | Existing read-only repository calls and minimal diagnostics |
| `BotService.restart_bot` | Bot lifecycle application service | Pass correct Bot type/identity, preserve restart orchestration and lock behavior | Existing restart request; existing response/errors | Existing lifecycle operations only |

Keep helper policy transport-agnostic. Do not move business selection into routers or device adapters.

### Domain models

No new persisted model, API field, or database column is required. The existing `DeviceBindingRecord` is sufficient:

| Existing field | Type | Required for this use | Meaning |
|---|---|---|---|
| `id` | positive integer | yes | Audit identifier; never reattach it as the new binding |
| `entity_id`, `entity_type`, `env` | string | yes in real repository records | Immutable resource scope, distinct from mutable Bot Owner |
| `device_props.bolt_id` | string | yes | Exact Bot identity within resource scope |
| `device_props.entity_id`, `entity_type` | string | service managed-draft proof | Lifecycle metadata checked against current resource coordinates |
| `device_props.callback_token` | nonempty string | service managed-draft proof | Existing creation callback credential; presence only, never exposed |
| `apply_reason` | optional string | Caller marker when present | Exact machine-readable Caller reason, not general text matching |
| Published/evaluation/stage metadata | existing optional values | interpreted with positive lifecycle proof | Explicit foreign runtime markers exclude; generic `publish_id` alone does not override a managed draft identity |
| `status` | lifecycle enum/string | yes | Terminal history allowed; live draft blocks duplicate allocation |
| `device_provider` | nonempty string | recovery result | Passed through existing restart Provider policy |

Invariant: a historical binding is read-only evidence. Successful retry establishes its own current binding through existing allocation. No combination of foreign-scope activity and old draft history may silently retarget a restart to that foreign runtime.

### Diagnostic logs

Use the existing project logger. Record exact Bot/resource identity, selected or rejected binding ID, scope/skip reason, Provider when selected, and lifecycle status. Add diagnostics for skipped foreign scope and unknown/rejected identity only where they explain recovery decisions. Never dump `device_props`, `callback_token`, headers, or complete records. Tests must assert decision/identity fields and that a sentinel credential is absent from output.

No external system boundary is added or modified: existing SDK requests, HTTP response shape, and allocation lifecycle are reused. Therefore new request/response payload logging or recursive sanitization infrastructure is not needed.

### Allowed files

| Path | Expected change |
|---|---|
| `apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py` | Minimal helper invocation and Bot-type context in historical recovery |
| A focused helper under the same `core/bot_management` module | Optional small scope-policy unit, avoiding further monolithic growth |
| `apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py` | Add/update recovery integration cases and accurate fixtures |
| A focused helper test file in the same tests subtree | Scope/status/pagination/logging cases as needed |
| `apps/backend/src/agentclaw/community/core/bot_management/README.md` | Only if needed to document new internal unit or policy |
| `apps/backend/specs/failed-draft-restart/` | This spec and execution/review reports |

Do not change OCB merely to advance a gitlink, public test thresholds, unrelated router/frontend/chat behavior, ARCA resource configuration, or lock semantics.

### Acceptance criteria

- [ ] A failed unbound service with newer live Caller/published/eval bindings and older terminal managed draft history restarts with the draft Provider.
- [ ] New allocation is reached once under the durable lock; no old binding is copied or in-place-restarted.
- [ ] Unknown scope, wrong resource identity, a live latest draft, missing Provider, and absent history yield the existing refusal without allocating.
- [ ] Pagination finds a trustworthy draft beyond the first page after skipping known foreign scopes.
- [ ] Allocation failure leaves failure observable, releases the lock, and permits another attempt through the same resolver.
- [ ] Existing personal, bound-Bot, concurrent restart, and Provider-policy tests remain passing.
- [ ] New helper business-unit line coverage exceeds 90%; changed executable lines are at least 90% covered. Report measured numerator/denominator, not an assumed percentage. For the pre-existing oversized BotService, cover each touched wiring branch rather than changing whole-file exclusions or thresholds.
- [ ] Targeted lint, unused-import/variable checks, and `git diff --check` pass. Do not expand an already oversized service through unrelated refactoring.

## Review Spec

Reject Provider-based stage guesses, fallback-to-default allocation when service history is unknown, treating all `bolt_id` matches as drafts, reattaching released IDs, selecting Caller/published IDs as current, weakened live-draft rejection, skipped pagination, secret logging, or changes to unrelated boundaries.

Verify the new classifier is reachable through the public restart operation. Check both selected Provider and side effects: lock acquisition, one allocation, successful new-binding writeback, no mutation of historical records, and no allocation on refusal. Preserve immutable entity coordinates independently from Owner permissions.

Review the existing generic personal tests for compatibility rather than forcing all old rows to carry service metadata. New service fixtures must model the real creator's persisted fields.

## QA / Regression Spec

| Case | Setup / action | Expected result |
|---|---|---|
| TC-01 | FAILED service, no current binding; newest Caller PENDING then terminal managed draft | Caller skipped; draft Provider used for one fresh allocation |
| TC-02 | Newer ACTIVE/RELEASED published or eval rows; managed BaaS draft also has `publish_id` | Explicit Caller/eval/non-draft markers win; managed draft Provider-operation IDs do not cause false exclusion |
| TC-03 | Latest known draft ACTIVE or PENDING, older terminal draft | Refuse; no duplicate allocation and no fallback to older draft |
| TC-04 | No matching history, unknown matching scope, missing lifecycle identity, or missing Provider | Existing FAILED_WITHOUT_BINDING refusal; no allocation |
| TC-05 | Rows for another Bot/entity/environment | Never selected; exact repository scope asserted |
| TC-06 | First page contains only unrelated/foreign rows; later page has terminal draft | Correct pagination and draft recovery |
| TC-07 | Existing valid personal-Bot history without service-only metadata | Prior personal recovery behavior preserved |
| TC-08 | Concurrent retry requests | Existing durable lock permits only one allocation |
| TC-09 | Allocation fails, then next explicit retry succeeds | Failure and lock cleanup preserved; eventual new binding written; old ID untouched |
| TC-10 | Skip/reject/recover logs with sentinel credential in historical props | Useful identity/reason visible; credential absent |

Use synthetic local fixtures and mocks; no production IDs, raw logs, credentials, or company-only services belong in the public change. Run baseline restart idempotency tests, new focused tests, affected Bot lifecycle tests, and repository-required static gates. Record limitations accurately.

## Ship Spec

- Source repository: GitHub `inclusionAI/Avernet`.
- Worktree: isolated Avernet worktree provided by the main agent.
- Topic: `fix/failed-draft-restart-rel20261105`.
- Base: freshly fetched `github/REL20261105`; replay only task commits, verify the final diff and commit range.
- Create an English PR with Problem / Solution / Validation and accurate checks. Address actual CI failures and reasonable review findings without weakening gates.
- No OCB worktree/gitlink update, merge, deployment, production correction, or remote restart is authorized by this task.
- Rollback: revert this focused source change; no data migration or historical binding mutation requires reversal.

## CI repair addendum: deterministic drain-test synchronization

The user also authorized fixing failures encountered during PR validation. The first remote Backend run failed only in the pre-existing `tests/community/core/agentswarm/runtime/worker/test_drain_breaker.py::TestRealHandlerDrain::test_within_grace_real_slice_completes`; feature tests passed. This test waits for inflight bookkeeping before the actual handler initialization completes, letting slow CI startup consume its 3-second drain grace.

Allow a test-only repair in that file: wait for the real fake subagent to enter its blocking gate before starting drain, then release it after claiming stops. Keep the existing 3-second grace, final COMPLETED/final-answer/DONE/no-error assertions, and over-grace breaker tests. Bound preparation waits and clean up tasks on failure. Prove the old race and new behavior using the same injected 3.2-second real repository initialization delay; run the full drain test file plus the existing 109 feature/restart cases. Do not modify AgentSwarm runtime, product semantics, or CI thresholds.
