# Plan: Creating a Bot With Its Configuration (W13)

Spec: `spec.md` in this directory. Work item W13, issue #1696.

## What already exists, and what that leaves

W4 built this item's engine on purpose. Before designing anything, here is what
is already in place and must be *used* rather than re-created:

| Already there | Where | What it gives W13 |
| --- | --- | --- |
| `validate(document, active_engine, bot_type)` | `BotConfigManifestServiceProtocol` | Validation with no bot record — its docstring already names this path |
| `resolve_capabilities(active_engine, bot_type)` | `capabilities.py` | The second entry point, written for W13's preflight |
| `start_apply(..., phases=...)` | `config_manifest_apply_service.py` | One phase at a time, with the tenant bound onto the thread |
| `ApplyPhase.PRE_CONTAINER` / `ON_CONTAINER` | `apply/order.py` | `script` alone vs. everything else |
| `build_materialisers()` | `apply/registry.py` | The live set of constructs something can act on |
| `declared_entries(parsed, construct)` | `apply/orchestrator.py` | "Did the document declare this category?" |
| `trigger` column, `String(32)` | `apply_models.py` | Its comment already reserves `create`; no migration |
| `DeviceActivatedEvent` + `LifecycleBase` listeners | `core/events`, `skill_symlink_listener.py` | The platform-side "container is up" signal |

So the work is **sequencing and a public surface**, not machinery. Four things do
not exist yet: a preflight that also demands a materialiser, a seam in
`create_bot` between the row and the container, a listener that runs phase B once
per creation, and the endpoint pair.

## Architecture

```text
POST /openapi/v1/bots/with-manifest
  │
  ├─ _prepare_create (existing policy; may rewrite engine)
  ├─ preflight: quota / name / engine        ← existing
  ├─ preflight: manifest                     ← NEW  validate + materialiser gate
  ├─ persist manifest  key=(entity_id, bot_id)   ← NEW  leg 1, no schema change
  └─ Passport apply → 202 AWAITING_AUTHORIZATION

POST /openapi/v1/bots/{bot_id}/with-manifest/status      (the poll)
  │
  ├─ complete_bot_authorization (existing)
  │     PENDING  → AWAITING_AUTHORIZATION
  │     ¬ISSUED  → AUTHORIZATION_REJECTED
  │     ISSUED   → re-validate stored manifest vs. effective engine
  │                └─ bot_service.create_bot(..., pre_provision=…)
  │                      ├─ row insert + template            (existing)
  │                      ├─ pre_provision(bot)  ← NEW SEAM   phase A, synchronous
  │                      └─ device provisioning              (existing)
  └─ derive state from the latest apply record

DeviceActivatedEvent ──► ManifestCreateApplyListener  ← NEW
                              guard: latest apply is this creation's phase A
                              start_apply(ON_CONTAINER, carry_from=<phase A id>)
```

The two ends of the state machine are read, never stored: authorization comes
from Passport, everything after it is derived from the bot record plus the apply
record. **No new table, no new column.**

## Key decisions

### K-1 The materialiser gate is derived, not listed

`spec.md` requires that W5/W6 widen this endpoint by landing. A module-level
constant listing `{script, mcp}` would drift, so the set comes from the registry
itself: the apply service grows

```python
def materialised_constructs(self) -> frozenset[ApplyConstruct]:
    return frozenset(build_materialisers(...).keys())
```

on `BotConfigManifestApplyServiceProtocol`. It builds the same registry
`_orchestrator()` builds, so the two cannot disagree — and it costs three
singleton lookups, not a network call.

The preflight then refuses any construct the document **declares** that is not in
that set. "Declares" is `declared_entries(parsed, construct) is not None` walked
over `APPLY_ORDER` — the same distinction the orchestrator uses, so a
declared-empty category (`mcp: []`, which *removes*) is treated as declared, and
an absent one is not.

This is strictly narrower than `PUT`. `PUT` accepts `skills` today and lets it sit
inert; this refuses it until W5 lands, because here the cost of accepting is a
Passport application, a user's click and a live bot.

### K-2 One seam in `create_bot`, and it is generic

`BaasService._build_create_bot_payload` reads `ac_bot_startup_script` while
composing the start command, and it runs inside device provisioning — which
`create_bot` calls after inserting the row. So phase A's window is *inside*
`create_bot`, between the insert and provisioning. `create_bot` gets:

```python
pre_provision: Callable[[dict], None] | None = None
```

invoked once, after the row (and any template) exists and before any provisioning
branch. It takes the bot record and returns nothing.

- **Generic, not manifest-shaped.** `BotService` is already 5000 lines; giving it
  a manifest dependency to call would put a second copy of "does this bot have a
  manifest" beside the one the apply service owns. A callback keeps that knowledge
  in the caller.
- **It must not raise.** `create_bot` wraps the call and logs. This is spec D-5
  mechanically enforced: a manifest-layer failure can never abort creation or
  leave a half-created bot.
- `BotServiceProtocol.create_bot` is `(*args, **kwargs)`, so no protocol change and
  no conformance-test churn.

### K-3 Two applies, two triggers, one carried report

Phase A and phase B are separate `start_apply` calls separated by the whole of
container provisioning, with distinct triggers that fit the existing `String(32)`
column:

- `create:pre_container`
- `create:on_container`

The triggers are the state machine's marker. Without them, phase A's terminal
`SUCCEEDED` record is indistinguishable from the whole creation's, and a poll
would report `READY` while the container was still coming up.

To keep the terminal report complete (spec: "including the entries from both
phases"), `start_apply` grows `carry_from_apply_id: str | None`. When set, the
service loads that record's report and prepends its categories to the report it
finishes with, re-deriving the summary over the union — so a phase A that failed
and a phase B that succeeded terminate as `PARTIAL`, which the poll reports as
`FAILED`. Phase A's own record stays readable on its own.

### K-4 Phase A is synchronous; `start_apply` is not

`start_apply` returns as soon as the thread starts, which is wrong for phase A —
provisioning must not begin until the script row exists. Rather than have W13
bypass the orchestrator (the thing W4's shape exists to prevent), the service
grows a sibling:

```python
def apply_now(self, *, ..., phases) -> ApplyReport
```

the same body as `start_apply` minus the thread: lock, validate, record `RUNNING`,
run `asyncio.run(orchestrator.apply(...))` inline, finish, release. It shares the
private helpers so there is one lifecycle, not two. Phase A is a database write
with no fetch and no device — bounded work on a request thread.

### K-5 Phase B hangs on device activation, guarded to fire once

A new `ManifestCreateApplyListener`, modelled on `SkillSymlinkListener`
(`LifecycleBase`, subscribes in `startup()`, resolves the bot from the event's
device binding via `BotRepository`). It acts only when **all** of:

1. the bot has a stored manifest;
2. the latest apply record for that bot exists and its trigger is
   `create:pre_container`.

Condition 2 is what keeps this out of W8's territory: the activation event also
fires on restarts and re-publishes, and on those the latest apply is an explicit
one, a republish one, or absent — so the listener does nothing. It is also what
makes phase B fire exactly once: as soon as phase B starts, the latest trigger is
`create:on_container`.

Phase A therefore runs on the creation path **even when the manifest declares no
`script`** — an empty report, but the marker the listener keys on. This is a real
constraint on the implementation, not an accident, and gets its own test.

### K-6 The poll re-validates before it creates

The poll echoes creation attributes (like today's auth-status poll), so the engine
at completion can differ from the one the manifest was validated against.
Capabilities are engine-dependent, so before `create_bot` runs, the stored
document is re-validated against the effective engine and re-gated on the
materialiser set. It fails while nothing has been created — the one moment where
failing is free.

### K-7 State derivation table

| Observed | Reported |
| --- | --- |
| No bot record; Passport `PENDING` (or not ready) | `AWAITING_AUTHORIZATION` + handles |
| No bot record; Passport terminal-not-`ISSUED` | `AUTHORIZATION_REJECTED` |
| Bot record; no apply record | `CREATING` |
| Latest apply trigger `create:pre_container` | `CREATING` |
| Latest apply trigger `create:on_container`, `RUNNING` | `APPLYING` |
| …terminal `SUCCEEDED` | `READY` + report |
| …terminal `PARTIAL` or `FAILED` | `FAILED` + report |

Two edges worth stating rather than discovering:

- **Provisioning failed.** The bot record exists but no container will ever
  activate, so phase B never starts and the table above would sit at `CREATING`
  forever. The poll checks the bot's own status and reports `FAILED` with a
  message naming provisioning, not the manifest. The six-state machine has no
  `CREATE_FAILED`, and inventing one here would change a contract the issue fixed.
- **A creation with no manifest** through this endpoint: phase A still runs (an
  empty apply), so the same table applies unchanged.

### K-8 Default-off switch

`BOT_CONFIG_MANIFEST_CREATE_ENABLED`, read **per request** (so tests can set it)
by a FastAPI dependency in the new adapter module; when off, both routes answer
`404` — an unreleased endpoint should not advertise itself. The comment at the
definition carries §2.11's reason: leg 1 writes a manifest row keyed by a `bot_id`
that may never become a bot, nothing caps those rows, deleting a bot never reaches
them, and allocating a `bot_id` consumes no quota — so #1698 (expiry) is the
precondition for turning it on.

## Files

### New

| File | What |
| --- | --- |
| `core/bot_config_manifest/creation.py` | The creation-leg seam: preflight (validate + materialiser gate), persist, phase A, re-validate. Pure functions + one small service over the two manifest services. |
| `adapters/http/openapi_v1/bots/create_with_manifest.py` | The two routes, the switch dependency, the state mapping. |
| `adapters/http/openapi_v1/bots/schemas_create_with_manifest.py` | Request/response models, incl. the `CreationState` enum. |
| `core/bot_config_manifest/apply/create_listener.py` | `ManifestCreateApplyListener` — phase B on `DeviceActivatedEvent`. |
| `tests/community/…` | See the task list. |

### Changed

| File | Change |
| --- | --- |
| `core/bot_management/services/bot_service.py` | `create_bot(..., pre_provision=None)`; invoke after row/template, before provisioning, non-raising. |
| `core/bot_management/create_flow.py` | Both flow functions take an optional creation-manifest seam and call it at the three points. |
| `core/bot_config_manifest/services/config_manifest_apply_service.py` | `apply_now`, `carry_from_apply_id`, `materialised_constructs`. |
| `core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py` | The three new members. |
| `di/modules/bot_management_module.py` | Bind the creation service and the listener. |
| `adapters/http/openapi_v1/__init__.py` | Mount the new router. |
| `core/bot_config_manifest/README.md`, `docs/bot-config-manifest/user-manual.zh-CN.md` | The creation flow, the poll states, the `script`-dependency rule. |

## Risks

1. **The seam lands in the wrong place in `create_bot`.** The whole item is worth
   nothing if the script row is written after the payload is composed. Pinned by a
   test that asserts ordering directly, not by reading the code.
2. **The listener widens into W8.** Guard 2 (K-5) is the fence; a test drives a
   restart activation and asserts nothing applies.
3. **Tenant loss on the listener's thread.** No request is behind it. It follows
   the established `bind_current_avernet_tenant(...)` wrap at the `Thread(...)`
   construction site — never as a decorator — and a test asserts the tenant seen
   inside the apply.
4. **`create_bot`'s many other callers.** The new parameter is keyword-only with a
   `None` default and every existing call site is untouched; the existing suites
   run unedited as the check.

## Testing strategy

- **Unit** — the preflight gate (each unsupported construct refused, each
  supported one accepted, a registry with a stub materialiser widening the gate);
  the state-derivation table, case by case; the report merge (`PARTIAL` from a
  failed phase A + clean phase B).
- **Ordering** — phase A completes before provisioning is entered, asserted on
  call order rather than on timing.
- **Listener** — fires on the creation's first activation; does nothing on a
  restart activation, on a bot with no manifest, and on a second activation.
- **Tenancy** — the tenant observed inside phase A and inside phase B equals the
  tenant of the request that created the bot.
- **Endpoint** — the full flow through the app: submit → 202 → poll pending →
  poll issued → `CREATING` → `APPLYING` → `READY`; an invalid manifest refused
  with `422` before Passport is touched; a construct with no materialiser refused
  at submission; a partial apply reported as `FAILED` with the bot still running;
  the switch off giving `404`.
- **Regression** — every existing create, auth-status, manifest, apply and
  startup-script test passes **unedited**. That is the criterion for "nothing else
  moves".
