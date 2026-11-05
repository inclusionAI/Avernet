# Native task Caller inheritance (BCS compatibility path)

## Problem and scope

The native `bcs_assign_task` tool sends `task.dispatch` without a parent Run or
invocation ID. Previously this entered ordinary task admission with no Caller
metadata. A Caller-enabled Worker then correctly rejected ordinary content with
`CALLER_EXECUTION_UNAVAILABLE`. Security-check success, a queued task ID, and a
completed Manager result-delivery Run do not prove that the Worker executed.

This change is BCS-only production behavior. OpenClaw plugin production files
remain unchanged. It adds one inbound Service API method,
`MessageFlowService::handle_native_task_dispatch`, used by the authenticated WS
adapter. Existing task RPC fields, identity Plugin API, storage schema and
Managed task/result lifecycle remain unchanged. The additive Service API entry
is released as `bcs-service-api` 3.1.0 (previously 3.0.0); workspace path consumers
rebuild together. The default preserves source compatibility for existing
implementations. No Plugin API or wire version changes.

## Identity and execution are distinct

```text
Human ingress -> accepted Owner master ordinary Run
              -> BCS delegation-only lineage / HumanOriginHandle
              -> native assignment -> existing protected Caller Worker pipeline

Human ingress -> accepted Caller master protected Run
              -> exact BCS ParentExecutionTokenRef (Run + attempt)
              -> native assignment -> existing protected Caller Worker pipeline
```

An Owner master does not get a synthetic execution token or Caller container.
Its normal container message is unchanged. The common path is trusted parent
resolution and protected Worker delivery, not pretending that an Owner executed
as a Caller. The Worker still follows container preparation, identity issuance,
protected send and Managed acceptance/terminal processing. A submitted send is
not itself an execution-completion receipt.

For the native path, a newer Human session capture must never replace the
parent Run's identity. Neither `prepare_session_capture_fallback` nor the legacy
relay-capture handoff is used. Existing coordination/relay compatibility retains
its own behavior; this fix does not redefine it.

## Native correlation contract

1. The existing Bot-event boundary validates authenticated Bot, Group/Session,
   live producer identity and accepted Run aliases before observing a tool start.
   Observation does not dispatch a task.
2. Record `bcs_assign_task` starts by a SHA-256 fingerprint of authenticated Bot,
   canonical Group/Session, exact raw `target_bot`, `message`, `response_mode`
   and `timeout_s`. Absent optional values and JSON null compare equivalently.
   Names are not normalized to guess another invocation. Full task text is not
   retained in this index.
3. The WS RPC adapter passes the registered Bot and request ID to the new Service
   API; it does not accept model-supplied parent/attempt/invocation authority.
   Existing Manager role, Session state, participant and target checks still run.
   Target Caller-configuration read failures propagate, rather than selecting Owner.
4. A Caller target requires one unambiguous prior matching start. Atomically
   claim it before awaiting external reads. Exact start retransmission does not
   renew freshness. Another start with the same arguments poisons the candidate,
   including an accepted start that has lost/missing lineage or a malformed
   invocation ID. No waiting for a later frame on the serial WS handler.
5. Resolve the exact canonical parent delivery (at most two rows), live Run
   context and current lineage. Check Bot/Group/Session/environment, canonical
   Run, observed attempt, deadline and active Managed state. A parent known to be
   Managed cannot become a workflow parent merely because its row disappeared.
   Workflow parents require their own current lineage and Run context.
6. Caller parents require their exact registered execution material. A durable
   Caller marker cannot downgrade to a Human origin if its material is lost.
   Owner parents use existing delegation-only lineage. Reserve the existing
   bounded invocation allowance before admitting the child.
7. Persist only existing non-secret parent/invocation fields on the normal task
   admission; the protected pipeline resolves and rechecks material at dispatch.
   Failures and uncertain admission outcomes never refund the claim/allowance.

Request receipts bind authenticated Bot + request ID to the complete command.
A completed repeat returns the original task result without replaying delivery
side effects. Changed input, pending/failed/uncertain repeats are refused. A late
completion cannot overwrite a replacement receipt after its retention expires.
The existing lineage invocation reservation rejects a second reservation, even
when the request ID and arguments change; it is not an idempotent admission
permit. No additional invocation ledger is introduced.

### Bounded compatibility, not a new exactly-once protocol

The legacy wire has **no shared invocation key**. Therefore this compatibility
path cannot universally distinguish concurrent identical assignments from
changed-request-ID replay. It favors refusal over guessing a parent's identity.

- A start is matchable for 30 seconds from BCS observation, not a wire timestamp.
- Candidate tombstones last until the later of ten minutes or the observed
  lineage deadline. A legitimate identical-argument assignment can be refused
  during this period even if it is a new tool invocation.
- Request receipts last ten minutes. They are process-local, not durable
  idempotency across restart, failover or arbitrary delayed replay.
- Correlation stores are bounded (4,096 entries per index); saturation refuses
  new work rather than evicting live evidence. Each access prunes at most 64
  expired entries. A large expiry backlog may conservatively refuse work until
  subsequent accesses catch up. Native request receipt capacity also applies to
  ordinary targets using this entry point.
- The actual SDK must deliver its tool-start callback **before** executing the
  native RPC, and both must reach the same BCS lineage owner. If it does not,
  the RPC fails immediately before protected admission. No polling, retry loop
  or later-frame wait is introduced to hide that incompatibility.
- There is no atomic transaction between parent terminalization and child
  admission. The parent preflight and existing dispatch-time material checks
  remain separate. A queued child can still fail if identity expires/is lost.

The plugin compatibility test verifies that the unchanged plugin forwards an
SDK-provided start before its RPC. Its SDK is a test double; it does **not** prove
scheduling guarantees for every deployed OpenClaw SDK version. Deployments that
need unrestricted same-argument concurrency or durable invocation correlation
need a separately versioned wire change, not a session-speaker fallback.

### Composition compatibility

The production `BcsMessageFlow` override performs this correlation when the
configured target is Caller-enabled. Owner targets keep ordinary delivery.
Caller identity, lineage and Managed runtime dependencies must be wired by the
existing composition root. Without Caller admission support, the method retains
ordinary behavior and the existing delivery gate still refuses Caller content.
The Service API's default implementation intentionally provides only that legacy
ordinary behavior, discarding any supplied delegation; it does not promise
Caller inheritance or the production prequeue checks.

## Storage, cache and concurrency cost

These are incremental costs, in addition to existing role/participant checks,
interceptors, canonical message writes and task admission:

| Stage | Added access cost |
| --- | --- |
| Accepted tool start | Local bounded index; reuses the Managed-row result already read by event validation. No new SQL/cache I/O. |
| Native target configuration | One existing read-through configuration lookup. Local cache hit: zero SQL. Miss: one projected `SELECT ... LIMIT 1` (at most one row). Same-process sync invalidates the cache; existing cross-process TTL remains. |
| Claimed Caller parent | One environment/Bot/Session/canonical-Run query, `LIMIT 2`, rather than the unbounded legacy Run/request/downstream-alias UNION. No per-child history scan. |
| Run context | Memory lookup, or Redis context + terminal-marker GETs (two on the normal hit). Existing alias fallback is bounded: up to seven GETs total on a miss/racing refill. No hash/queue scan in this preflight. |
| Successful request replay / pending or mutated replay | Local receipt lookup, no additional admission/DB work. |
| Missing/ambiguous/expired start | No parent lookup or task write; preceding target/role checks still have their normal costs. |
| Admission / Worker dispatch | Existing Managed task transaction and protected pipeline; no new tables, migration, secondary child ledger or retry scanner. |

Different requests may execute preflights concurrently. Each matching candidate
is consumed under a local lock before parent-validation I/O. After those reads,
the existing lineage directory atomically reserves the invocation before child
admission. Neither lock is held over DB or network I/O, and expiry cleanup is
bounded. Config lookup errors propagate. The
existing lineage directory may sweep its bounded 1,024-entry production map.
Sustained failures consume bounded receipts/claims; they do not trigger retries
or repeated admission of the same request. This is an access-cost inspection,
not a throughput measurement: memory-backed regression tests do not establish
real MySQL/Redis latency, query plans, pool behavior or load scalability.

## Verification seams

- `caller_owner_relay_contract` uses real Human ingress, task application entry,
  Managed admission/runtime and the protected pipeline. Recording Gateway,
  identity and transport ports replace only external I/O. It checks Owner's
  ordinary send/no token/no Caller container, Caller Worker's protected send,
  the original Human despite a newer speaker, and exact Caller-parent exchange.
- Negative cases cover ambiguous/unbound starts, reordered RPC/start, one-use
  claim/replay, changed parameters/scopes, forged parent input, changed attempt,
  expired/terminal parent and lost Caller material. Refusal leaves no child row.
- Existing queued-task and coordination tests retain nameless BaaS result
  compatibility, ordinary task/result behavior and Caller delivery gating.
- `native-task-ordering.test.ts` exercises real plugin forwarding/RPC encoding
  with a stub SDK and socket. No plugin production source is changed.

Private identity-provider issuance and deployed SDK scheduling require staging
validation. No production deployment or document write is part of these tests.

## Local validation (2026-11-05)

Completed checks for this change:

| Check | Result |
| --- | --- |
| `caller_owner_relay_contract`, including the invocation-reuse regression | 17 cases passed across focused runs; the original Owner-to-Caller reproduction failed before the fix. |
| Caller pipeline/runtime, queued-task and coordination contract suites | 150 passed. |
| `cargo test -p bcs-message-flow --lib caller_ --offline` | 35 passed. |
| `cargo test -p bcs-ws --test frame_compat task_ --offline` | 11 passed; 10 pre-existing ignored cases. |
| Plugin `npm run test-local -- test/native-task-ordering.test.ts` | 1 passed; production plugin sources unchanged. |
| `bash scripts/ci/check-config-validation.sh` | 148 passed on the elevated rerun. The sandboxed run could not bind the telemetry test's local TCP listener. |
| `bash scripts/ci/tests/import_scope_regression.sh` | 11 passed. The original gate failed the three integration-test fixtures; production and contract violations remain rejected. |
| Port purity, forbidden symbols, store boundaries, interceptor chain, protocol compatibility | Passed. |
| `git diff --check` and this change's source-file line counts | Passed; each source file owned by this change is at most 1,000 lines. |

The import checker now excludes only the standard crate-level integration-test
composition directories, consistent with architecture Rule 5. Its production
checks remain active. The full import, dependency, trait-naming and static
conformance gates still report existing repository violations. No baseline was
expanded to hide them. Full-workspace conformance test discovery was interrupted
rather than completed; its output must not be treated as proof of missing tests.
The public-API checker was skipped because `cargo-public-api` was unavailable;
the baseline-growth checker could not resolve its configured default base ref.

The final repeated Caller regression after the Service API package-version bump
was cancelled while waiting for an artifact lock held by a separate Cargo build.
It is not counted as a passing final-tree rerun. Whole-workspace coverage,
Singlebox E2E, real MySQL/Redis load and deployed SDK/private-provider validation
were not run. These results do not establish deployment readiness by themselves.
