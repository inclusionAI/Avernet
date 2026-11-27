# MultiRollout@1.0 durable controller

Owner: Backend L5 `context_collab`. This is a **local extension**, not an assigned
ACCS RFC or a claim of compliance with an inaccessible external document. The
four existing Method profiles remain pinned to `@0.2`; `get_profile` still defaults
to `0.2`, so request `get_profile("MultiRollout", "1.0")` explicitly.

## Contracts and authority

- [Policies](multi_rollout_policy.py) are closed typed values. All five hard
  budgets are mandatory; booleans are not integer budgets. References resolve to
  installed exact capabilities, never import paths or arbitrary URLs.
- [Controller facts and outbound ports](multi_rollout_contracts.py) define the
  side-effect-free strategy proposal boundary, isolation/notification capabilities
  and candidate-scoped MethodRunPort. [DurableSwarmMethodRun](multi_rollout_execution.py)
  uses the real engine, provider routing and trusted acceptance ingress.
- L6 selects methods with `MethodCompositionTemplate`; L5 resolves **active
  Bindings and the unique NEST edge**, not L6 templates or declaration order.
  Only Swarm has an executable body adapter. Single/Group matrix entries are
  structural seams, not installed execution support. Open and recursive
  MultiRollout bodies are rejected. The old MethodStore bind path rejects this
  extension; it cannot become a second runtime authority.
- Each candidate is an actual ColletRollout with independently allocated stage
  identities, a derived Swarm Binding/edge, plan, execution namespace and sessions.
  `current_rollout_id` is a stable display/interaction **focus**, not an execution
  allowlist. Parallel completion order never moves it; adoption moves it to the
  sole winner. Adaptive successors move focus to the new candidate.
- The [SQL snapshot](../l6_products/product_profile_service/snapshot_codec.py), CAS,
  receipts, outbox and admission rows commit atomically. Schema is
  `product-runtime/multi-rollout@1`; an explicit no-discriminator legacy decoder
  preserves serial profiles. No automatic migration of running legacy Collets.

## Strategies and guarded plans

`ParallelBestOf(samples, max_concurrency, selection_ref)` creates one fixed batch
of 1–32 independent runs, at most 8 concurrently. It waits for all actual terminal
candidate outcomes; one failed candidate does not fail its siblings. There is no
first-success shortcut or hidden replacement sample. Only independently verified
PASS candidates qualify. [HighestScoreStrategy](multi_rollout_capabilities.py)
requires a host **trusted evaluator** with a frozen rubric/version/range. Selection
validates complete, unique, finite scores and breaks ties by candidate ordinal,
not completion order. Worker self-reported scores are not trusted inputs.

`AdaptiveUntilAccepted(feedback_mode, adaptation_ref, ...)` runs one candidate at
a time. Only ACCEPTANCE_FAILED triggers quality adaptation; runtime failure,
unknown delivery, unavailable authority or validator is not quality feedback.
Interactive mode stores WAITING_FEEDBACK, one notification key and an absolute
bounded deadline. Autonomous mode requires an explicit host delegation grant,
rechecked at the actual strategy call. Autonomous no-op may request bounded human
feedback; otherwise it terminates NO_PROGRESS rather than spinning.

Human and autonomous proposals pass the **same controller validator**:

- RE_DO creates fresh candidate IDs under the unchanged plan.
- OPTIMIZE creates a new immutable RunPlan, limited to existing stage instructions,
  original-candidate narrowing/selection and a recomputed rerun successor closure.
  Instructions/selection are projected into actual per-stage provider input.
- SHUTDOWN revokes scheduling and writes real cancel/reconcile work. It cannot
  invent cancelled provider facts, stage failures or root Work acceptance.
- DO_NOTHING does not reset a human deadline. Repeated no-op, unchanged patches or
  repeated failures are bounded by persistent progress/rollout/strategy budgets.

The initial implementation **fully reruns** every successor. A requested partial
rerun is recorded, but is not evidence that unaffected artifacts are immutable,
equivalent or still authorized. It never reuses sessions or unverifiable evidence.
Profiles, stage topology, acceptance, permissions and global budgets cannot be
changed by a proposal. PASS with `overall_acceptance="owner"` projects only to
Collet RUN_FINISHED; owner/root Work acceptance remains independent.

## Isolation, quotas and host installation

The public DI module installs unavailable strategy/isolation/notification ports.
Publication and Collet creation fail closed unless the host explicitly supplies:

1. Strategy/evaluator exact versions, frozen rubric and current authority; an
   adaptation Agent for adaptive mode and explicit autonomous delegation.
2. Isolation attesting independent mutable sessions, workspaces, external keys,
   artifact namespaces and **safe external side effects**. Workspace separation
   alone does not authorize email, deployment, payment, merge or other irreversible
   effects. Preparation is idempotent by namespace; route/artifact access is checked
   through this capability on real execution/report paths.
3. A consistent, current tenant-wide quota (1–8). The built-in SQL admission rows
   arbitrate **across Collets and workers**. Candidate slots are reserved before
   dispatch, held across stages, and released only after real terminal/settled
   execution. Unknown delivery and unconfirmed cancellation keep their slots.
4. An idempotent notification sender, deduplicating its stable key even after a
   crash following delivery. No raw feedback or credentials are notification keys.

All provider/Agent calls are outside SQL transactions. Strategy operations have a
persisted key, payload digest, generation, lease and unique fence. A duplicate or
competing in-flight adjustment returns a retryable conflict without calling another
Agent. Trusted shutdown can fence an in-flight selection/adaptation. Every actual
recomputation reserves another strategy call; committed receipts/decisions replay
without a new call. External proposal awaits are bounded below the claim lease.

## Scheduling, progress and recovery

The host must explicitly schedule `RolloutIntentWorker.run_once(limit)` **and**
`check_deadlines(limit)`. There is no background thread or perpetual retry loop.
Candidate external IO is concurrent; short same-Collet SQL writes remain CAS
serialized. Multi intents are fenced by run/generation/plan/allocation, **not**
superseded merely because a sibling advanced the aggregate revision. Outbox claims
have durable owner/fence/lease. Lease recovery cannot bypass unknown allocations.

Signed progress still uses `ReportContext(request_id, attempt, expected_revision)`
with **aggregate** CAS revision. Two valid sibling reports can conflict normally:

1. Unknown result: retry the identical original request and revision for its receipt.
2. Confirmed stale/uncommitted: refresh revision and submit the same business fact
   with a new technical request ID, retaining its actual allocation/attempt fence.
3. Do not resubmit terminal, revoked, expired or old-generation allocations.

Queries expose [bounded control metadata](multi_rollout_projection.py): phase,
outcome, focus/winner, counts, plans, namespaces, reservations, peak concurrency,
strategy-call disposition/elapsed time, waiting deadline and cancellation state.
They do not expose raw feedback or become another write authority.

| Symptom | Distinguishing facts and next action |
| --- | --- |
| Candidate queued | Check candidate slot reservation, configured concurrency and tenant SQL slots; do not add a process-only semaphore as quota proof. |
| Unknown start/no route | Persisted attempt is authoritative. Without a host query-by-key/idempotency reconciliation capability, do not resend; inspect/settle the external run explicitly. Deadline can revoke scheduling but cannot pretend the external run stopped. |
| Cancel requested/unknown | Continue explicit cancellation reconciliation. A late returned route is retained as cancellation work. Release a slot only after positive cancellation acknowledgement or an actual terminal provider observation; neither manufactures a cancelled business event. |
| Strategy call interrupted | Expired claimed work can recompute side-effect-free proposals under a new fence and an additional charged call. A known failed/unknown outbox item needs explicit host recovery; inspect the persisted operation and limits before retrying controller select/adapt or the original interactive UUID. |
| Feedback wait survives restart | Use the original `waiting_until`; neither a no-op nor worker reconstruction extends it. |
| Snapshot limit | 12 MiB is a hard streamed-encoding bound. Do not truncate acceptance, decisions or receipts to continue. Create a new governed run or archive outside this writer. |

MySQL DDL, host/provider cancellation semantics and deployment require separate
integration verification; local SQLite/recording-provider tests are not evidence of
those external integrations. See [delivery evidence](../../../../../../../specs/2026-11-26-context-collab-multi-rollout-method/delivery.md).
