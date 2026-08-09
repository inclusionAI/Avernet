# Plan — Public API: Publish Lifecycle for Service Bots

Implements `spec.md` in this directory. Issue
[#909](https://github.com/inclusionAI/Avernet/issues/909).

All paths are relative to `src/backend/src/agentclaw/community/` unless stated
otherwise.

---

## 1. Shape of the change

Five public operations in a new `publish` category, backed by one new core
service that composes services that already exist. Nothing in the publish
pipeline changes: `PublishFlowService.process` is called exactly as the internal
router calls it, and the internal router is not touched.

```
adapters/http/openapi_v1/publish/router.py      ← thin: params, envelope, projection
        │  (UserIdDep, OwnerIdDep, PageParamsDep)
        ▼
core/service_bot/services/release_lifecycle_service.py   ← ALL policy lives here
        │
        ├── BotService.get_bot ............... owner-scoped, tenant-guarded resolve
        ├── core/bot_collaborator/access.py .. role bar (extracted, see §3)
        ├── BotPublishService ................ create-first / upgrade / read records
        ├── PublishFlowService.process ....... the two CAS advances (unchanged)
        └── PublishOperationRepository ....... the ledger read
```

The router owns no domain decision. That is the architecture rule (`core` stays
transport-agnostic; adapters translate protocol details) and it is also what
makes the state-machine behaviour testable without a TestClient.

### Endpoints

Component literal `publish`; addressing rule `/openapi/v1/bots/<component>/{bot_id}/…`.

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/openapi/v1/bots/publish/{bot_id}/releases` | Start a verify release | `202 Envelope[Release]` started · `200 Envelope[Release]` already in flight |
| GET | `/openapi/v1/bots/publish/{bot_id}/releases` | List releases, newest first | `Envelope[Page[Release]]` |
| GET | `/openapi/v1/bots/publish/{bot_id}/releases/{version}` | One release's state | `Envelope[Release]` |
| POST | `/openapi/v1/bots/publish/{bot_id}/releases/{version}/promote` | Promote verify → online | `202 Envelope[Release]` promoted · `200 Envelope[Release]` already promoted |
| GET | `/openapi/v1/bots/publish/{bot_id}/releases/{version}/operations` | The release's operation ledger | `Envelope[Page[ReleaseOperation]]` |

Every operation takes the required `user_id` query parameter and the optional
`owner_id` query parameter. **No `stage` parameter**: a release *is* the stage
transition; `stage` appears inside a ledger entry as data, never as an address.

`{version}` is `ac_bot_publish.version` — per-bot, monotonic from 1, assigned by
`BotPublishService._get_next_version`. Declared `int` with `ge=1`, so a
non-numeric segment is a 422 before any lookup runs.

---

## 2. Published contracts

### `ReleaseState` — the published projection of `PublishStatus`

A closed enum in the adapter's `schemas.py`, mapped 1:1 so nothing is collapsed
and no internal spelling leaks:

| `PublishStatus` | `ReleaseState` | Meaning published |
|---|---|---|
| `draft` | `draft` | Created, not started |
| `building` | `building` | Build running |
| `built` | `built` | Built, verify release starting |
| `validate_pub` | `publishing_verify` | Publishing to the verify runtime |
| `validating` | `verifying` | Verify runtime live, awaiting promotion |
| `online_pub` | `publishing_online` | Online publish running |
| `success` | `online` | Live online |
| `upgraded` | `superseded` | A newer version took over |
| `released` | `retired` | Taken offline |
| `failed` | `failed` | Failed |

The mapping table is exhaustive over `PublishStatus` and asserted so by a test —
a status added later fails CI here rather than escaping as an unmapped 500.

The accompanying `message` is the fixed English string
`PublishFlowService` already publishes for that status (`_DESCRIBE_STATUS_MESSAGES`
/ `_SYNC_ONLY_STATUS_MESSAGES`), obtained by calling `describe_publish` rather
than by copying the table. For `failed` the message is the fixed
`"Publish failed"` — **not** `describe_publish`'s
`f"Publish failed: {error_message}"`, which interpolates internal text
(spec Decision 6). The projection strips it; it is not read and then discarded.

### `Release` payload

Published: `version`, `state`, `message`, `bot_id`, `name`, `description`,
`created_at`, `updated_at` (ISO-8601).

Withheld, deliberately: `id` (internal pk), `source_bot_pk`, `publish_bot_id`,
`owner_id` (the caller supplied it), `last_pub_id`, `env`, `permission_owner`,
and the whole of `ext` — which carries `binding.{verify,online}` binding ids,
`migration_path`, `config_artifact`, `error_message`, retry flags and
`source_status`. `ext` is never read by the projection, so a key added to it
later cannot leak by default.

### `ReleaseOperation` payload

Published: `kind`, `stage`, `attempt`, `state`, `operator`, `created_at`,
`updated_at`.

`kind` and `state` are published enums mirroring `PublishOperationKind` and
`PublishOperationState` value-for-value (both are already stable, English,
caller-meaningful vocabularies); both mappings are asserted exhaustive, so a new
kind fails CI here as it already does in the deploy-partition test. `stage`
publishes the raw `PublishStage` value (`draft`/`verify`/`online`/`eval`) or the
empty string, unchanged.

Withheld: `id`, `publish_id`, `bot_uuid`, `baas_publish_id`, `params`, `result`,
`last_error`, `request_id`, `env` (spec Decision 7).

Ordering: `gmt_create` ascending, then `id` ascending — a timeline reads
forward, and ties inside one second stay stable across pages.

---

## 3. Extraction: the role bar

`core/engine_runtime/gate.py` already holds the level-parameterised half of the
role check (`resolve_operator_level`) welded to a fixed `OPERATOR_LEVEL` bar
(`require_bot_operator`). Publishing needs the same fail-closed lookup at a
*different* bar, and a `publishing` module importing `engine_runtime` for it
would be the wrong dependency direction.

**Move both into the domain that owns role policy** —
`core/bot_collaborator/access.py`, new file:

```python
def resolve_permission_level(collaborators, *, bot_pk, caller_id, owner_id) -> PermissionLevel
def require_bot_role(collaborators, *, bot_pk, bot_id, caller_id, owner_id, min_level) -> None
```

`require_bot_role` raises `BotNotFoundError` — the masked 404 — and logs both
ids at the refusal, byte-for-byte as `require_bot_operator` does today.

`core/engine_runtime/gate.py` keeps `OPERATOR_LEVEL`, `require_bot_operator` and
`resolve_operator_level` as thin, re-exported delegations to the new module, so
every existing import site and every existing test is untouched. This is a
behaviour-preserving extraction (recipe step 6): proved by leaving
`tests/…/openapi_v1/engine_runtime/test_operator_access.py` and
`tests/community/core/engine_runtime/` unmodified and green.

Bars used by this category:

- **Writes** (start, promote): `PermissionLevel.ADMIN` — `CollaboratorRole.ADMIN`
  is defined as *edit + publish* (spec Decision 2).
- **Reads** (get, list, operations): `PermissionLevel.MEMBER` — the same
  `OPERATOR_LEVEL` the access expansion applies to the runtimes these releases
  produce.

Both constants are named once in the new core service
(`PUBLISH_LEVEL` / `PUBLISH_READ_LEVEL`), not spelled at call sites.

---

## 4. New core service

`core/service_bot/services/release_lifecycle_service.py` —
`ReleaseLifecycleService`, `@inject`, singleton.

Collaborators: `BotService`, `CollaboratorServiceProtocol` (core),
`BotPublishService`, `PublishFlowService`, `PublishOperationRepository`, plus
`get_current_env()` for the env scope.

Returns transport-agnostic values only: `BotPublishRecord`,
`list[PublishOperationRecord]`, and a small frozen
`ReleaseAdvance(record, started: bool)` for the two writes — `started` is what
the adapter turns into 202 vs 200. No HTTP types, no dicts shaped like
responses.

### `_resolve_target(bot_id, owner_id, caller_id, *, min_level) -> BotFacts`

1. `BotService.get_bot(bot_id, owner_id)` — owner-scoped and tenant-guarded;
   a bot outside the tenant or under another owner raises `BotNotFoundError`.
2. `require_bot_role(..., bot_pk=bot["id"], min_level=min_level)`.
3. Bot type must be `service`, else `BotNotServiceTypeError`. **After** the role
   check, so a stranger does not learn the type.
4. Returns the existing `BotFacts` value object (`bot_id`, `bot_type`,
   `active_engine`, `owner_id`, `bot_pk`) — already the narrow, no-device-topology
   shape this surface needs, reused rather than re-invented.

Synchronous and not cheap (row read + template fetch + possible collaborator
query), so the service exposes an `async` façade that runs it via
`asyncio.to_thread`, exactly as `resolve_bot_off_loop` does.

### `start_verify_release(...) -> ReleaseAdvance`

After `_resolve_target(min_level=ADMIN)`:

1. Newest record for the bot: `publish_repo.list_by_source_bot(bot_pk, env)`
   returns newest-first; take `records[0]` (`None` when the bot has never
   published). Keyed on `bot_pk`, never `bot_id` (spec Decision 8).
2. Choose the draft to advance:

   | Newest record | Action |
   |---|---|
   | *none* | `create_first_publish_for_bot(...)` → DRAFT v1 |
   | `DRAFT` | use it |
   | `SUCCESS` | `upgrade_publish(record.id, owner_id)` → DRAFT vN+1 (idempotent via `last_pub_id`) |
   | `BUILDING`/`BUILT`/`VALIDATE_PUB`/`VALIDATING`/`ONLINE_PUB` | in flight → `ReleaseAdvance(record, started=False)`, **no side effect** |
   | `FAILED`/`UPGRADED`/`RELEASED` | `ReleaseNotStartableError` → 409 |

   `create_first_publish_for_bot` needs a name; it is derived from the bot's own
   name (`bot["bot_name"]`, falling back to `bot_id`). The public surface takes
   no release name: the internal one exists because the workbench asks a human
   for it, and inventing a required public field to satisfy an internal column
   would be a contract we then have to keep. `permission_owner` keeps its
   `"owner"` default. `description` is optional in the request body.
3. `await flow_service.process(publish_id=draft.id, operator=caller_id)`.
4. Re-read the record and compare: `started = result.status == BUILDING and
   the record was at DRAFT before the call`. Concretely — `process` returns
   `PublishFlowResult`; `started` is `result.action == "process" and
   result.status == PublishStatus.BUILDING`. The CAS loser gets the describe
   path, whose status is whatever the winner advanced to, so `started=False`
   falls out without a second query in the normal case.
5. Return `ReleaseAdvance(record=<re-read record>, started=...)`.

The record is re-read after `process` so the payload reports the state the
advance actually produced, not the pre-call snapshot.

### `promote_release(...) -> ReleaseAdvance`

After `_resolve_target(min_level=ADMIN)` and `_load_release(bot_facts, version)`:

| Record state | Result |
|---|---|
| `VALIDATING` | `process` → `ReleaseAdvance(started=True)` on `ONLINE_PUB`, `started=False` if the CAS was lost |
| `ONLINE_PUB` / `SUCCESS` | `ReleaseAdvance(started=False)` — already promoted |
| anything else | `ReleaseNotPromotableError` → 409 |

`process` is called **only** from `VALIDATING`, so promotion can never fall
through into the `DRAFT` branch of the flow's own dispatch.

### `_load_release(bot_facts, version) -> BotPublishRecord`

`publish_repo.get_by_publish_bot_id_and_version(bot_facts.bot_id,
bot_facts.owner_id, version, env)`, then **assert
`record.source_bot_pk == bot_facts.bot_pk`** and raise `PublishNotFoundError`
otherwise. The repository lookup is already owner-scoped; the primary-key assert
is what closes the `bot_id`-is-not-unique hole for good (spec Decision 8).

### `list_releases(...) -> tuple[int, list[BotPublishRecord]]`

`list_by_source_bot(bot_pk, env)` (newest-first), sliced in the service for the
page window, with the full count as `total`. `list_by_source_bot` has no
limit/offset parameters and the per-bot release count is small and bounded by
how many times a human published; adding pagination to the repository protocol
would change a contract three other callers share, for no benefit here. This is
stated in the service docstring so the choice reads as deliberate.

### `list_release_operations(...) -> tuple[int, list[PublishOperationRecord]]`

`publish_operation_repo.list_by_publish_id(record.id)` after `_load_release`,
sorted ascending by `(gmt_create, id)`, sliced the same way. Because the
`publish_id` comes from a record already proven to belong to the addressed bot,
no ledger row from another bot can be reached.

### Errors

New, in `core/service_bot/services/release_errors.py` (dependency-free, so the
adapter can map them without importing the service):

- `ReleaseNotStartableError`
- `ReleaseNotPromotableError`

Both subclass the existing `BotPublishServiceError` so an unmapped-anywhere path
still lands on a sane base — and both are therefore mapped **before** it in
`ENVELOPE_ERRORS` (base class last; the surface's standing order rule).

---

## 5. Service API protocol + DI

- `api/release_lifecycle_service.py` — `ReleaseLifecycleServiceProtocol`,
  `@runtime_checkable`, with **real signatures** (not `*args/**kwargs`): the
  architecture gate `tests/community/architecture/test_service_api_conformance.py`
  checks conformance for registered pairs, and a new protocol should be written
  to the standard the gate exists to enforce.
- Register the `(ReleaseLifecycleServiceProtocol, ReleaseLifecycleService)` pair
  in that gate's registry.
- Bind in `di/modules/service_bot_module.py`, beside
  `_publish_flow_service_protocol` — same module, same publish collaborators,
  same database-mode-keyed repository bindings.

---

## 6. Adapter

New package `adapters/http/openapi_v1/publish/` — `__init__.py`, `router.py`,
`schemas.py`.

- `APIRouter(prefix="/bots/publish", tags=["publish"])`, mirroring the
  engine-runtime groups' literal-first prefix.
- Handlers: `require_principal` (`PrincipalDep`), `UserIdDep`, `OwnerIdDep`
  (imported from `openapi_v1/engine_runtime/params.py` — one spelling of
  `owner_id` on the surface, per that module's own instruction), `PageParamsDep`,
  `request: Request`, `@envelope_errors`.
- Responses via `responses.py` builders: `envelope`, `page`, `accepted`. The
  202-vs-200 split is `accepted(...)` vs `envelope(...)`, chosen from
  `ReleaseAdvance.started`; the route declares `status_code=202` and the handler
  returns a `JSONResponse`-free model, so the 200 branch sets
  `response.status_code = 200` on the injected `Response`. (`bots/router.py`'s
  create handler already does exactly this for its `201`/`202` split — follow it,
  don't invent a second mechanism.)
- Mount in `openapi_v1/__init__.py`: add `publish_router` to `_SUBGROUPS`
  (before the bots router, per the mount-order rule) with
  `USER_SCOPED_ERROR_RESPONSES` — which already documents the 403 and the 409
  this category needs, and none of the 501/504 it cannot return.

### `ENVELOPE_ERRORS` additions (`openapi_v1/responses.py`)

Order matters — leaves before their base:

| Exception | Status | Fixed message |
|---|---|---|
| `service_bot…bot_publish_service.BotNotFoundError` | 404 | `"Not found"` |
| `PublishNotFoundError` | 404 | `"Not found"` |
| `BotNotServiceTypeError` | 409 | `"Operation not supported for this bot"` |
| `ReleaseNotStartableError` | 409 | `"Release cannot be started from its current state"` |
| `ReleaseNotPromotableError` | 409 | `"Release is not awaiting promotion"` |
| `PublishStatusInvalidError` | 409 | `"Release is not in a state that allows this operation"` |
| `PublishAlreadyExistsError` | 409 | `"Release already exists"` |
| `BotPublishServiceError` *(base — last)* | 500 | `"Publish service error"` |
| `PublishFlowServiceError` | 500 | `"Publish service error"` |

**Trap to avoid:** `service_bot.services.bot_publish_service.BotNotFoundError` is
a *different class* from `bot_management.services.bot_service.BotNotFoundError`,
which the table already maps. Both must be present, and the 404 messages must be
byte-identical so "no such bot" and "not your bot" stay indistinguishable.

Business subcodes follow the existing scheme (`xxx000` unless the category needs
distinguishable leaves); the four 409s here are operationally distinct, so they
get `409` + a per-error subcode in `ERROR_SUBCODES`, matching how the skills
category already differentiates its conflicts.

---

## 7. Documentation

- `docs/openapi-v1/README.md`:
  - Track B status board: new `publish` row, `✅ DONE — PR #___`.
  - New endpoint section with the five-row table, the state map, the ledger's
    published/withheld field sets, and the role bar.
  - Reserved names: add `publish` to the `<!-- reserved-component-names -->`
    fenced list (the routed one — a route publishes it in this change, so it
    must not go in the unrouted list).
  - Deferred-items list: strike the publish-lifecycle entry pointing at #909.
  - Changelog: dated line.
- `docs/openapi-v1/README.zh-CN.md`: the same edits, mirrored.

---

## 8. Tests

New, under `tests/community/adapters/http/openapi_v1/publish/`:

| File | Pins |
|---|---|
| `test_publish_endpoints.py` | all five handlers, success + every mapped error; the 202/200 split; `extra="forbid"` on the request body |
| `test_release_state_machine.py` | start and promote from **each** of the ten `PublishStatus` values, plus both CAS-loser paths |
| `test_publish_access.py` | role matrix — owner / admin collaborator / member collaborator / stranger × writes and reads; every refusal byte-identical to a missing bot |
| `test_publish_projection.py` | the state map is exhaustive over `PublishStatus`; the kind/op-state maps exhaustive over their enums; the withheld field sets absent from both payloads; no `ext` key reachable |
| `test_publish_tenant_isolation.py` | a foreign tenant's bot and a foreign bot's version are both masked 404s, against the real Track A guard |

Under `tests/community/core/service_bot/services/`:

| File | Pins |
|---|---|
| `test_release_lifecycle_service.py` | the draft-resolution table, `_load_release`'s `source_bot_pk` assert, ledger scoping, the read/write bar constants |

Amended:

- `test_path_convention.py` — reserved-name lists (doc ↔ routes) pick up `publish`
  automatically; the run must confirm it.
- `test_explicit_user_id.py` — the counts move from 56/4 to 61/4; update the
  expectation.
- `test_openapi_error_schema.py` — the new group must document exactly the
  `USER_SCOPED_ERROR_RESPONSES` set.
- `tests/community/architecture/` — the new cross-module imports must be declared
  in the touched modules' `README.md` `## Context Boundary` sections
  (`core/service_bot` ← `core/bot_collaborator`, `adapters/http/openapi_v1`
  ← the new protocol). This has failed CI twice before on exactly this;
  declare first, then run.

Unmodified and green, as the proof that nothing internal moved:
`tests/community/adapters/http/service_bot/test_router_publish_coverage.py`,
`tests/community/core/service_bot/services/test_publish_flow_service.py`,
`tests/community/e2e/publish_boundary/`, and the engine-runtime operator/stage
suites.

---

## 9. Rejected alternatives

Recorded so they are not reopened:

| Rejected | Why |
|---|---|
| Publish `POST …/releases` as *advance only*, requiring the record to be minted elsewhere | Leaves the public surface unable to start a first release at all; the draft record is storage, not a user-facing artifact (spec Decision 1) |
| A second public write that mints the release record | Two calls for one intent, and the second is pure bookkeeping the caller cannot reason about |
| Address a release by its record id | Global auto-increment; leaks cross-tenant volume, and its meaning is internal (spec Decision 9) |
| Collapse the ten statuses into three or four coarse phases | The whole point of the read side is following progress; `built` vs `publishing_verify` is exactly the distinction a stalled publish turns on |
| Return 409 to the loser of a double-submit | Makes correct retry logic read as failure on an API that is asynchronous by construction (spec Decision 3) |
| Publish `ext.error_message` on a failed release | Raw internal text — exception reprs, provider ids, internal-language strings; the surface's fixed-message rule exists for this |
| Publish the ledger's `params` / `result` / `baas_publish_id` | Unversioned internal payloads and provider-side identifiers would become a contract the moment they shipped |
| Hide ledger kinds this surface cannot trigger (rollback, restore, scale) | Would make `attempt` numbering and the timeline lie about the bot's own history |
| One role bar for the whole category | Either hides progress from members who can already watch the runtime, or lets them ship (spec Decision 2) |
| A `stage` query parameter, matching the engine-runtime groups | A release *is* the stage transition; `stage` is data inside a ledger entry, not an address |
| Add `avernet_tenant` to the publish tables | Not this change's job; isolation already derives from the tenant-guarded bot resolve plus primary-key keying, the same argument PR #904 made (spec Decision 8) |

---

## 10. Risk and rollback

- **No schema change, no DDL, no migration.** Rollback is reverting the code.
- **The internal surface is untouched**, so a revert cannot strand a release: any
  record this surface created is an ordinary draft the internal surface already
  knows how to drive.
- **The one shared-code edit is the role-bar extraction (§3).** It is
  behaviour-preserving and its proof is the engine-runtime suites staying
  unmodified and green. If review prefers not to move it, the fallback is a
  `min_level` parameter on `require_bot_operator` with the current bar as the
  default — smaller, but leaves `publishing` importing `engine_runtime`.
- **The surface still answers 401 in every environment without a gateway
  principal signing key**, exactly like the other categories. Like them, this
  category's end-to-end verification is blocked on the same event, and the tests
  above exercise the handlers directly.
