# Plan: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` in this directory. Work item W13, issue #1696.

> **Revision 3** — reworked after two review rounds on PR #1791. Rev 2 moved the
> creation onto a task-queue job (`spec.md` D-3/D-4/D-7). Rev 3 answers "do we
> really need `apply_now`?" and "why a seam inside `create_bot`?" with: we need
> neither. Phase A runs **before** `create_bot`, and the job's own reschedule
> idiom waits for it. See K-2 and K-5.

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

So the work is **sequencing, a job handler, and a public surface**. Three things do
not exist: a preflight that also demands a materialiser, the job handler, and the
endpoint pair. Note what is *not* on that list any more — nothing inside
`create_bot`, and no new apply entry point (K-2, K-5).

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
  ├─ 2. phase A not done?     start_apply(PRE_CONTAINER) → Reschedule until terminal
  ├─ 3. no bot record?        complete_bot_authorization(...)  ← unmodified
  │                             └─ create_bot → provisioning   (existing, untouched)
  ├─ 4. container not up?     Reschedule
  ├─ 5. phase B not started?  start_apply(ON_CONTAINER, carry_from=<phase A>)
  ├─ 6. phase B running?      Reschedule
  └─ 7. terminal              Complete
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

### K-2 No seam in `create_bot` — phase A runs before it

The first two revisions put a `pre_provision` callback inside `create_bot`,
between the row insert and provisioning, because that looked like the only window
before `BaasService._build_create_bot_payload` composes the start command.

It is not. **Phase A needs nothing from the bot record**, checked rather than
assumed:

- `BotStartupScriptService.put` is keyed by `(entity_id, bot_id)` and reads no bot
  record — it validates size and encoding and upserts.
- Both key parts are known at submission: `bot_id` is allocated by
  `generate_bot_id`, `entity_id` by the same rule `create_bot` will apply.
- The placeholder whitelist is exactly four names — `BOT_ENGINE_TYPE`, `BOT_ENV`,
  `BOT_TENANT`, `BOT_ARCH` — and `placeholders.py` says there is **deliberately no
  `BOT_ID`**; there is no `BOT_NAME` either. Every one resolves from the creation
  request, so nothing waits on `_resolve_bot_name`'s defaulting inside
  `create_bot`.

So the job runs phase A **before** it calls creation at all. "The row exists
before the start command is composed" becomes true *by construction* — there is no
hook that could be placed in the wrong function, and no ordering test that could
pass today and rot later. `bot_service.py` is untouched by this item.

The one cost: phase A can write a startup-script row for a bot that never gets
created. That is the same orphan class as the stored manifest and is cleaned on
the same terminal paths (K-8).

Phase A needs an `ApplyContext` without a record, which is the `(engine_type,
bot_type)` capability entry point W1 built for W13 — so the apply service grows a
way to build a context from the creation attributes instead of a bot dict. That
is a smaller change than the callback it replaces, and it uses a seam that already
exists for this caller.

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

### K-5 No `apply_now` — the job reschedules instead

Rev 2 added a synchronous `apply_now` so phase A could finish before provisioning
began. With phase A moved ahead of `create_bot` (K-2), the job can simply use the
queue's own idiom:

```text
phase A not started?   start_apply(phases={PRE_CONTAINER}) → Reschedule(5s)
phase A still RUNNING? Reschedule(5s)
phase A terminal?      → create the bot
```

`start_apply` already does everything needed; nothing new goes on the service's
contract. The handler finds phase A's record with `last_apply(entity_id, bot_id)`
and recognises it by its `create:pre_container` trigger — the same read the poll
makes, and the same value it later passes as `carry_from_apply_id`, so no
repository method is added either.

The cost is one reschedule interval (5 s) added to a creation, immediately after a
step that waited on a **human clicking a link**. It is not measurable against
that.

Rejected alternatives, recorded because "why not just X" is the obvious question:

- **`start_apply` + poll the record inside the request** — a busy-wait in the
  creation path.
- **A `wait=True` flag on `start_apply`** — the return type is what actually
  differs (`ApplyAccepted` handle vs. a finished `ApplyReport`), so a boolean
  would make the return type conditional at every call site.
- **Write the startup-script row directly, skipping the apply engine** — no apply
  record, no per-entry report, no lock, and category knowledge leaks back out of
  the registry. W4's design exists to prevent exactly this.

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
| `core/bot_management/create_flow.py` | An optional creation-manifest seam, called at preflight and persist. **`bot_service.py` is not touched.** |
| `core/bot_config_manifest/services/config_manifest_apply_service.py` | `carry_from_apply_id`, `materialised_constructs`, and a record-free apply context. |
| `core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py` | The two new members. |
| `di/modules/bot_management_module.py` | Bind the creation service; register the handler in the task-queue registry. |
| `adapters/http/openapi_v1/__init__.py` | Mount the new router. |
| `di/config.py` (or the module that owns it) | The configurable creation deadline, default 600 s. |
| `core/bot_config_manifest/README.md`, `docs/bot-config-manifest/user-manual.zh-CN.md` | The creation flow, the poll states, the `script`-dependency rule. |

## Risks

1. ~~**The seam lands in the wrong place in `create_bot`.**~~ Retired by K-2:
   phase A precedes creation entirely, so there is no hook to misplace. A test
   still asserts the ordering, but it is now asserting a property of the sequence
   rather than guarding a fragile insertion point.
2. **A re-run of the handler double-creates or double-applies.** Every step is
   written as "is this already done?" and the two underlying operations are
   already idempotent (`create_bot` on a supplied id, `start_apply` under its
   lock). Tested by invoking the handler twice at each step.
3. **Silent tenant loss on the worker.** K-6; tested, not commented.
4. **The registry is the first production adopter of the handler registry.** Its
   docs say the registry is empty until an adopter registers handlers, so the
   wiring (bootstrap-time registration, `app_name` config) needs checking against a
   running app, not just unit tests.
5. **Phase A writes a startup-script row for a bot that may never exist.**
   Bounded by the same deadline and cleaned on the same terminal paths as the
   stored manifest (K-8).

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
