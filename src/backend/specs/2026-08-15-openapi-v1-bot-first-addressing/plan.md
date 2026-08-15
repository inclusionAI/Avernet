# Plan — `/openapi/v1/bots` Bot-First Addressing

## Shape of the change

Three moving parts, in dependency order.

**1. The new addresses are the existing routers with different prefixes.** No
handler is rewritten. For the nineteen swap-places operations `bot_id` is a path
parameter both before and after — only the router prefix and the route path move.
For the twenty query-carried ones the handler's `bot_id` parameter changes from
`Query(...)` to `BotIdPath` and the router prefix gains `{bot_id}`; the body
stays untouched except on `POST …/routines`, which drops one field.

**2. The legacy addresses become a package that owns the old contract.** Not
aliases — a legacy operation publishes the old parameter names in the old
locations and translates. It divides in two:

- The nineteen swap-places operations need **no shim code at all**. `bot_id` is a
  path parameter in both shapes, so the same endpoint function is registered a
  second time under the old path — one line per route.
- The twenty query-carried ones need a real shim: a function with the old
  signature that calls the new handler. Roughly ten lines each.

**3. The authorization seam collapses to one mechanism.** Once every new address
carries its bot on the path and spells its owner `owner_id`, nothing on the new
surface defers its grant check to its handler. The deferral machinery does not
disappear — the legacy addresses still need it — it **moves into the deprecated
package**, where it is deleted along with the package. `principal.py` and
`admission.py` come out clean.

## Files

### New

| File | What |
| --- | --- |
| `adapters/http/openapi_v1/deprecated/__init__.py` | `build_deprecated_router()`; exports `LEGACY_ROUTES` (the `(method, path)` set) so nothing else has to restate it |
| `adapters/http/openapi_v1/deprecated/_shim.py` | The `deprecated=True` route defaults, and the private grant-check helpers moved out of `principal.py` / `admission.py` |
| `adapters/http/openapi_v1/deprecated/engine_runtime.py` | 19 one-line re-registrations: approvals, connection, engine, identity, models, sessions |
| `adapters/http/openapi_v1/deprecated/resources.py` | 7 shims |
| `adapters/http/openapi_v1/deprecated/routines.py` | 7 shims, including the body-carried `bot_id` on create |
| `adapters/http/openapi_v1/deprecated/skills.py` | 6 shims, keeping `owner_entity_id` and `…/skills/upload` |
| `adapters/http/openapi_v1/deprecated/bots.py` | 2 shims for the old `engine-config` address |
| `adapters/http/openapi_v1/deprecation.py` | Middleware stamping `Deprecation` / `Sunset` on responses whose matched route is in `LEGACY_ROUTES` |
| `tests/…/openapi_v1/test_legacy_parity.py` | Drives every legacy/new pair and compares status, body and envelope |
| `tests/…/openapi_v1/test_deprecation_headers.py` | Every legacy operation is `deprecated: true` and answers with both headers; no new operation is |
| `tests/…/openapi_v1/test_no_duplicate_request_fields.py` | The invariant that started this: no field name in two locations on one operation, across the whole published surface |

### Changed

| File | Change |
| --- | --- |
| `openapi_v1/identity/router.py` | prefix → `/openapi/v1/bots/{bot_id}/identity`; routes `""` and `/{file_type}` |
| `openapi_v1/engine_runtime/approvals/router.py` | prefix → `…/{bot_id}/approvals`; routes `/mode`, `/modes`; `session_key` moves from the PUT body to a query parameter |
| `openapi_v1/engine_runtime/connection/router.py` | prefix → `…/{bot_id}/connection`; route `""` |
| `openapi_v1/engine_runtime/engine/router.py` | prefix → `…/{bot_id}/engine`; routes `/available`, `/capabilities`, `/status` |
| `openapi_v1/engine_runtime/models/router.py` | prefix → `…/{bot_id}/models`; routes `""`, `/{model_id}` |
| `openapi_v1/engine_runtime/sessions/router.py` | prefix → `…/{bot_id}/sessions`; routes `""`, `/{session_id}`, `/{session_id}/messages` |
| `openapi_v1/resources/router.py` | prefix → `…/{bot_id}/resources`; `bot_id` becomes `BotIdPath` on all seven |
| `openapi_v1/routines/router.py` | prefix → `…/{bot_id}/routines`; `bot_id` becomes `BotIdPath`; `RoutineCreate.bot_id` removed; the inline `caller.require_bot` in create removed |
| `openapi_v1/skills/router.py` | prefix → `…/{bot_id}/skills`; `bot_id` becomes `BotIdPath`; upload becomes `POST ""`; `owner_entity_id` → `owner_id`; the two inline `caller.require_bot` calls removed |
| `openapi_v1/bots/router.py` | `engine-config` routes move to `/{bot_id}/engine/config` on their own router |
| `openapi_v1/routines/schemas.py` | `RoutineCreate` drops `bot_id` |
| `openapi_v1/engine_runtime/approvals/schemas.py` | the mode-write body drops `session_key` |
| `openapi_v1/admission.py` | entries for 39 new addresses; `BODY_BOT_ID_OPERATIONS`, `SKILL_SCOPED_OPERATIONS`, `OWNER_ADDRESSED_OPERATIONS` deleted; the two skills reads move to `GRANT_CHECKED_ADDRESSED_BOT` |
| `openapi_v1/principal.py` | `_defers_to_its_handler` deleted, `TODO(#960)` resolved; the refuse-when-no-bot branch kept |
| `openapi_v1/__init__.py` | mount the deprecated router; rewrite the mount-order and addressing docstrings |
| `adapters/http/app.py` | register the deprecation middleware |
| `tests/…/openapi_v1/test_path_convention.py` | the new rule and both name lists |
| `tests/…/openapi_v1/test_admission_inventory.py`, `test_principal_seam.py`, `test_explicit_user_id.py`, and the per-group endpoint tests | new addresses, new counts |
| `docs/openapi-v1/README.md` + `.zh-CN.md` | addressing rule, both fenced name lists, ~88 path references |
| `docs/openapi-v1/engine-surface.md` + `.zh-CN.md` | 16 path references |
| `src/gateway/configs/schemas/bots.openapi.json` | regenerated |

## Approach notes

### The router prefix carries `{bot_id}`

Verified against the installed FastAPI: `APIRouter(prefix="/openapi/v1/bots/{bot_id}/sessions")` binds `bot_id` for every route on the router and publishes it as a path parameter on each. Handlers keep their existing `bot_id: BotIdPath` parameter and need no edit; the route path loses its `/{bot_id}` segment. So for the six already-bot-scoped groups the diff is the prefix line plus each `@router.<verb>` path string.

### Legacy re-registration for the nineteen

```python
legacy = APIRouter(prefix="/openapi/v1/bots/approvals", tags=["approvals (deprecated)"])
legacy.get("/{bot_id}/mode", response_model=Envelope[ApprovalState], deprecated=True)(get_approval_mode)
```

The handler is already decorated with `@envelope_errors`, so the second
registration inherits it. Response models and `responses=` tables are restated
on the registration; `_shim.py` holds a helper so the deprecated flag and the
tag suffix cannot be forgotten.

Two of the nineteen are not pure re-registrations, because their contract also
changes: the approvals `PUT` moves `session_key` out of the body, so its legacy
form needs a shim that accepts the old body. Nineteen minus one.

### Where the second authorization mechanism lives

Today `require_granted_bot` consults `_defers_to_its_handler`, and seven
operations are named there because their bot is not on the path or query string.
After this change no *new* address is in that position, but every *legacy*
address in the routines and skills groups still is.

So: the legacy routers mount **without** `_GRANT_CHECKED`, and each legacy shim
performs the grant check itself — the same `caller.require_bot(bot_id, owner_id=…)`
call the current handlers make, at the same point, before any service call. The
deferral table becomes a private detail of `deprecated/_shim.py` rather than a
carve-out in the shared seam.

The property the deferral list exists for is preserved and moves with it: an
operation the shared dependency cannot check is **refused**, never waved through.
`require_granted_bot` keeps that branch, and after this change it is a backstop
for a route added later rather than a live path.

### Deprecation headers

A middleware rather than a per-route dependency: it reads
`scope["route"].path`, which `access_log.py` already relies on, and stamps
`Deprecation: true` plus `Sunset: <http-date>` when the matched route is in
`LEGACY_ROUTES`. One place, and a legacy route added without the header is not
possible because the set is built from the registrations.

The sunset date is a single module constant. It is a published promise — see the
open question in the spec.

### admission.py grows before it shrinks

The table is keyed by `(method, path)` as FastAPI reports it, and legacy routes
are real routes, so every legacy address keeps its entry with today's mode and
every new address gets one. That is 39 additions, not 39 replacements — the table
roughly doubles for the duration of the deprecation and halves again on removal.

`test_admission_inventory.py` is what makes an omission loud, so the additions
are driven by running it rather than by transcribing a list.

### The gateway needs no change, and one test proves it

The wide `"/openapi/v1/bots/**"` rule in `route_security` covers every new
address, and none of the six per-path exceptions touches a re-addressed group.
`route_security` matches on the full pattern grammar — parameters included — so
`GET /openapi/v1/bots/{bot_id}/authorized-apps` keeps working unchanged. The
gateway's `tests/unit/core/authn/test_route_security.py` pins agreement with
`admission.py`'s `REFUSED` set; no `REFUSED` operation is re-addressed, so it
should stay green. Run it to confirm rather than assume.

## Risks

**A legacy and a new address drifting apart.** The whole compatibility promise is
that they answer identically, and two registrations of one handler make that
easy — until a shim quietly diverges. `test_legacy_parity.py` is the control:
it drives both and compares, and it is the test to write first.

**Missing an address in `admission.py`.** An operation absent from the table is
refused, so the failure mode is a 401 on a route that should work, caught by
`test_admission_inventory.py` before review.

**The doc path references.** ~104 occurrences across four files, and the
convention test parses two fenced lists out of `README.md`. Mechanical, but the
volume is where a typo hides; the test is what catches a list that fell behind.

**Scope creep from the three contract fixes.** Each is independently revertible
by design. If review wants them out, `owner_entity_id` → `owner_id` is the one
that cannot simply be dropped — it is load-bearing for closing #960 — so it
would have to be kept and the other two deferred.

**The published artifact is a release gate.** `bots.openapi.json` currently
matches the code exactly; regenerating it is a task, not a side effect, and the
compat diff on it will be large by design.
