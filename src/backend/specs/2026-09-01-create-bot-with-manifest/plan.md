# Plan: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` in this directory. Work item W13, issue #1696.

> **Revision 5.** All open questions closed: the terminal states name *what*
> failed, the submit response carries no state, and the endpoint is **ARCA-only**.
> Rev 4 moved applying onto the task queue for all three cases. Revision history
> at the end.

## What already exists, and what that leaves

Two components were built before this item and are meant to be *used*:

**W4's apply engine** —

| Already there | Where | What it gives W13 |
| --- | --- | --- |
| `validate(document, active_engine, bot_type)` | `BotConfigManifestServiceProtocol` | Validation with no bot record — its docstring already names this path |
| `resolve_capabilities(active_engine, bot_type)` | `capabilities.py` | The second entry point, written for W13's preflight |
| `start_apply(..., phases=...)` | `config_manifest_apply_service.py` | One phase at a time |
| `ApplyPhase.PRE_CONTAINER` / `ON_CONTAINER` | `apply/order.py` | `script` alone vs. everything else |
| `build_materialisers()` | `apply/registry.py` | The live set of constructs something can act on |
| `declared_entries(parsed, construct)` | `apply/orchestrator.py` | "Did the document declare this category?" |
| `trigger` column, `String(32)` | `apply_models.py` | Its comment already reserves `create`; no migration |
| Lock with `lock_token` + stale reaping | `config_manifest_apply` repos | Serialisation that survives a handler moving off-thread |

**The task queue** (`core/task_queue`) — whose README names the pattern this item
removes: it replaces "the 'spawn a daemon thread and `sleep`' pattern, which loses
work on restart and double-runs across pods."

| Already there | What it gives W13 |
| --- | --- |
| `TaskQueueService.enqueue(type, payload, deadline_seconds, idempotency_key=…)` | Both enqueues |
| `Reschedule` / `Complete` / `Fail` outcomes | The creation job's step machine |
| `TaskStatus.TIMED_OUT`, enforced DB-side | "The user never clicked", distinct from failure |
| Claim CAS + lease reclaim | An apply survives the pod that started it |
| `wake_on_enqueue` + `WorkerWakeup` | No idle-interval latency; its docstring is written around handlers enqueuing follow-up work |

## Architecture

```text
POST /openapi/v1/bots/with-manifest
  │
  ├─ _prepare_create (existing policy)
  ├─ preflight: quota / name / engine            ← existing
  ├─ preflight: ARCA-only + manifest             ← NEW  engine gate, validate,
  │                                                     materialiser gate
  ├─ persist manifest  key=(entity_id, bot_id)   ← NEW  no schema change
  ├─ Passport apply → authorization handles      ← existing (never creates inline)
  └─ enqueue creation job(attributes + tenant, deadline)
        └─► 202 {bot_id, iframe_url, redirect_url}   (no state — poll owns that)

TaskWorker ──► CreationJobHandler                 (reschedules itself)
  │  with avernet_tenant_scope(payload["tenant"]):
  ├─ 1. Passport not ISSUED?   PENDING → Reschedule ; declined → discard, Fail
  ├─ 2. phase A not done?      start_apply(PRE_CONTAINER) → Reschedule until terminal
  ├─ 3. no bot record?         complete_bot_authorization(...)   ← unmodified
  ├─ 4. container not up?      Reschedule
  └─ 5. start_apply(ON_CONTAINER, carry_from=<phase A>) → Complete
        deadline elapses at any point → TIMED_OUT (+ discard)

TaskWorker ──► ApplyTaskHandler                   ← one type, all three cases
  │  with avernet_tenant_scope(payload["tenant"]):
  └─ rebuild context → orchestrator.apply(...) → finish record → release lock

GET /openapi/v1/bots/{bot_id}/with-manifest/status
  └─ pure read of: the job record, the bot record, the apply records
```

## Key decisions

### K-1 The materialiser gate is derived, not listed

The set comes from the registry itself — the apply service grows
`materialised_constructs() -> frozenset[ApplyConstruct]` returning
`frozenset(build_materialisers(...).keys())`, the same registry `_orchestrator()`
builds. The preflight refuses any construct the document **declares** that is
absent from it, where "declares" is `declared_entries(parsed, construct) is not
None` walked over `APPLY_ORDER` (so a declared-empty category, which *removes*,
counts as declared).

### K-2 No seam in `create_bot` — phase A runs before it

**Phase A needs nothing from the bot record**, checked rather than assumed:

- `BotStartupScriptService.put` is keyed by `(entity_id, bot_id)` and reads no bot
  record — it validates size and encoding and upserts.
- Both key parts are known at submission.
- The placeholder whitelist is exactly `BOT_ENGINE_TYPE`, `BOT_ENV`,
  `BOT_TENANT`, `BOT_ARCH`; `placeholders.py` states there is **deliberately no
  `BOT_ID`**, and there is no `BOT_NAME` either — so nothing waits on
  `_resolve_bot_name`'s defaulting inside `create_bot`.

So the job runs phase A **before** it calls creation. "The row exists before the
start command is composed" becomes true *by construction*, with no hook that could
be placed in the wrong function. `bot_service.py` is untouched by this item.

Phase A needs an `ApplyContext` without a record, which is the
`(engine_type, bot_type)` capability entry point W1 built for W13.

The one cost: phase A can write a startup-script row for a bot that never gets
created — the same orphan class as the stored manifest, cleaned on the same
terminal paths (K-8).

### K-3 Applying becomes a task — what moves, and what does not

`start_apply`'s body splits at the thread, and **the split is chosen so its public
contract does not change**:

| Stays in `start_apply`, synchronous on the caller's thread | Moves into the task handler |
| --- | --- |
| Acquire the lock (so a concurrent apply still raises `ManifestApplyInProgressError`) | Rebuild the context |
| Re-validate the stored document (so a validation failure still raises to the caller) | Run the orchestrator |
| Mint `apply_id`, write the `RUNNING` record | Write the terminal record |
| **Enqueue the task** (was: start the thread) | Release the lock, by token |
| Return `ApplyAccepted(apply_id, RUNNING)` | |

So `POST …/config-manifest/apply` still answers `202` with an id, still refuses a
concurrent apply, still surfaces validation synchronously. Only the executor
changes.

**The lock is held across the handoff**, acquired by the enqueuer and released by
the handler using the token in the payload. That is what the existing token-based
release and the stale-lock reaping already support; a task that never runs (worker
disabled) leaves a lock that the TTL reaps, exactly as a dead thread does today.

**The payload carries identifiers, not state.** Specifically it does **not** carry
the parsed document: `MAX_DOCUMENT_BYTES` is 64 KB and `ac_task_queue.payload` is
`Text`, which on MySQL is also 64 KB — a large manifest plus the rest of the
payload would not fit, and a truncated payload is a silent corruption. The handler
therefore re-reads and re-validates through `_parsed_or_empty`.

The consequence, stated because it is a real behaviour change: today `parsed` is
snapshotted before the thread starts, so a `PUT` landing in between is not picked
up; after this change the handler reads the document as of execution. The window
is short (`wake_on_enqueue` means milliseconds, not an idle interval), a
concurrent *apply* is impossible because the lock is held, and re-reading is the
level-triggered behaviour W4 already chose for its own re-validation. Accepted,
and noted where the handler reads.

**The context is rebuilt, not serialised.** A bot dict in the payload could be
stale by the time the task runs, and it is not small. The handler re-reads the bot
record by `(entity_id, bot_id)`; for phase A there is no record, so `engine_type`
and `bot_type` come from the payload and capabilities resolve from those.

### K-4 One task type

The three cases — phase A, phase B, an explicit apply on a running bot — differ
only in `phases`, the `trigger` label, and whether a previous report is carried.
The orchestrator branches on none of them: `trigger` appears in it exactly twice,
as a parameter and as a field it copies onto the report, and there is no branch on
phase names or on first-boot anywhere (W4 pinned that as §2.7). Three task types
would be three registry keys for one behaviour, and `trigger` already carries the
distinction for anyone querying the apply table. `wake_on_enqueue` is per-type,
but all three want immediacy.

### K-5 The creation job waits for phase A only

Phase A has a downstream dependency: creation must not begin until the script row
exists. Phase B has none — nothing in the platform is blocked on it, which is
exactly true of an apply against a running bot too. So the job starts phase B and
finishes; phase B is then observed the way any apply is, by reading its record.

Nothing is lost by not waiting: the poll derives `APPLYING` / `READY` / `FAILED`
from the apply record and its trigger, not from the job; `carry_from_apply_id` is
passed when phase B *starts*; the deadline still covers everything that precedes
phase B; and a phase B whose worker dies is now completed by another worker rather
than needing a bound at all.

No `apply_now`, and no synchronous variant of anything: the job waits by
rescheduling until phase A's record is terminal. Rejected alternatives, recorded
so they are not re-derived — polling the record inside the request (a busy-wait in
the creation path); a `wait=True` flag on `start_apply` (the return type is what
differs, so a boolean makes it conditional at every call site); writing the
startup-script row outside the apply engine (no record, no report, no lock, and
category knowledge leaks out of the registry).

### K-6 Re-entrancy, and why "no retry" is not the reason

The queue guarantees a single claimer but **at-least-once invocation**: "a crashed
worker's task is reclaimed after its lease expires", whether or not a handler ever
returns `Retry`. So both handlers are written to be re-entrant, and safety is
argued from what they do:

- **The apply task** is safe because apply *converges* — re-applying an unchanged
  document performs no writes (W4's criterion), and the lock serialises attempts.
  A partially-written category is re-planned against current state on the re-run.
- **The creation job** re-derives its step from durable state every invocation
  rather than tracking a cursor. `create_bot` is already idempotent on a supplied
  `bot_id` (it returns the existing bot), and `start_apply` takes the lock.

Reschedule delay: 5 s, matching the existing publish poller's cadence.

### K-7 Two applies, two triggers, one carried report

Phase A and phase B are separate `start_apply` calls separated by the whole of
container provisioning, with distinct triggers that fit the existing `String(32)`
column: `create:pre_container` and `create:on_container`.

`start_apply` grows `carry_from_apply_id: str | None`: the named record's
categories are prepended to the report phase B finishes with and the summary
re-derived over the union — so a failed phase A plus a clean phase B terminates
`PARTIAL`, which the poll reports as `FAILED`. Without it the report a caller reads
at `READY` would name the MCP entries and silently omit `script`.

The triggers are also how the job and the poll tell "phase A is done" from "the
whole creation is done", via `last_apply` — so no repository method is added.

### K-8 State derivation — a pure read

| Observed | Reported |
| --- | --- |
| Job live, no bot record | `AWAITING_AUTHORIZATION` + handles |
| Job `FAILED` (declined) | `AUTHORIZATION_REJECTED` |
| Job `TIMED_OUT` before a bot exists | `AUTHORIZATION_EXPIRED` |
| Bot record exists; no `create:on_container` apply yet | `CREATING` |
| Bot record exists but provisioning failed, or the deadline elapsed with no container | `CREATE_FAILED` |
| `create:on_container` apply `RUNNING` | `APPLYING` |
| …terminal `SUCCEEDED` | `READY` + report + bot |
| …terminal `PARTIAL` / `FAILED` | `APPLY_FAILED` + report + bot |

The two failure states are kept apart deliberately: `CREATE_FAILED` means there is
no usable bot and the manifest is beside the point, `APPLY_FAILED` means the bot is
up and part of its configuration is missing. A caller must not have to read a
message to tell those apart, and an invalid manifest is neither — it is a `422` at
submission with no bot and no state.

**No row in that table requires an external call.** The first one in particular is
read off "the job is live and no bot exists", *not* by querying AgentPass — the
job is what polls Passport, and duplicating that in the poll would make a read
path do business work.

The authorization handles come from the job's payload (written at submission),
read via the task record. If that proves awkward, the alternative is not to return
them at all — the create response already did — rather than to re-query Passport.

`CREATE_FAILED` is what the earlier revisions had no name for. It covers both
shapes of "no usable bot": creation itself raised, or the bot record exists and no
container ever came up, in which case the job reaches its deadline with no
`create:on_container` apply to point at.

### K-9a ARCA only

The preflight refuses a teclaw engine, using the same mechanism as the unbacked-
construct refusal. The reason is structural rather than a missing materialiser:
this item's pre/post-container split exists because
`BaasService._build_create_bot_payload` reads the startup-script row while
composing a start command, and teclaw has no analogue — `TeclawProvisionService`
composes a config artifact at provision time and hands it to BaaS. Delivering a
teclaw manifest post-container would be a different mechanism from the one W8
lands, so a bot created here would get semantics that change under it. W8 owns
that arm: its scope names "teclaw 在第一份 artifact 组装之前", its first acceptance
criterion is the first-artifact guarantee, and its scale note lists
`TeclawProvisionService`.

`is_teclaw` is the engine authority — the same callable the capability resolver
already takes — never a hand-rolled `== "teclaw"`.

### K-9b Submission never creates the bot inline

`create_bot_with_authorization` creates the bot inline when Passport returns a
token immediately. This endpoint always goes through user consent, so W13's
submission composes the pieces — policy, preflight, persist, the Passport
application — and stops, rather than calling that function whole. If a token ever
does come back immediately, the job's first run sees `ISSUED` and proceeds
normally, so there is no special case and the phase-A-before-creation ordering
holds on every path.

### K-10 Cleanup on a bot-less terminal

When the job ends declined or timed out, it deletes the stored manifest and any
startup-script row phase A wrote, before returning. Both deletes are idempotent.
This is what retires the feature switch.

## Files

### New

| File | What |
| --- | --- |
| `core/bot_config_manifest/creation.py` | The creation seam: preflight, persist, phase A, discard. |
| `core/bot_config_manifest/apply/apply_task.py` | The apply task handler and its payload shape. |
| `core/bot_config_manifest/create_job.py` | The creation job handler. |
| `adapters/http/openapi_v1/bots/create_with_manifest.py` | The two routes and the state mapping. |
| `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` | Models and the `CreationState` enum. |

### Changed

| File | Change |
| --- | --- |
| `core/bot_config_manifest/services/config_manifest_apply_service.py` | Enqueue instead of spawning a thread; `carry_from_apply_id`; `materialised_constructs`; a record-free apply context. |
| `core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py` | The new members. |
| `core/bot_management/create_flow.py` | An optional creation-manifest seam at submission. **`bot_service.py` and `complete_bot_authorization` are not touched.** |
| `di/modules/bot_management_module.py` | Bind the creation service; register both handlers in the task-queue registry. |
| `adapters/http/openapi_v1/__init__.py` | Mount the new router. |
| The config module | The creation deadline, default 600 s. |
| `core/bot_config_manifest/README.md`, `docs/bot-config-manifest/user-manual.zh-CN.md` | The creation flow, the poll states, the `script` rule, and the worker precondition. |

## Risks

1. **The worker becomes load-bearing.** Applying — and therefore bot creation —
   only progresses where `task_queue_worker.enabled=true` and `ac_task_queue` is
   provisioned. Today that flag gates an optimisation; after this it gates the
   feature. It must be documented as a deployment requirement, and it is the first
   thing to check when a creation appears stuck.
2. **First adopter of enqueue idempotency.** The README says the key mechanism is
   "not yet adopted by any call site" and that adoption must ship in a *strictly
   later release* than the mechanism. That ordering is satisfied, but it has to be
   confirmed against the deployed release rather than assumed.
3. **The lock now spans a process boundary.** Acquired by the enqueuer, released by
   the handler. A task that never runs holds it until the TTL reaps it — the same
   outcome as a thread that dies today, but worth a test.
4. **Re-read instead of snapshot** (K-3) is a small behaviour change on the
   running-bot path. It is level-triggered by design, but it is the one place where
   "nothing else moves" is a claim about contracts rather than about internals.
5. **A re-run of a handler double-acts.** Mitigated by convergence and the lock
   (K-6), and tested by invoking each handler twice at every step.

## Testing strategy

- **Unit** — the preflight gate (each construct, plus a stub materialiser widening
  it); the state-derivation table case by case; the report merge.
- **Apply task** — the three cases run through one handler; the lock is released on
  every path including a raising orchestrator; a second invocation converges and
  writes nothing; the existing apply route's `202` + `apply_id` + concurrent-apply
  refusal are unchanged.
- **Creation job** — each step's outcome; a second invocation at every step is a
  no-op; the deadline path deletes both rows.
- **Ordering** — phase A completes before creation is called, on recorded call
  order.
- **Tenancy** — the tenant observed inside each handler equals the submitting
  request's, written so that dropping the scope *fails* rather than passing by
  coincidence (the getter returns the default, it does not raise).
- **Endpoint** — submit → `202` → poll `AWAITING_AUTHORIZATION` → authorize →
  `CREATING` → `APPLYING` → `READY`, the report carrying both phases; an invalid
  manifest `422` with Passport never called; an unbacked construct refused at
  submission; a `PARTIAL` apply reported `FAILED` **with the bot present**.
- **Regression** — every existing create, auth-status, manifest, apply and
  startup-script test passes **unedited**.

## Revision history

| | What changed, and why |
| --- | --- |
| **rev 1** | Rode the existing two-leg Passport pipeline; a device-activation listener ran phase B; the poll echoed creation attributes; a default-off switch. |
| **rev 2** | Onto a task-queue job with a deadline. The listener and its restart guard go; abandonment becomes terminal; the poll takes only a `bot_id`; the switch is replaced by the job cleaning up after itself. |
| **rev 3** | Phase A moves ahead of bot creation, deleting both the `pre_provision` seam in `create_bot` and the synchronous `apply_now`. |
| **rev 4** | Applying becomes a task on all three paths (D-9), one task type (D-10); the job stops waiting for phase B (K-5); submission never creates inline (K-9b); the poll is a pure read (K-8). |
| **rev 5** | Terminal states split into `CREATE_FAILED` and `APPLY_FAILED` so the three failure modes are distinguishable (D-6); the submit response carries no state; the endpoint refuses teclaw (D-8, K-9a). Deployment preconditions confirmed by the owner. |
