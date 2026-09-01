# Plan: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` in this directory. Work item W13, issue #1696.

> **Revision 2** — reworked after review on PR #1791. The creation is carried by a
> task-queue job instead of by a device-activation listener; the poll takes only a
> `bot_id`; the feature switch is gone. `spec.md`'s D-3, D-4 and D-7 record why.

## What already exists, and what that leaves

Two components were built before this item and are meant to be *used*, not
re-created:

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

**The task queue** (`core/task_queue`) — whose README names this exact shape as
its motivating use case: "polling an external operation until it reaches a
terminal state: a handler that reschedules itself until done, bounded by a
wall-clock deadline."

| Already there | What it gives W13 |
| --- | --- |
| `TaskQueueService.enqueue(type, payload, deadline_seconds, idempotency_key=…)` | Submission's one call |
| `Reschedule` / `Complete` / `Fail` outcomes | Wait for Passport, wait for the container, finish |
| `TaskStatus.TIMED_OUT`, enforced DB-side | "The user never clicked", as a status distinct from failure |
| Claim CAS + lease reclaim | Survives a pod restart; exactly one worker runs it |
| `wake_on_enqueue` | The job starts at submission instead of waiting out an idle poll |

So the work is **sequencing, a job handler, and a public surface**. Four things do
not exist: a preflight that also demands a materialiser, a seam in `create_bot`
between the row and the container, the job handler, and the endpoint pair.

## Architecture

```text
POST /openapi/v1/bots/with-manifest
  │
  ├─ _prepare_create (existing policy)
  ├─ preflight: quota / name / engine            ← existing
  ├─ preflight: manifest                         ← NEW  validate + materialiser gate
  ├─ persist manifest  key=(entity_id, bot_id)   ← NEW  no schema change
  ├─ Passport apply → authorization handles      ← existing
  └─ enqueue job(attributes + tenant, deadline)  ← NEW
        └─► 202 {bot_id, AWAITING_AUTHORIZATION, iframe_url, redirect_url}

TaskWorker ──► BotCreateWithManifestHandler       ← NEW   (reschedules itself)
  │
  ├─ with avernet_tenant_scope(payload["tenant"]):
  ├─ 1. Passport not ISSUED?  PENDING → Reschedule ; declined → Fail(+cleanup)
  ├─ 2. no bot record?        create_bot(..., pre_provision=phase_a)
  │                             ├─ row insert + template        (existing)
  │                             ├─ pre_provision(bot) ← NEW SEAM  phase A, sync
  │                             └─ device provisioning          (existing)
  ├─ 3. container not up?     Reschedule
  ├─ 4. phase B not started?  start_apply(ON_CONTAINER, carry_from=<phase A>)
  ├─ 5. phase B running?      Reschedule
  └─ 6. terminal              Complete
        deadline elapses at any point → TIMED_OUT (+cleanup)

GET /openapi/v1/bots/{bot_id}/with-manifest/status
  └─ derives the state from: the job record, the bot record, the apply records
```

Every state is **read**, never separately stored: authorization from Passport,
progress from the job row and the apply rows. **No new table, no new column.**

## Key decisions

### K-1 The materialiser gate is derived, not listed

`spec.md` requires that W5/W6 widen this endpoint by landing, so the set comes
from the registry itself. The apply service grows

```python
def materialised_constructs(self) -> frozenset[ApplyConstruct]:
    return frozenset(build_materialisers(...).keys())
```

on its protocol — the same registry `_orchestrator()` builds, so the two cannot
disagree. The preflight refuses any construct the document **declares** that is
absent from it, where "declares" is `declared_entries(parsed, construct) is not
None` walked over `APPLY_ORDER` (so a declared-empty category, which *removes*,
counts as declared).

### K-2 One seam in `create_bot`, and it is generic

`BaasService._build_create_bot_payload` reads `ac_bot_startup_script` while
composing the start command, inside device provisioning, which `create_bot` calls
after inserting the row. Phase A's window is therefore *inside* `create_bot`:

```python
pre_provision: Callable[[dict], None] | None = None
```

invoked once, after the row (and any template) exists and before any provisioning
branch, taking the bot record.

- **Generic, not manifest-shaped.** A manifest dependency on `BotService` would
  put a second copy of "does this bot have a manifest" beside the one the apply
  service owns.
- **It must not raise.** `create_bot` wraps and logs — spec D-5 enforced
  mechanically.
- `BotServiceProtocol.create_bot` is `(*args, **kwargs)`, so no protocol change.

### K-3 The job handler is a state machine over observed facts

The handler re-derives where it is on every invocation instead of tracking a
cursor, because the queue guarantees single-claim but **at-least-once** invocation:
a crashed worker's task is re-claimed and the handler runs again. Each step's
question is "is this already done?", answered from durable state — the bot record,
the device status, the apply records — so a re-run resumes rather than repeats.

That is also why creation itself is safe: `create_bot` is already idempotent on a
supplied `bot_id` (it returns the existing bot), and `start_apply` takes the apply
lock, so a duplicated invocation cannot double-create or double-apply.

Reschedule delay: 5 s, matching the existing publish poller's cadence.

### K-4 Two applies, two triggers, one carried report

Phase A and phase B are separate `start_apply` calls separated by the whole of
container provisioning, with distinct triggers that fit the existing `String(32)`
column:

- `create:pre_container`
- `create:on_container`

The triggers are also how the handler and the poll tell "phase A is done" from
"the whole creation is done".

To keep the terminal report complete, `start_apply` grows
`carry_from_apply_id: str | None`: the named record's categories are prepended to
the report phase B finishes with, and the summary re-derived over the union — so a
failed phase A plus a clean phase B terminates `PARTIAL`, which the poll reports as
`FAILED`.

### K-5 Phase A is synchronous; `start_apply` is not

`start_apply` returns as soon as its thread starts, which is wrong for phase A —
provisioning must not begin until the script row exists. The service grows a
sibling, `apply_now(...)`, with the same body minus the thread: lock, validate,
record `RUNNING`, run inline, finish, release. Phase A is a database write with no
fetch and no device, so it is bounded work.

### K-6 The tenant rides in the payload

The queue has no tenant column and no request context at handler time. The payload
carries the submitting request's tenant and the handler opens
`avernet_tenant_scope(tenant)` around its whole body.

This gets a test rather than a comment because **the failure is silent**:
`get_current_avernet_tenant()` is total — outside a request it returns the
*default* tenant instead of raising — so a handler that forgets the payload value
substitutes the wrong `${BOT_TENANT}` and reads and writes the manifest tables
under the wrong tenant, with nothing raised anywhere.

### K-7 State derivation

| Observed | Reported |
| --- | --- |
| Job live, no bot record, Passport `PENDING`/not ready | `AWAITING_AUTHORIZATION` + handles |
| Job `FAILED` on a declined authorization | `AUTHORIZATION_REJECTED` |
| Job `TIMED_OUT`, or Passport expired | `AUTHORIZATION_EXPIRED` |
| Bot record exists; no `create:on_container` apply yet | `CREATING` |
| `create:on_container` apply `RUNNING` | `APPLYING` |
| …terminal `SUCCEEDED` | `READY` + report + bot |
| …terminal `PARTIAL` / `FAILED` | `FAILED` + report + bot |

Provisioning that fails outright is the one edge the six states do not name: the
bot exists but no container will ever come up, so the job hits its deadline and the
poll reports `FAILED` with a message naming provisioning, not the manifest.

### K-8 Cleanup on a bot-less terminal

When the job ends declined or timed out, it deletes the stored manifest before
returning `Fail` (the delete is idempotent — W1 made absence success). This is what
retires the feature switch: the rows this endpoint creates are bounded by their own
jobs.

## Files

### New

| File | What |
| --- | --- |
| `core/bot_config_manifest/creation.py` | The creation seam: preflight (validate + materialiser gate), persist, phase A, cleanup. |
| `core/bot_config_manifest/create_job.py` | `BotCreateWithManifestHandler` — the task-queue handler and its payload shape. |
| `adapters/http/openapi_v1/bots/create_with_manifest.py` | The two routes and the state mapping. |
| `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` | Request/response models and the `CreationState` enum. |

### Changed

| File | Change |
| --- | --- |
| `core/bot_management/services/bot_service.py` | `create_bot(..., pre_provision=None)`; invoked after row/template, before provisioning, non-raising. |
| `core/bot_management/create_flow.py` | An optional creation-manifest seam, called at preflight and persist. |
| `core/bot_config_manifest/services/config_manifest_apply_service.py` | `apply_now`, `carry_from_apply_id`, `materialised_constructs`. |
| `core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py` | The three new members. |
| `di/modules/bot_management_module.py` | Bind the creation service; register the handler in the task-queue registry. |
| `adapters/http/openapi_v1/__init__.py` | Mount the new router. |
| `di/config.py` (or the module that owns it) | The configurable creation deadline, default 600 s. |
| `core/bot_config_manifest/README.md`, `docs/bot-config-manifest/user-manual.zh-CN.md` | The creation flow, the poll states, the `script`-dependency rule. |

## Risks

1. **The seam lands in the wrong place in `create_bot`.** The whole item is worth
   nothing if the script row is written after the payload is composed. Pinned by a
   test asserting call order.
2. **A re-run of the handler double-creates or double-applies.** Every step is
   written as "is this already done?" and the two underlying operations are
   already idempotent (`create_bot` on a supplied id, `start_apply` under its
   lock). Tested by invoking the handler twice at each step.
3. **Silent tenant loss on the worker.** K-6; tested, not commented.
4. **The registry is the first production adopter of the handler registry.** Its
   docs say the registry is empty until an adopter registers handlers, so the
   wiring (bootstrap-time registration, `app_name` config) needs checking against a
   running app, not just unit tests.
5. **`create_bot`'s many other callers.** The new parameter is keyword-only with a
   `None` default; the existing suites run unedited as the check.

## Testing strategy

- **Unit** — the preflight gate (each construct, and a stub materialiser widening
  it); the state-derivation table case by case; the report merge.
- **Handler** — each step's reschedule/complete/fail outcome; a second invocation
  at every step is a no-op; the deadline path deletes the manifest.
- **Ordering** — phase A completes before provisioning is entered, on call order.
- **Tenancy** — the tenant observed inside phase A and inside the handler equals
  the submitting request's, and a payload missing it is a test failure rather than
  a silent default.
- **Endpoint** — submit → `202` → poll `AWAITING_AUTHORIZATION` → authorize →
  `CREATING` → `APPLYING` → `READY`; an invalid manifest `422` with Passport never
  called; an unbacked construct refused at submission; a `PARTIAL` apply reported
  `FAILED` **with the bot present** in the response.
- **Regression** — every existing create, auth-status, manifest, apply and
  startup-script test passes **unedited**.
