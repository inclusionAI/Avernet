# Plan — Public API: Publish Lifecycle for Service Bots

Implements `spec.md` in this directory. Issue
[#909](https://github.com/inclusionAI/Avernet/issues/909).

**Read `design.md` first.** During review the scope widened: the public surface
is built on an *evolved* internal core (the `core/service_bot/release/`
package — typed release facts, one store for `ext`, the status machine as
data, the ledger as the single home of BaaS workflow ids), not on the current
mixin pipeline as-is. `design.md` defines that evolution and its phasing;
this plan implements **Phase 1** of it plus the public category.

All paths are relative to `src/backend/src/agentclaw/community/` unless stated
otherwise.

---

## 1. Shape of the change

Four public operations in a new `publish` category, on top of the Phase-1 core
evolution from `design.md`. The stored JSON keys, the internal HTTP surface and
the DB schema do not change; the shape of the code does. Neither write mints a
release record — creating a service bot already creates its first draft
(`bot_service.create_bot`, the `resolved_bot_type == "service"` branch), so a
first release has a draft to advance from the moment the bot exists.

```
adapters/http/openapi_v1/publish/router.py      ← thin: params, envelope, projection
        │  (UserIdDep, OwnerIdDep, PageParamsDep)
        ▼
core/service_bot/release/lifecycle.py            ← ALL public-surface policy
        │
        ├── BotService.get_bot ............... owner-scoped, tenant-guarded resolve
        ├── core/bot_collaborator/access.py .. role bar (extracted, see §3)
        ├── release/machine.py ............... the declared status machine
        ├── release/store.py ................. typed ext + CAS advance
        └── release/operations.py ............ ledger-backed workflow-id reads

core/service_bot/release/                        ← Phase 1 of design.md §3–§4
        facts.py / store.py / machine.py / operations.py
        (PublishExtState absorbed; ext id-stash writes stopped;
         process() consults the machine)
```

The router owns no domain decision. That is the architecture rule (`core` stays
transport-agnostic; adapters translate protocol details) and it is also what
makes the precondition behaviour testable without a TestClient.

### Endpoints

Component literal `publish`; addressing rule `/openapi/v1/bots/<component>/{bot_id}/…`.

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/openapi/v1/bots/publish/{bot_id}/releases` | Start a verify release — **requires the newest release to be a draft** | `202 Envelope[Release]` |
| GET | `/openapi/v1/bots/publish/{bot_id}/releases` | List releases, newest first | `Envelope[Page[Release]]` |
| GET | `/openapi/v1/bots/publish/{bot_id}/releases/{version}` | One release's state | `Envelope[Release]` |
| POST | `/openapi/v1/bots/publish/{bot_id}/releases/{version}/promote` | Promote verify → online — **requires the release to be validating** | `202 Envelope[Release]` |

The operation-ledger endpoint from the first draft of this plan is **dropped**
(spec Decision 4): `ac_publish_operation` stays internal, and nothing in this
change reads it.

Every operation takes the required `user_id` query parameter and the optional
`owner_id` query parameter. **No `stage` parameter**: a release *is* the stage
transition. **No request body on either write**: the operations take no
arguments beyond their address, so there is nothing for a body to carry, and no
body means no `extra="forbid"` surface to get wrong.

`{version}` is `ac_bot_publish.version` — per-bot, monotonic from 1, assigned by
`BotPublishService._get_next_version`. Declared `int` with `ge=1`, so a
non-numeric segment is a 422 before any lookup runs.

The start write takes no version: it advances the bot's newest release, and its
precondition is exactly that the newest release is a draft. The response carries
the version it advanced.

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
  is defined as *edit + publish* (spec Decision 3).
- **Reads** (get, list): `PermissionLevel.MEMBER` — the same `OPERATOR_LEVEL`
  the access expansion applies to the runtimes these releases produce.

Both constants are named once in the new core service
(`PUBLISH_LEVEL` / `PUBLISH_READ_LEVEL`), not spelled at call sites.

---

## 4. The Phase-1 core evolution (from `design.md`)

Executed before the public surface is wired, in this order:

1. **`release/facts.py`** — `ReleaseFacts` (`StageBindings`, `BuildArtifact`,
   `FailureInfo`, `engine_overrides_by_stage`, opaque `passthrough`), with
   `from_ext`/`to_ext` round-tripping the existing JSON keys byte-compatibly.
   Contract: a round-trip property test over real-shaped ext blobs, including
   legacy variants.
2. **`release/store.py`** — `ReleaseStore` absorbs `PublishExtState` (same
   CAS/status+ext persistence semantics, verbatim), exposing
   `load`/`mutate`/`advance`/`advance_with_facts` in `ReleaseFacts` terms.
   `PublishExtState` becomes a thin delegating shell for the one release it
   takes the mixins to migrate off it, then dies with them (Phase 2).
3. **`release/machine.py`** — `RELEASE_MACHINE` as declared in design §3.3.
   `process()` is refactored to consult it for the USER transition instead of
   its two hand-written branches; the failure writes take each transition's
   `on_failure` target from the machine instead of hand-writing
   `ext["source_status"]` at eight sites. Same transitions, same messages,
   same wire payloads — the internal suite passing **unmodified** is the proof.
4. **`release/operations.py`** — ledger-backed workflow-id reads
   (`latest_release_workflow` / `latest_restart_workflow` /
   `latest_scale_workflow` / `is_restart_in_flight`) with read-fallback to the
   legacy ext keys for pre-#197 rows. The ext id-stash **writes stop**:
   `ext.publish.{stage}` (`publish_ext_mixin`, `release_stage`),
   `ext.restart.{stage}` + `restart.restarting` (`restart_mixin`),
   `ext.scale.publish_id` (`scale_mixin`); their readers
   (`progress_sync_mixin`, `retry_ops_mixin`, `rollback_ops_mixin`,
   `upgrade_resolution_mixin`, `restart_mixin:525`, `baas_service:3425`,
   `arca_image_pin:134`, `bot_publish_service:641` — the binding reads among
   these move to `StageBindings`, the id reads to `operations.py`) are
   converted call-site by call-site. Rolling-deploy safety per design §4.
5. **Architecture test** — outside `release/facts.py` and `release/store.py`,
   no module under `core/service_bot` touches `ext[`/`ext.get(`/
   `ext.setdefault(` on a publish record. (Grandfathered exceptions, each
   named in the test with its Phase-2/3 ticket: the approval / rollback /
   draft-restore / eval mixins' `passthrough` keys.)

What Phase 1 does **not** touch, per design §4: the durable task handlers'
semantics, the #197 ledger writes, the internal HTTP surface, the DB schema,
and the hitchhiking domain states (approval, rollback, eval, data-init).

---

## 5. New core service

`core/service_bot/release/lifecycle.py` —
`ReleaseLifecycleService`, `@inject`, singleton.

Collaborators: `BotService`, `CollaboratorServiceProtocol` (core),
`BotPublishRepositoryProtocol`, `ReleaseStore`, `TaskQueueService` (for the
enqueue on a won advance — the same `enqueue_verify_flow` /
`enqueue_online_release` helpers the flow uses), plus `get_current_env()` for
the env scope.

The two writes drive the USER transitions **directly** through
`ReleaseStore.advance` — the same CAS `process()` uses — and enqueue the same
durable task on a win, consulting `RELEASE_MACHINE` so this service and
`process()` cannot disagree about what the two transitions are. A lost CAS is
a boolean, not a parsed message (this replaces the message-comparison
mechanism from an earlier draft of this plan; see design §3.5).

Returns transport-agnostic values only: `BotPublishRecord` and
`(total, list[BotPublishRecord])`. No HTTP types, no dicts shaped like
responses. The writes return the **re-read record** after the advance, so the
payload reports the state the advance actually produced.

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

### `start_verify_release(...) -> BotPublishRecord`

After `_resolve_target(min_level=ADMIN)`:

1. Newest record for the bot: `publish_repo.list_by_source_bot(bot_pk, env)`
   returns newest-first; take `records[0]`. Keyed on `bot_pk`, never `bot_id`
   (spec Decision 7).
2. **Precondition:** the newest record exists and `status == DRAFT`. Anything
   else — no record at all (a data anomaly for a service bot, since creation
   mints the draft), `BUILDING`…`ONLINE_PUB` in flight, `SUCCESS`, `UPGRADED`,
   `RELEASED`, `FAILED` — raises `ReleaseNotStartableError`. **No side effect on
   this path**: nothing is minted, nothing is advanced.
3. Advance via the machine: look up the USER transition from `DRAFT` in
   `RELEASE_MACHINE` and drive it with `ReleaseStore.advance(draft.id,
   BUILDING, DRAFT)`. `False` means a concurrent submit won between the
   precondition read and the CAS — raise `ReleaseNotStartableError`, the same
   answer as step 2, per spec Decision 2.
4. On a win, enqueue the transition's durable task (`enqueue_verify_flow`,
   the same helper `process()` uses) — CAS-then-enqueue in the same order as
   `process()`, so the double-submit guarantee is identical.
5. Re-read and return the record.

### `promote_release(...) -> BotPublishRecord`

After `_resolve_target(min_level=ADMIN)` and `_load_release(bot_facts, version)`:

1. **Precondition:** `status == VALIDATING`. Anything else — including
   `ONLINE_PUB` (already promoted) and `SUCCESS` (already online) — raises
   `ReleaseNotPromotableError`. No side effect on this path.
2. Advance via the machine: `ReleaseStore.advance(record.id, ONLINE_PUB,
   VALIDATING)`; `False` (the CAS loser) ⇒ `ReleaseNotPromotableError`, the
   same answer as step 1.
3. On a win, enqueue `enqueue_online_release` — same order as `process()`.
4. Re-read and return the record.

Both writes and `process()` drive **the same two machine transitions through
the same CAS**, so the surfaces cannot drift, and a stale observation advances
nothing even under a race. A machine-agreement test pins that the transitions
this service drives are exactly the machine's USER-driven set.

### `_load_release(bot_facts, version) -> BotPublishRecord`

`publish_repo.get_by_publish_bot_id_and_version(bot_facts.bot_id,
bot_facts.owner_id, version, env)`, then **assert
`record.source_bot_pk == bot_facts.bot_pk`** and raise `PublishNotFoundError`
otherwise. The repository lookup is already owner-scoped; the primary-key assert
is what closes the `bot_id`-is-not-unique hole for good (spec Decision 7).

### `list_releases(...) -> tuple[int, list[BotPublishRecord]]`

`list_by_source_bot(bot_pk, env)` (newest-first), sliced in the service for the
page window, with the full count as `total`. `list_by_source_bot` has no
limit/offset parameters and the per-bot release count is small and bounded by
how many times a human published; adding pagination to the repository protocol
would change a contract three other callers share, for no benefit here. This is
stated in the service docstring so the choice reads as deliberate.

### `get_release(...) -> BotPublishRecord`

`_resolve_target(min_level=MEMBER)` then `_load_release`.

### Errors

New, in `core/service_bot/release/errors.py` (dependency-free, so the
adapter can map them without importing the service):

- `ReleaseNotStartableError`
- `ReleaseNotPromotableError`

Both subclass the existing `BotPublishServiceError` so an unmapped-anywhere path
still lands on a sane base — and both are therefore mapped **before** it in
`ENVELOPE_ERRORS` (base class last; the surface's standing order rule).

---

## 6. Service API protocol + DI

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

## 7. Adapter

New package `adapters/http/openapi_v1/publish/` — `__init__.py`, `router.py`,
`schemas.py`.

- `APIRouter(prefix="/bots/publish", tags=["publish"])`, mirroring the
  engine-runtime groups' literal-first prefix.
- Handlers: `require_principal` (`PrincipalDep`), `UserIdDep`, `OwnerIdDep`
  (imported from `openapi_v1/engine_runtime/params.py` — one spelling of
  `owner_id` on the surface, per that module's own instruction), `PageParamsDep`,
  `request: Request`, `@envelope_errors`.
- Responses via `responses.py` builders: `envelope`, `page`, `accepted`. Both
  writes declare `status_code=202` and return `accepted(...)` — there is no
  alternate success status, because a call that did not advance the release is a
  409, not a success.
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
| `ReleaseNotStartableError` | 409 | `"Release is not in a state that can be started"` |
| `ReleaseNotPromotableError` | 409 | `"Release is not awaiting promotion"` |
| `PublishStatusInvalidError` | 409 | `"Release is not in a state that allows this operation"` |
| `BotPublishServiceError` *(base — last)* | 500 | `"Publish service error"` |
| `PublishFlowServiceError` | 500 | `"Publish service error"` |

**Trap to avoid:** `service_bot.services.bot_publish_service.BotNotFoundError` is
a *different class* from `bot_management.services.bot_service.BotNotFoundError`,
which the table already maps. Both must be present, and the 404 messages must be
byte-identical so "no such bot" and "not your bot" stay indistinguishable.

Business subcodes follow the existing scheme (`xxx000` unless the category needs
distinguishable leaves); the three publish 409s are operationally distinct, so
they get `409` + a per-error subcode in `ERROR_SUBCODES`, matching how the
skills category already differentiates its conflicts.

---

## 8. Documentation

- `docs/openapi-v1/README.md`:
  - Track B status board: new `publish` row, `✅ DONE — PR #___`.
  - New endpoint section with the four-row table, the state map, the
    precondition rule for the two writes, the role bar, and the **known
    limitation** (re-publishing a live bot still needs the internal upgrade to
    mint the next draft — spec's Known limitation section).
  - Reserved names: add `publish` to the `<!-- reserved-component-names -->`
    fenced list (the routed one — a route publishes it in this change, so it
    must not go in the unrouted list).
  - Deferred-items list: strike the publish-lifecycle entry pointing at #909.
  - Changelog: dated line.
- `docs/openapi-v1/README.zh-CN.md`: the same edits, mirrored.

---

## 9. Tests

New, under `tests/community/adapters/http/openapi_v1/publish/`:

| File | Pins |
|---|---|
| `test_publish_endpoints.py` | all four handlers, success + every mapped error; both writes answer 202 on success and nothing else |
| `test_release_state_machine.py` | start and promote attempted from **each** of the ten `PublishStatus` values — exactly one succeeds per write, the other nine are the fixed 409, with **no side effect** (no record minted, no status moved); plus both CAS-loser paths refused with the same 409 |
| `test_publish_access.py` | role matrix — owner / admin collaborator / member collaborator / stranger × writes and reads; every refusal byte-identical to a missing bot |
| `test_publish_projection.py` | the state map is exhaustive over `PublishStatus`; the withheld field set absent from the payload; no `ext` key reachable; the `failed` message carries no internal text |
| `test_publish_tenant_isolation.py` | a foreign tenant's bot and a foreign bot's version are both masked 404s, against the real Track A guard |

New, under `tests/community/core/service_bot/release/` (the Phase-1 core):

| File | Pins |
|---|---|
| `test_release_facts.py` | `from_ext`/`to_ext` round-trip property over real-shaped and legacy ext blobs — every key the model does not own preserved verbatim; passthrough keys untouched |
| `test_release_store.py` | the CAS/status+ext persistence semantics carried over from `PublishExtState`, in `ReleaseFacts` terms |
| `test_release_machine.py` | machine agreement — exactly two USER transitions; every non-terminal status covered; `on_failure` targets equal today's `source_status` writes; `process()` and `ReleaseLifecycleService` drive the same USER set |
| `test_release_operations.py` | ledger-first workflow-id reads with ext fallback for pre-#197 rows; the restart-vs-release classification needs no ext comparison |
| `test_lifecycle_service.py` | both preconditions row by row; the CAS-loser boolean path; `_load_release`'s `source_bot_pk` assert; the read/write bar constants |

New, under `tests/community/architecture/`:

- the no-raw-ext gate from plan §4 step 5, with its named grandfathered
  exceptions.

Amended:

- `test_path_convention.py` — reserved-name lists (doc ↔ routes) pick up `publish`
  automatically; the run must confirm it.
- `test_explicit_user_id.py` — the counts move from 56/4 to 60/4; update the
  expectation.
- `test_openapi_error_schema.py` — the new group must document exactly the
  `USER_SCOPED_ERROR_RESPONSES` set.
- `tests/community/architecture/` — the new cross-module imports must be declared
  in the touched modules' `README.md` `## Context Boundary` sections
  (`core/service_bot` ← `core/bot_collaborator`, `adapters/http/openapi_v1`
  ← the new protocol). This has failed CI twice before on exactly this;
  declare first, then run.

Unmodified and green, as the proof the Phase-1 refactor preserved behaviour:
`tests/community/adapters/http/service_bot/test_router_publish_coverage.py`,
`tests/community/core/service_bot/services/test_publish_flow_service.py`,
`test_publish_crash_windows.py`, `test_publish_tasks.py`,
`tests/community/e2e/publish_boundary/`, and the engine-runtime operator/stage
suites. One honest caveat: any existing test that *asserts the ext id stashes
are written* (`ext.publish`/`ext.restart`/`ext.scale`) is asserting the exact
duplication this change removes — such assertions are updated to assert the
ledger row instead, and each such edit is called out individually in the PR so
it reads as a contract change made on purpose, not a test bent to pass.

---

## 10. Rejected alternatives

Recorded so they are not reopened:

| Rejected | Why |
|---|---|
| Publish the operation ledger (a fifth, read-only endpoint) | The ledger is the pipeline's internal step structure — provider workflow ids, attempt bookkeeping, crash-recovery state. Publishing even a projection turns a crash-safety mechanism into a contract that then cannot be restructured (spec Decision 4) |
| Start-release resolves-or-creates the draft (mint on demand) | Folds `create_first_publish_for_bot` / `upgrade_publish` — each with its own preconditions — into one public call whose effect depends on state the caller cannot see. The precondition form keeps the operation's meaning fixed; the cost is the recorded known limitation (spec Decision 1) |
| Return the release's state with a 200 when a write's precondition fails | Four flavours of "no" (too early, too late, repeat, race-loser) would advertise timing detail a caller cannot act on differently; one fixed 409 plus the read endpoint answers all four (spec Decision 2) |
| Detect the CAS winner by parsing `process()`'s result (message comparison, or an `advanced: bool` field) | The message form is a string tripwire; the field form changes the internal wire payload (`ApiResponse.data` serializes the whole result). Driving the machine transition directly through `ReleaseStore.advance` makes the outcome a boolean at the source (design §3.5) |
| Wrap the current pipeline as-is and defer the core evolution entirely | The review asked for the evolution, and the public surface would otherwise be a fourth consumer of the untyped ext maze — every new consumer makes the eventual cleanup strictly harder (design §1) |
| Big-bang rewrite of the whole pipeline in this change | ~8k lines carrying production crash-safety semantics (#197) and three fix-specs; the strangler phasing in design §4 keeps every step provable against the existing suites |
| Address a release by its record id | Global auto-increment; leaks cross-tenant volume, and its meaning is internal (spec Decision 8) |
| Collapse the ten statuses into three or four coarse phases | The whole point of the read side is following progress; `built` vs `publishing_verify` is exactly the distinction a stalled publish turns on |
| Publish `ext.error_message` on a failed release | Raw internal text — exception reprs, provider ids, internal-language strings; the surface's fixed-message rule exists for this |
| One role bar for the whole category | Either hides progress from members who can already watch the runtime, or lets them ship (spec Decision 3) |
| A `stage` query parameter, matching the engine-runtime groups | A release *is* the stage transition; there is nothing left for a `stage` to address |
| Add `avernet_tenant` to the publish tables | Not this change's job; isolation already derives from the tenant-guarded bot resolve plus primary-key keying, the same argument PR #904 made (spec Decision 7) |

---

## 11. Risk and rollback

- **No schema change, no DDL, no migration.** Rollback is reverting the code.
  The stored JSON keys are round-trip compatible, so rows written by the new
  code are readable by the old — except the stopped id-stash writes, whose
  rolling-deploy story is design §4: the ledger has carried those ids
  authoritatively since #197, and both old and new readers consult it.
- **The internal HTTP surface's contract is untouched.** The internal-file
  edits are the Phase-1 refactor (§4) — `process()` consulting the machine,
  the ext accessors migrating to `ReleaseStore`, the id-stash writes stopping
  — all behaviour-preserving by the suites named in §9. A revert cannot strand
  a release: every record this surface advances is an ordinary record the
  internal surface already drives.
- **The largest real risk is the Phase-1 read migration** (the ~11 call sites
  in §4 step 4). It is sequenced first, lands with the round-trip and
  ledger-fallback tests before the public surface is wired on top, and each
  call-site conversion is a small, individually-revertable commit.
- **The one shared-code move is the role-bar extraction (§3).** It is
  behaviour-preserving and its proof is the engine-runtime suites staying
  unmodified and green. If review prefers not to move it, the fallback is a
  `min_level` parameter on `require_bot_operator` with the current bar as the
  default — smaller, but leaves `publishing` importing `engine_runtime`.
- **The surface still answers 401 in every environment without a gateway
  principal signing key**, exactly like the other categories. Like them, this
  category's end-to-end verification is blocked on the same event, and the tests
  above exercise the handlers directly.
