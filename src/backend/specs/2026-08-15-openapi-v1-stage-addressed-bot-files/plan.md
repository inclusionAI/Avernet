# Plan: Public API — Stage-Addressed Per-Bot Files

## Approach

The machinery exists; nothing here is new policy. `core/engine_runtime/stage.py`
already knows which runtime a stage names, `RuntimeStage` and `StageQuery`
already publish the parameter, and `EngineConnectionService._stage_binding_id`
is the worked example. Three things are missing and this plan adds exactly
those:

1. A core helper that goes one step further than `resolve_stage_bind_id` — a
   published stage → **`DeviceContext`** — because a file surface needs to
   address a filesystem, not forward a request. `EngineConfigService` and
   `IdentityService` both call it, so the two cannot drift from each other or
   from the relay.
2. A core refusal for a write addressed to a published runtime, mapped once.
3. The parameter on five handlers.

The one design constraint that shapes everything below: **a request that names
no stage must execute the code it executes today**. So the draft branch is
`resolve_for_bot(bot_id, owner_id)` exactly as now — no extra query, no new
lookup, no new error — and the published branch is the only new path.

## The rules

- **One bot-identity model.** `BotFacts` with `stage: str` beside it — the shape
  the relay and the Service API Protocol already use (spec D8). No new value
  object.
- **Reads** take `stage: str`. The draft branch is the existing
  `resolve_for_bot(bot_id, owner_id)` call, unchanged. The published branch
  resolves `BotFacts` from an owner-scoped read *inside the service*, then goes
  through the shared rule.
- **Writes** take `stage: str` too. `require_stage_writable` refuses anything
  but the draft as the first statement of the method, then the draft is
  resolved. A write never reaches the facts-resolving branch at all.
- **Stage-keyed → `resolve_stage_bind_id`. Record-keyed →
  `select_stage_bind_id`.** Stated in both modules' docstrings and at all four
  call sites (spec D3).

## Affected Components

| Component | Change |
| --- | --- |
| `core/engine_runtime/errors.py` | new `EngineStageReadOnlyError` |
| `core/engine_runtime/models.py` | `BotFacts.from_record` — one projection of a bot row into facts |
| `core/engine_runtime/relay.py` | its one `BotFacts(...)` construction moves onto `from_record` |
| `core/engine_runtime/stage.py` | `require_stage_writable`, `resolve_published_device_context`; docstring records the second rule |
| `core/services/engine_config.py` | two new constructor deps; `_bot_facts`; read and write each take `stage: str` |
| `core/services/identity.py` | one new constructor dep; `_bot_facts`; `stage: str` threaded down the read path and on the write |
| `api/engine_config_service.py` | Protocol mirrors both signatures |
| `adapters/http/openapi_v1/engine_runtime/params.py` | `WRITE_STAGE_DESCRIPTION`, `WriteStageQuery` |
| `adapters/http/openapi_v1/responses.py` | `EngineStageReadOnlyError → (409, …)` |
| the engine-config router #1074 Task 15 creates at `/openapi/v1/bots/{bot_id}/engine` | `stage` on its two handlers |
| `adapters/http/openapi_v1/identity/router.py` | `stage` on the three handlers — nothing else |
| `adapters/http/bot_management/router.py` | two internal call sites say `draft` explicitly |
| `docs/openapi-v1/README.md` | the stage section covers 21 operations, not 16 |

## Data Model Changes

None. No table, column, or DDL. The runtimes and their bindings already exist;
this addresses them.

## API / Interface Changes

### The core seam

```python
# core/engine_runtime/models.py — one projection of a bot row into facts
@classmethod
def from_record(cls, record: dict, *, bot_id: str, owner_id: str) -> "BotFacts":
    """The narrow facts, from an owner-scoped ``(bot_id, owner_id)`` row."""

# core/engine_runtime/stage.py
def require_stage_writable(stage: str) -> None:
    """Refuse a write to a published runtime. Not conditional on bot type or
    liveness — neither changes the answer, and both would need a lookup."""

def resolve_published_device_context(
    resolver, publish_repo, binding_repo, *, facts: BotFacts, stage: str
) -> DeviceContext:
    """The device context of the published runtime ``stage`` names."""
    require_stage_addressable(facts.bot_type, stage)
    bind_id = resolve_stage_bind_id(publish_repo, binding_repo,
                                    bot_pk=facts.bot_pk, bot_id=facts.bot_id,
                                    stage=stage, env=get_current_env())
    return resolver.resolve_for_binding(bind_id, facts.owner_id, bot_id=facts.bot_id)
```

**Published-only, and the branch stays at the caller** — which is exactly
`relay._resolve_device`'s structure, where the draft leg is an inline
`resolve_for_bot` and the published leg is a separate method. Each service reads:

```python
if stage == STAGE_DRAFT:
    ctx = self._resolver.resolve_for_bot(bot_id, owner_id)   # unchanged; no row read
else:
    facts = self._bot_facts(bot_id, owner_id)                # owner-scoped
    ctx = resolve_published_device_context(
        self._resolver, self._publish_repo, self._binding_repo,
        facts=facts, stage=stage,
    )
```

Two notes on why it is shaped this way rather than as one function covering both
legs: a helper that handled the draft would need `BotFacts` for the draft, which
is the row read the compatibility requirement forbids; and
`require_stage_addressable` needs `bot_type`, which only the published branch has
resolved — so a personal bot naming `verify` costs one row read and is then
refused, before any device work.

`resolve_for_binding`, not the relay's `resolve_for_binding_invoke`: callers here
address a **filesystem** on the resolved device and need the full connection info
the invoke variant deliberately omits. That single differing line is why this is
a sibling of `relay._resolve_published_device` rather than a shared body; what
they *do* share is `resolve_stage_bind_id`, which is the rule that must not
drift.

`BotFacts.from_record` is the one touch outside the feature: the relay's single
construction site moves onto it, so there is one projection of a bot row into
facts rather than two. Reversible in isolation if a reviewer disagrees.

### The service signatures

```python
# core/services/engine_config.py  (+ api/engine_config_service.py, identically)
async def read_bot_config(self, *, bot_id, owner_id, entity_id, entity_type,
                          engine_type, stage: str) -> dict: ...
async def write_bot_config(self, *, bot_id, owner_id, entity_id, entity_type,
                           engine_type, config, stage: str) -> None: ...
```

One added parameter, the same `str` the relay carries.

**Required, not defaulted, on this pair** — matching
`EngineRuntimeRelayProtocol`, which documents `stage` as "required with no
default, so the stage a handler gated on and the stage it forwards to cannot
silently diverge." A default would also have to be the same object on both sides
(`test_service_api_conformance.py` compares defaults by value), which means
`api/` importing `core.engine_runtime` at runtime — verified to raise
`ImportError`, because that package's import graph reaches the DI container and
a partially-initialised `bot_service`. Convention and mechanics agree.

`IdentityService` has no Protocol and many internal callers (`bot_profile`,
`sync_agents_md`, the legacy `/api/identity` router), so there `stage` is
**defaulted** to `STAGE_DRAFT` and every existing caller is untouched. That is a
deliberate departure from the required-no-default convention, taken because the
alternative is editing ten call sites to say what they already mean.

### The five handlers

The two engine-config handlers are edited **wherever #1074's Task 15 leaves
them** — its own router mounted at `/openapi/v1/bots/{bot_id}/engine`, serving
`GET`/`PUT …/config`. Task 15 moves the handler bodies unchanged, so
`_engine_config_target` travels with them;
if Task 15 has not landed, the same two handlers are still in
`openapi_v1/bots/router.py` at `/{bot_id}/engine-config` and the edit is
identical apart from the file.

Every one of the five is the same two-line edit — declare the parameter, pass
its value. No adapter helper, no repository, no branching:

```python
# the engine-config router
async def get_bot_engine_config(bot_id, request, owner_id,
                                stage: StageQuery = RuntimeStage.DRAFT, ...):
    ...
    await engine_config_service.read_bot_config(..., stage=stage.value)

async def update_bot_engine_config(..., stage: WriteStageQuery = RuntimeStage.DRAFT, ...):
    ...
    await engine_config_service.write_bot_config(..., stage=stage.value)
```

The identity router in particular needs **no** change beyond that. It resolves no
bot today — the owner-scoping is the `(bot_id, owner_id)` binding query inside
`resolve_for_bot` — and it still resolves none. An earlier draft had it inject
`BotRepository` and look the bot up when a published stage was named, to avoid
changing the draft path's failure mode (today a bot that is not the caller's
fails as `409 Bot has no active device`; an unconditional lookup would make it
`404`). Moving the facts resolution into the service removes the need for that
conditional entirely: the draft path never reaches it.

The service-side helper both services gain:

```python
def _bot_facts(self, bot_id: str, owner_id: str) -> BotFacts:
    """The addressed bot's facts, from an owner-scoped read.

    ``get_by_id_and_owner`` and not ``get_by_id``: ``bot_id`` is not unique
    across owners, so a wider query can name another owner's row (spec D6).
    """
    record = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
    if not record:
        raise BotNotFoundError(f"Bot not found: {bot_id}")
    return BotFacts.from_record(record, bot_id=bot_id, owner_id=owner_id)
```

Both services already hold `BotRepository` (`self._bot_repo`), so this adds no
constructor dependency.

## Key Files & Functions

```text
core/engine_runtime/errors.py
    + EngineStageReadOnlyError            # sibling of EngineStageNotLiveError

core/engine_runtime/models.py
    + BotFacts.from_record(record, *, bot_id, owner_id)

core/engine_runtime/relay.py
    ~ resolve_bot: its BotFacts(...) construction → BotFacts.from_record
                   (the one touch outside the feature; one projection, not two)

core/engine_runtime/stage.py
    + require_stage_writable
    + resolve_published_device_context
    ~ module docstring: names select_stage_bind_id as the *other* question

core/services/engine_config.py
    ~ __init__(+ publish_repo, + binding_repo)
    + _bot_facts(bot_id, owner_id)
    ~ _bot_config_device_fs(+ stage)  → draft inline / published via the helper
    ~ read_bot_config(+ stage) / write_bot_config(+ stage)
    ~ read_publish_config: comment on why it stays record-keyed

core/services/identity.py
    ~ __init__(+ binding_repo)
    + _bot_facts(bot_id, owner_id)
    ~ _identity_device_fs / _device_read / read_identity_file (+ stage=STAGE_DRAFT)
    ~ get_bot_file / list_bot_files / update_bot_file (+ stage=STAGE_DRAFT)
    ~ _read_from_publish_device: comment on why it stays record-keyed

adapters/http/openapi_v1/responses.py
    + EngineStageReadOnlyError: (409, "The requested stage is read-only")
```

## Dependencies

- **PR #1074** (`docs(openapi-v1): specify bot-first addressing…`) — a **hard
  ordering dependency**, confirmed with the owner: it lands first and this is
  written against the surface it leaves.

  What it gives us: identity already sits at `/openapi/v1/bots/{bot_id}/identity`
  (its Task 4, done), and its Task 15 moves engine-config to
  `/openapi/v1/bots/{bot_id}/engine/config` onto its own router. What it
  constrains: the deprecated addresses it registers are frozen byte for byte and
  must **not** gain `stage` (spec D7).

  It also rewrites the suite Task D4 extends: `test_stage_addressing.py`'s
  prefix test becomes `_is_engine_runtime()`, backed by an
  `_engine_runtime_paths()` set read off the mounted routers (current groups
  plus the deprecated package's). Task D4 is written against that shape, not
  today's. Its `_OWNER_ADDRESSED_ELSEWHERE` also gains the two skills
  operations, now that they spell the locator `owner_id`.

- **PR #1073** (`fix(backend): read an unwritten engine config as empty, not
  500`) — **merged to `dev` as `a5afd54`.** No longer a conflict to manage:
  Task B1 edits `read_bot_config`/`read_publish_config` as #1073 left them, so
  the read goes through its `_read_config_bytes` helper rather than a raw
  `device_fs.read_file`. #1074 carried the same merge and reports one conflict
  on the relocated handler, resolved in its favour.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| The surface audit missed a group | Re-run over all 31 `resolve_for_bot` call sites, not a truncated listing — that is how MCP was found. The groups reachable from `/openapi/v1` are engine-config, identity, resources, skills, MCP, routines and the engine-runtime relay; the rest are internal-only surfaces |
| The draft path changes behaviour by accident | The draft branch is literally `resolve_for_bot(bot_id, owner_id)`; a test asserts the resolver call is identical with and without `?stage=draft`, and that no bot lookup is made on the identity draft path |
| Two new constructor deps break direct service construction in tests | Nine call sites, enumerated in the task list; `injector` supplies them in prod (`DeviceBindingRepository` and `BotPublishRepositoryProtocol` are both already bound) |
| `test_stage_addressing.py` asserts `stage` is on *exactly* sixteen operations | The document half gains a `_STAGE_ADDRESSED_ELSEWHERE` set, mirroring the existing `_OWNER_ADDRESSED_ELSEWHERE` — so the assertion stays exact rather than being loosened |
| #1074's `_is_engine_runtime()` matches the moved `…/{bot_id}/engine/config` and asserts `owner_id` on it, which engine-config does not carry | Not ours to fix and not ours to work around — it breaks #1074's Task 15 before this feature exists (spec **Open Questions**). Task D4 starts by confirming which shape #1074 settled on, and adds the five to `_STAGE_ADDRESSED_ELSEWHERE` on top of it |
| `stage` leaks onto a deprecated address | The deprecated package re-registers old addresses against their own shim handlers; this feature edits only the new-address handlers. Task D4's exact-set assertion is what catches a leak |
| A future reader re-merges the two 409s | Both messages, both error docstrings and the `ENVELOPE_ERRORS` comment state the distinction |
| The write refusal drifts into "write the draft instead" | The write path never resolves `BotFacts`, so there is nothing to resolve a published binding *with* |
| The in-service facts read duplicates one the adapter already did | Accepted and recorded (spec D8). It is one owner-scoped row read, on published-stage requests only; the draft path — every request that names no stage — reads nothing extra. Task D3 pins that |

## Alternatives Considered

- **Collapse `read_publish_config` onto `resolve_stage_bind_id`.** Rejected: it
  is keyed by `publish_id` and that rule ignores the record named. Spec D3.
- **`200` with `{"applied": false}` for a published-stage write.** Rejected in
  the requirement and here: a caller checking status codes reads it as success.
- **Refuse published-stage writes at the adapter.** Rejected: it is domain
  policy, and the internal routes would not inherit it.
- **A separate `stage` enum or parameter for writes.** Rejected: same values,
  same vocabulary; only the description differs.
- **Resolve the bot unconditionally in the identity router.** Rejected: it
  changes the draft path's failure mode (409 → 404), which the "byte-for-byte"
  requirement forbids. It would be an improvement to make deliberately, on its
  own.
- **A `StageAddress` value object** carrying `(stage, bot_pk, bot_type)`.
  Rejected on review: those are a subset of `BotFacts` fused with the stage, so
  it would stand a second bot-identity model beside the one the relay and the
  Service API Protocol already use (spec D8).
- **Threading `BotFacts` down from the adapters** instead of resolving it in the
  service. Rejected with D8: it avoids one row read on the published path, at
  the cost of an optional at every service boundary (`None` meaning draft) and a
  conditional bot lookup in the identity router.

## Rollout

Additive and backward compatible. Every existing request omits `stage` and is
unchanged. No DDL, no migration, no config. Rollback is the revert.

## Test Strategy

```text
tests/community/core/engine_runtime/test_stage.py            (extend)
    require_stage_writable: draft passes; verify/online raise
    resolve_published_device_context: resolve_for_binding with the resolved
                                  bind_id, facts.owner_id and facts.bot_id;
                                  personal + published → EngineStageNotLiveError
                                  before any resolver call
    BotFacts.from_record: same fallbacks the relay applied inline

tests/community/core/services/test_engine_config_service.py  (extend)
    read at verify/online resolves through the publish record's binding
    write at verify/online raises EngineStageReadOnlyError and the dispatcher
      is never touched (assert_not_called — "nothing is written")

new: tests/community/core/services/test_identity_stage_addressing.py
    the same two pins for identity read/list/write

new: tests/…/openapi_v1/test_stage_addressed_bot_files.py
    GET …/{bot_id}/engine/config and …/{bot_id}/identity[/{file_type}] with
      each stage → the address the service saw
    default (no parameter) → the draft resolve, and no bot row read at all
    PUT with stage=verify|online → 409 "The requested stage is read-only"
    PUT with no stage → unchanged 200
    stage=eval → 422 from the enum, no handler run
    the deprecated twins (…/bots/identity/{bot_id}, …/{bot_id}/engine-config)
      do not declare `stage` in the document, and a request that sends it
      anyway still reads the draft — FastAPI ignores an undeclared query
      parameter, which is the freeze behaving as #1074 specified it

tests/…/openapi_v1/engine_runtime/test_stage_addressing.py   (extend)
    the document half: stage now on 16 + 5, still optional everywhere,
    still never a body field or path segment, and on no deprecated address
```
