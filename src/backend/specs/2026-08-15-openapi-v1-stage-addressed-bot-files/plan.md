# Plan: Public API — Stage-Addressed Per-Bot Files

## Approach

The machinery exists; nothing here is new policy. `core/engine_runtime/stage.py`
already knows which runtime a stage names, `RuntimeStage` and `StageQuery`
already publish the parameter, and `EngineConnectionService._stage_binding_id`
is the worked example. Three things are missing and this plan adds exactly
those:

1. A core helper that goes one step further than `resolve_stage_bind_id` —
   stage → **`DeviceContext`** — because a file surface needs to address a
   filesystem, not forward a request. `EngineConfigService` and
   `IdentityService` both call it, so the two cannot drift from each other or
   from the relay.
2. A core refusal for a write addressed to a published runtime, mapped once.
3. The parameter on five handlers.

The one design constraint that shapes everything below: **a request that names
no stage must execute the code it executes today**. So the draft branch is
`resolve_for_bot(bot_id, owner_id)` exactly as now — no extra query, no new
lookup, no new error — and the published branch is the only new path.

## The rules

- **Reads** carry a `StageAddress` — stage plus the `(bot_pk, bot_type)` from
  the read that proved the caller's ownership. Published stages need both; the
  draft reads neither.
- **Writes** carry only a stage name. `require_stage_writable` refuses anything
  but the draft as the first statement of the method, then the draft is
  resolved. There is no code path from a write to a published binding.
- **Stage-keyed → `resolve_stage_bind_id`. Record-keyed →
  `select_stage_bind_id`.** Stated in both modules' docstrings and at all four
  call sites (spec D3).

## Affected Components

| Component | Change |
| --- | --- |
| `core/engine_runtime/errors.py` | new `EngineStageReadOnlyError` |
| `core/engine_runtime/stage.py` | `StageAddress`, `DRAFT_ADDRESS`, `require_stage_writable`, `resolve_stage_device_context`; docstring records the second rule |
| `core/services/engine_config.py` | two new constructor deps; read takes `address`, write takes `stage` |
| `core/services/identity.py` | one new constructor dep; `address` threaded down the read path; `stage` on the write |
| `api/engine_config_service.py` | Protocol mirrors both signatures |
| `adapters/http/openapi_v1/engine_runtime/params.py` | `WRITE_STAGE_DESCRIPTION`, `WriteStageQuery` |
| `adapters/http/openapi_v1/responses.py` | `EngineStageReadOnlyError → (409, …)` |
| `adapters/http/openapi_v1/bots/router.py` | `stage` on the two engine-config handlers |
| `adapters/http/openapi_v1/identity/router.py` | `stage` on the three handlers; `BotRepository` injected for the published branch |
| `adapters/http/bot_management/router.py` | two internal call sites say `draft` explicitly |
| `docs/openapi-v1/README.md` | the stage section covers 21 operations, not 16 |

## Data Model Changes

None. No table, column, or DDL. The runtimes and their bindings already exist;
this addresses them.

## API / Interface Changes

### The core seam

```python
# core/engine_runtime/stage.py

@dataclass(frozen=True)
class StageAddress:
    stage: str
    bot_pk: int      # from the ownership-proving read; unread on draft
    bot_type: str    # decides whether a published stage is refused

DRAFT_ADDRESS = StageAddress(stage=STAGE_DRAFT, bot_pk=0, bot_type="")

def require_stage_writable(stage: str) -> None:
    """Refuse a write to a published runtime. Not conditional on bot type or
    liveness — neither changes the answer, and both would need a lookup."""

def resolve_stage_device_context(
    resolver, publish_repo, binding_repo, *, address, bot_id, owner_id
) -> DeviceContext:
    require_stage_addressable(address.bot_type, address.stage)
    if address.stage == STAGE_DRAFT:
        return resolver.resolve_for_bot(bot_id, owner_id)          # unchanged path
    bind_id = resolve_stage_bind_id(publish_repo, binding_repo,
                                    bot_pk=address.bot_pk, bot_id=bot_id,
                                    stage=address.stage, env=get_current_env())
    return resolver.resolve_for_binding(bind_id, owner_id, bot_id=bot_id)
```

`resolve_for_binding`, not the relay's `resolve_for_binding_invoke`: callers
here address a **filesystem** on the resolved device and need the full
connection info the invoke variant deliberately omits.

`StageAddress` is one value rather than three parameters because identity
threads it four levels deep (`get_bot_file` → `read_identity_file` →
`_device_read` → `_identity_device_fs`); three parameters at each hop is three
chances to drop one.

### The service signatures

```python
# core/services/engine_config.py  (+ api/engine_config_service.py, identically)
async def read_bot_config(self, *, bot_id, owner_id, entity_id, entity_type,
                          engine_type, address: StageAddress) -> dict: ...
async def write_bot_config(self, *, bot_id, owner_id, entity_id, entity_type,
                           engine_type, config, stage: str) -> None: ...
```

**Required, not defaulted, on this pair only.** The conformance test
(`test_service_api_conformance.py`) compares defaults *by value*, so a default
would force `api/` to import `core.engine_runtime` at runtime — and that
package's import graph reaches the DI container and a partially-initialised
`bot_service`. Verified: it raises `ImportError` when `api/engine_config_service`
is imported first. Both adapters therefore state the runtime they address, which
reads better regardless.

`IdentityService` has no Protocol and many internal callers
(`bot_profile`, `sync_agents_md`, the legacy `/api/identity` router), so there
the parameters are **defaulted** to `DRAFT_ADDRESS` / `STAGE_DRAFT` and every
existing caller is untouched.

### The five handlers

```python
# bots/router.py — GET keeps the record it already fetched
async def get_bot_engine_config(bot_id, request, owner_id,
                                stage: StageQuery = RuntimeStage.DRAFT, ...):
    bot = bot_service.get_bot(bot_id, owner_id)        # ownership/tenant guard
    ...
    address=_stage_address(bot, stage)                 # pk + type from that record

# bots/router.py — PUT takes the parameter to refuse it
async def update_bot_engine_config(..., stage: WriteStageQuery = RuntimeStage.DRAFT, ...):
    ...
    await engine_config_service.write_bot_config(..., stage=stage.value)
```

The identity router today resolves no bot at all — the owner-scoping is the
`(bot_id, owner_id)` binding query inside `resolve_for_bot`. Adding an
unconditional lookup would change the draft path's failure mode (today a bot
that is not the caller's fails as `409 Bot has no active device`; a lookup would
make it `404`). So the lookup happens **only when a published stage is named**:

```python
# identity/router.py
def _stage_address(bot_id, owner_id, stage, bot_repo) -> StageAddress:
    if stage is RuntimeStage.DRAFT:
        return DRAFT_ADDRESS                            # no query, no change
    bot = bot_repo.get_by_id_and_owner(bot_id, owner_id)
    if not bot:
        raise BotNotFoundError(f"Bot not found: {bot_id}")
    return StageAddress(stage=stage.value, bot_pk=int(bot.get("id") or 0),
                        bot_type=str(bot.get("bot_type") or ""))
```

`BotRepository` injected directly in the adapter follows the resources router's
precedent, and avoids `bot_service.get_bot`, which attaches `device_binding` —
device topology this surface exists to stop publishing.

The identity **PUT** adds no lookup at all: it passes `stage=stage.value` and
the service refuses before anything is resolved.

## Key Files & Functions

```text
core/engine_runtime/errors.py
    + EngineStageReadOnlyError            # sibling of EngineStageNotLiveError

core/engine_runtime/stage.py
    + StageAddress, DRAFT_ADDRESS
    + require_stage_writable
    + resolve_stage_device_context
    ~ module docstring: names select_stage_bind_id as the *other* question

core/services/engine_config.py
    ~ __init__(+ publish_repo, + binding_repo)
    ~ _bot_config_device_fs(+ address)  → resolve_stage_device_context
    ~ read_bot_config(+ address) / write_bot_config(+ stage)
    ~ read_publish_config: comment on why it stays record-keyed

core/services/identity.py
    ~ __init__(+ binding_repo)
    ~ _identity_device_fs / _device_read / read_identity_file (+ address=DRAFT_ADDRESS)
    ~ get_bot_file / list_bot_files (+ address), update_bot_file (+ stage)
    ~ _read_from_publish_device: comment on why it stays record-keyed

adapters/http/openapi_v1/responses.py
    + EngineStageReadOnlyError: (409, "The requested stage is read-only")
```

## Dependencies

- **PR #1073** (`fix(backend): read an unwritten engine config as empty, not
  500`) is open against `dev` and touches
  `core/services/engine_config.py` — the same file Task B1 edits, in the same
  two read methods.

  This branch is based on **`dev`**, not on #1073, so the two changes are
  independent until B1 lands. Whichever merges second resolves the overlap; if
  #1073 is still open when implementation starts, rebase onto its head first so
  B1 is written against the `_read_config_bytes` helper it introduces rather
  than against the raw `device_fs.read_file` call it replaces. The two edits are
  compatible in substance — #1073 changes *how a missing file is decoded*, B1
  changes *which device is read* — so this is a textual conflict, not a design
  one.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| The draft path changes behaviour by accident | The draft branch is literally `resolve_for_bot(bot_id, owner_id)`; a test asserts the resolver call is identical with and without `?stage=draft`, and that no bot lookup is made on the identity draft path |
| Two new constructor deps break direct service construction in tests | Nine call sites, enumerated in the task list; `injector` supplies them in prod (`DeviceBindingRepository` and `BotPublishRepositoryProtocol` are both already bound) |
| `test_stage_addressing.py` asserts `stage` is on *exactly* sixteen operations | The document half gains a `_STAGE_ADDRESSED_ELSEWHERE` set, mirroring the existing `_OWNER_ADDRESSED_ELSEWHERE` — so the assertion stays exact rather than being loosened |
| A future reader re-merges the two 409s | Both messages, both error docstrings and the `ENVELOPE_ERRORS` comment state the distinction |
| The write refusal drifts into "write the draft instead" | The write path never receives a `StageAddress`, so there is nothing to resolve a published binding *with* |

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
- **Give writes a `StageAddress` for symmetry.** Rejected: it would carry facts
  the write must never use, and weaken D4 from structural to conventional.

## Rollout

Additive and backward compatible. Every existing request omits `stage` and is
unchanged. No DDL, no migration, no config. Rollback is the revert.

## Test Strategy

```text
tests/community/core/engine_runtime/test_stage.py            (extend)
    require_stage_writable: draft passes; verify/online raise
    resolve_stage_device_context: draft → resolve_for_bot with (bot_id, owner_id)
                                  and NOT resolve_for_binding; published →
                                  resolve_for_binding with the resolved bind_id;
                                  personal + published → EngineStageNotLiveError
                                  before any resolver call

tests/community/core/services/test_engine_config_service.py  (extend)
    read at verify/online resolves through the publish record's binding
    write at verify/online raises EngineStageReadOnlyError and the dispatcher
      is never touched (assert_not_called — "nothing is written")

new: tests/community/core/services/test_identity_stage_addressing.py
    the same two pins for identity read/list/write

new: tests/…/openapi_v1/test_stage_addressed_bot_files.py
    GET engine-config / identity with each stage → the address the service saw
    default (no parameter) → DRAFT_ADDRESS, and no bot lookup on identity
    PUT with stage=verify|online → 409 "The requested stage is read-only"
    PUT with no stage → unchanged 200
    stage=eval → 422 from the enum, no handler run

tests/…/openapi_v1/engine_runtime/test_stage_addressing.py   (extend)
    the document half: stage now on 16 + 5, still optional everywhere,
    still never a body field or path segment
```
