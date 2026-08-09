# Plan: Public API — Operate Shared Bots and Published Stages

## Approach

Two optional query parameters — `owner_id` and `stage` — on the 16
engine-runtime operations; one adjudication that replaces the `is_shared`
refusal with "is the caller this bot's owner or a member-level collaborator?";
one stage-aware device resolution that replaces the `draft_device=True`
boolean. The split mirrors the explicit-user-id change exactly: *acquisition*
(the request names owner and stage) is a parameter dependency, *adjudication*
(may this caller operate that bot) happens once, inside the resolve every
group already runs before touching a device.

Nothing moves for a request that names neither parameter: `owner_id` defaults
to the request's `user_id`, `stage` defaults to `draft`, and the resolved
behavior is byte-for-byte today's.

**This work builds on `specs/2026-08-08-openapi-v1-explicit-user-id/`** (PR
#902, merged to `dev` as `5cdb614`). One wrinkle is inherited from it: the
engine-runtime handlers bind `UserIdDep` to a local still named `owner_id` —
accurate while the caller was always the owner, which is precisely the
identity this change breaks. The conversion renames those locals to `user_id`
and introduces the real `owner_id`, so the dep's value is never again passed
as the owner.

## The rules

> **Adjudication.** A caller may operate a bot iff they are its owner, or a
> collaborator on it at `PermissionLevel.MEMBER` or above. Public visibility
> grants nothing. Any other caller is answered with `BotNotFoundError` — the
> masked 404, byte-identical to a bot that does not exist. A failed
> collaborator lookup refuses (fail closed), exactly as `bot_is_shared` does
> today.
>
> **Addressing.** `owner_id` and `stage` are optional query parameters on
> every engine-runtime operation, and nowhere else. `owner_id` defaults to the
> request's `user_id`; `stage` defaults to `draft`. A stage with no live
> runtime raises `EngineStageNotLiveError` → 409, one fixed message,
> distinguishable from the masked 404 (an operator may fix a stage; a stranger
> must not learn a bot exists).

Stage → binding, for a `service` bot:

| `stage` | Resolves | Live when |
| --- | --- | --- |
| `draft` (default) | `ac_bots.binding_id` via `resolve_for_bot` — today's `draft_device=True` path | always (it is the workspace) |
| `verify` | newest `ac_bot_publish` record at `VALIDATING`, `ext.binding["verify"]`, via `resolve_for_binding_invoke` | a verify release is validating |
| `online` | newest `ac_bot_publish` record at `SUCCESS`, `ext.binding["online"]`, via `resolve_for_binding_invoke` | an online release is live |

A `personal` bot has only `draft`; `verify`/`online` raise
`EngineStageNotLiveError` — same answer as a dead stage, because that is what
it is. No fallback in either direction (the current
`_resolve_published_device` already refuses rather than falling back to the
draft; this keeps that property per stage).

## Affected Components

- `…/core/engine_runtime/gate.py` — loses the `is_shared` refusal; keeps the
  bot-type allowlist; docstring rewritten (it also currently misstates the
  draft/published separation as a `publish_bot_id` naming scheme — the real
  separation is storage: `ac_bots.binding_id` vs
  `ac_bot_publish.ext.binding.{verify,online}`; the code never writes a
  `…pub…` id).
- `…/core/engine_runtime/relay.py` — `resolve_bot` gains the adjudication
  (caller vs owner + collaborator level); `call` replaces
  `draft_device: bool` with `stage: str`; `_resolve_published_device` becomes
  stage-parameterized.
- `…/core/engine_runtime/sharing.py` — **retired.** Its two callers (relay,
  connection) are exactly the two places the adjudication replaces.
  `BotFacts.is_shared` goes with it.
- `…/core/engine_runtime/models.py` — `BotFacts` drops `is_shared`; gains
  `owner_id` (the resolved owner, needed downstream where the caller no longer
  equals it).
- `…/core/engine_runtime/errors.py` — new `EngineStageNotLiveError`.
- `…/core/engine_runtime/connection.py` — `build` takes caller, owner and
  stage; adjudicates via the same core helper; resolves verify/online bindings
  through the same stage lookup as the relay; `OperatorContext.staff_id`
  becomes the caller (the true actor).
- `…/api/engine_runtime_service.py` — `EngineRuntimeRelayProtocol` mirrors the
  `resolve_bot_off_loop` / `call` signature changes (pinned by
  `tests/community/architecture/test_service_api_conformance.py`; protocol and
  implementation move together).
- `…/adapters/http/openapi_v1/engine_runtime/gating.py` — `resolve_operable_bot`
  takes caller, owner and stage and passes them through.
- `…/adapters/http/openapi_v1/engine_runtime/params.py` (new) — `OwnerIdDep`
  and the `stage` parameter, defined once like `UserIdDep`.
- `…/adapters/http/openapi_v1/engine_runtime/enums.py` — new `RuntimeStage`
  documented enum (`draft` / `verify` / `online`), request-only.
- `…/adapters/http/openapi_v1/engine_runtime/{sessions,engine,models,approvals,connection}/router.py`
  — 16 handlers gain the two parameters; forwards pass `stage` instead of
  `draft_device=True`.
- `…/adapters/http/openapi_v1/responses.py` — `EngineStageNotLiveError: (409,
  "No live runtime at the requested stage")`.
- `…/adapters/http/openapi_v1/contracts.py` — the 409 documented on the
  engine-runtime groups if `ERROR_RESPONSES` does not already carry 409
  surface-wide (verify at implementation; `DeviceNotBoundError` is already a
  409 on this surface).
- `src/backend/docs/openapi-v1/README.md` + `README.zh-CN.md`,
  `engine-surface.md` + `engine-surface.zh-CN.md` — the widened rule, the
  status board, the changelog (including the retroactive #880 entry).

**No schema module is touched** — both parameters are query-string, per the
placement rule.

## Data Model Changes

None. Collaborators (`ac_bot_collaborator`), publish records
(`ac_bot_publish`) and visibility (`ac_bots.public`) are read as they are.

## API / Interface Changes

**Additive for all 16 `/openapi/v1` engine-runtime operations** — two optional
query parameters and one new documented 409. No parameter is removed, no path
changes, no request body changes. A request valid today is valid tomorrow and
answers identically.

### The parameters

```python
# openapi_v1/engine_runtime/params.py (new)
async def resolve_owner_id(
    user_id: UserIdDep,
    owner_id: Annotated[str | None, Query(min_length=1, max_length=256,
                                          description=...)] = None,
) -> str:
    """The bot owner this request addresses. Defaults to the caller."""
    return owner_id if owner_id is not None else user_id

OwnerIdDep = Annotated[str, Depends(resolve_owner_id)]
StageQuery = Annotated[RuntimeStage, Query(description=...)]  # default RuntimeStage.DRAFT
```

(`str | None` here is the external input boundary: absent is a real state
meaning "the caller's own bot" — the contract `AGENTS.md` permits.)

### Every affected operation, in one shape

```diff
# openapi_v1/engine_runtime/sessions/router.py — list_sessions
 async def list_sessions(
     bot_id: str,
     page: PageParamsDep,
-    owner_id: UserIdDep,
+    user_id: UserIdDep,
+    owner_id: OwnerIdDep,
+    stage: StageQuery = RuntimeStage.DRAFT,
     request: Request,
     ...
 ) -> Envelope[SessionPage]:
-    facts = await resolve_operable_bot(relay, bot_id, owner_id, surface="sessions")
-    ... relay.call(bot_id=bot_id, owner_id=owner_id, facts=facts, draft_device=True, ...)
+    facts = await resolve_operable_bot(
+        relay, bot_id, caller_id=user_id, owner_id=owner_id,
+        stage=stage.value, surface="sessions")
+    ... relay.call(bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value, ...)
```

The rename on the first line of the signature is the point, not cosmetics:
`UserIdDep` returns the *caller*, and after this change the caller and the
owner are distinct roles that each go where they belong.

```jsonc
// Owner, own private bot — unchanged, byte-for-byte:
// GET /openapi/v1/bots/sessions/b-1?user_id=u-owner                     → 200
// Collaborator on the team's coding bot:
// GET /openapi/v1/bots/sessions/b-1?user_id=u-collab&owner_id=u-owner   → 200
// Owner watches the online runtime they published:
// GET /openapi/v1/bots/engine/b-1/status?user_id=u-owner&stage=online   → 200
// Collaborator takes the operator socket to the online runtime:
// GET /openapi/v1/bots/connection/b-1?user_id=u-collab&owner_id=u-owner&stage=online → 200 (wss URL)
// Stranger probes a real bot — identical to a bot that does not exist:
// GET /openapi/v1/bots/sessions/b-1?user_id=u-x&owner_id=u-owner        → 404 {"code":404000,"message":"Not found",…}
// Verify stage while nothing is validating:
// GET /openapi/v1/bots/sessions/b-1?user_id=u-owner&stage=verify        → 409 {"code":409000,"message":"No live runtime at the requested stage",…}
// stage=eval / anything not in the enum                                  → 422 (validation, enveloped)
```

`user_id` keeps its #902 contract on every operation: it names the verified
caller, and naming anyone else is the fixed 403 before any of this runs.

### The adjudication seam

In `relay.resolve_bot` (and mirrored in `EngineConnectionService.build`, which
composes its own socket without the relay):

```python
def resolve_bot(self, bot_id: str, owner_id: str, caller_id: str) -> BotFacts:
    bot = self._bot_service.get_bot(bot_id, owner_id)   # tenant + existence, masked 404
    if caller_id != resolved_owner:
        level = self._collaborator_level(bot_id, resolved_owner, caller_id)
        if level < PermissionLevel.MEMBER:
            logger.warning("caller %s is not an operator of bot %s owned by %s", ...)
            raise BotNotFoundError(...)                 # byte-identical to absent
```

`_collaborator_level` reads the existing collaborator repository (the relay
already holds it for `bot_is_shared`); a lookup failure logs and returns
`NONE` — fail closed, the direction `bot_is_shared` already pins. The gate
(`require_operable_bot`) keeps only the bot-type allowlist and its `surface`
naming; `is_shared` disappears from its signature.

Note the deliberate asymmetry with `DeviceService.get_device_connection`,
whose own model admits any caller to a *public* bot's connection: our
adjudication runs first and is stricter, so composing with the caller as
`OperatorContext.staff_id` cannot widen the surface — and a test pins that a
public bot's non-collaborator is refused before `DeviceService` is consulted.

### Stage resolution

`relay._resolve_device(facts, owner_id, stage)`:

```python
if facts.bot_type != _SERVICE_BOT_TYPE or stage == STAGE_DRAFT:
    if stage != STAGE_DRAFT:                       # personal bot, verify/online
        raise EngineStageNotLiveError(...)
    return self._resolver.resolve_for_bot(bot_id, owner_id)
record = newest publish record with status == _STAGE_STATUS[stage]   # VALIDATING / SUCCESS
bind_id = (record.ext.binding or {}).get(stage)
if not record or not bind_id:
    raise EngineStageNotLiveError(...)
return self._resolver.resolve_for_binding_invoke(int(bind_id), owner_id, bot_id=bot_id)
```

This is today's `_resolve_published_device` with the status filter
parameterized (`verify` ↔ `VALIDATING`, `online` ↔ `SUCCESS`) instead of
hardcoded to `SUCCESS`, and the refusal typed (`EngineStageNotLiveError`
instead of falling into `DeviceNotBoundError`). The publish-record lookup
stays keyed on `facts.bot_pk` — `bot_id` is not unique across owners.

The same stage→binding lookup is extracted where both the relay and
`EngineConnectionService` can call it (a small helper in
`core/engine_runtime/`), so the socket and the HTTP groups cannot drift on
which runtime a stage names — the same reason the gate lives once.

### Sessions on a shared runtime

Session create keeps stamping the request's verified user into the engine
payload — which is now the *actor* (a collaborator's session records the
collaborator), satisfying the spec's attributability criterion with no new
code. List/get stay device-wide: that is the operator-console contract,
documented rather than filtered.

## Key Files & Functions

```python
# core/engine_runtime/errors.py (new sibling of EngineBotTypeNotSupportedError)
class EngineStageNotLiveError(Exception):
    """The requested stage has no live runtime for this bot (→ 409)."""
```

```diff
# adapters/http/openapi_v1/responses.py — beside DeviceNotBoundError's 409
+    EngineStageNotLiveError: (409, "No live runtime at the requested stage"),
```

```diff
# api/engine_runtime_service.py — EngineRuntimeRelayProtocol
-    async def resolve_bot_off_loop(self, bot_id: str, owner_id: str) -> BotFacts: ...
+    async def resolve_bot_off_loop(
+        self, bot_id: str, owner_id: str, caller_id: str) -> BotFacts: ...
-    async def call(..., draft_device: bool = False) -> Any: ...
+    async def call(..., stage: str) -> Any: ...
```

`stage` is required with **no default** (a review refinement over this plan's
first revision, which defaulted it to the published online runtime — a
default no caller used, and a one-missing-kwarg trap: a handler that gated on
the draft but forgot `stage=` on the forward would silently address the
multi-caller online runtime). The relay has no callers outside the gated
groups, so requiring it costs nothing.

Two more refinements from the same review round, both in
`core/engine_runtime/stage.py`: the verify stage is live not only while a
record validates but also through the newest SUCCESS record's **retained**
verify binding while that binding is ACTIVE — mirroring cron's
`_get_retained_verify_publish_record`, so the two surfaces cannot disagree on
whether a runtime exists — and the stage vocabulary is aliased from
`PublishStage` rather than respelled.

Per-router handler counts (all gain `owner_id` + `stage`): sessions 7, engine
3, models 2, approvals 3, connection 1 — 16 operations.

Docstring corrections carried in the same change: `gate.py` and
`connection.py` both explain the draft/published separation via a
`source_bot_id + "pub" + version` id scheme that the code does not implement
(every creation site writes `publish_bot_id = bot_id`; the schema comment is
aspirational). The accurate statement — draft binding on `ac_bots.binding_id`,
published bindings only in `ac_bot_publish.ext.binding` — replaces it.

## Dependencies

None new. Sequencing only: after `2026-08-08-openapi-v1-explicit-user-id`.

## Risks & Mitigations

- **Risk:** the collaborator lookup fails open somewhere and a stranger
  operates a bot. **Mitigation:** the lookup failure path returns `NONE` and
  refuses, pinned by a test that mirrors today's
  `test_an_unreadable_collaborator_table_refuses_rather_than_publishes` — same
  direction, new mechanism.
- **Risk:** the masked 404 leaks through timing or wording — a refused
  operator vs a missing bot. **Mitigation:** both paths raise the same
  `BotNotFoundError` type mapped to the same fixed body; a test asserts
  byte-identical responses for (absent bot, foreign owner, non-operator
  caller).
- **Risk:** a multi-instance provider (baas/teclaw) fans one published stage
  out to several device UUIDs; this surface addresses the stage's *binding*
  and the transport resolves one current address per call, so results describe
  the addressed instance, not the fleet. **Mitigation:** documented in
  engine-surface.md; cron-style fan-out stays internal. No silent cap — the
  doc says what a stage answer covers.
- **Risk:** `EngineConnectionService` and the relay disagree on what a stage
  resolves to. **Mitigation:** one shared stage→binding helper, used by both;
  a test asserts the socket and the sessions group address the same binding
  for the same (bot, stage).
- **Risk:** the relay protocol signature change ripples.
  **Mitigation:** `test_service_api_conformance.py` pins protocol =
  implementation; all call sites are in the five gated groups plus the
  relay's internal callers, converted in one task.
- **Risk:** #902 and this change edit the same 16 handler signatures.
  **Mitigation:** sequencing is declared (this lands second); the artifacts
  are branch-independent, only implementation orders.
- **Risk:** a superseded verify record (status `upgraded`/`released`) resolves
  as live. **Mitigation:** the status filter admits exactly `VALIDATING` for
  verify and `SUCCESS` for online; superseded statuses refuse; a test covers
  the upgrade-in-flight sequence.

## Alternatives Considered

- **Per-caller session scoping on shared runtimes** (each caller sees only
  their sessions). Rejected: the engine's session-list ports drop the
  `user_id` filter entirely — both engines, proven at the port signature — so
  upstream filtering is a silent no-op; and a backend-owned
  caller→session index (the expert-chat pattern) rebuilds the chat product
  inside an operator console. The internal workbench already shows an owner
  the whole runtime; this surface documents the same exposure.
- **Admitting public-bot callers as operators**, matching
  `DeviceService.get_device_connection`'s internal model. Rejected: internal
  reachability is not public authorization; the audience of a public bot
  converses with it over the chat path. The narrower rule can widen later at
  the seam; the wider rule could never narrow.
- **`stage` as a path segment** (`…/sessions/{bot_id}/online`). Rejected: the
  addressing rule fixes what follows a component literal, a stage is a
  selector rather than a resource, and the placement rule the explicit-user-id
  change settled puts scoping parameters in the query string.
- **A required `owner_id`**, symmetric with the required `user_id`. Rejected:
  `user_id` became required because the caller's implicitness is about to
  stop being true (delegation); "the bot is my own" stays a valid default
  forever, and requiring it would break every existing request for no
  contract gain.
- **Keeping `draft_device` and adding a second boolean/param for published
  stages.** Rejected: two axes for one question, and the boolean's name is
  already wrong the moment verify exists.
- **A separate role bar per operation** (member reads, admin writes).
  Rejected for now: no internal surface draws that line, and inventing policy
  in an expansion makes the diff about the policy. The seam takes a level
  parameter, so the split stays one edit away. Flagged as spec Open
  Question 3.

## Rollout

No migration, no flag, no deploy ordering beyond landing after #902. The
change is additive on the wire; the behavioral widening applies only to
requests that name what it adds. External tenants still cannot reach the
surface (`route_security` admits only a Google-chain `user`), so the widened
grants land before any external caller exists to hold them — the same window
#902 uses. Rollback is reverting the commit.

## Test Strategy

Deliberate flips of the pins on today's refusals, then new pins on the widened
contract:

```python
# tests/community/core/engine_runtime/test_connection.py — flips
test_a_shared_service_bot_is_refused_before_a_device_is_touched   → served to owner
test_a_collaborated_service_bot_is_refused                        → served to member+
test_a_public_personal_bot_is_refused_before_a_device_is_touched  → served to owner
test_a_collaborated_personal_bot_is_refused                       → served to member+
test_an_unreadable_collaborator_table_refuses_rather_than_publishes → kept (fail closed, new mechanism)
test_an_unknown_bot_type_is_refused_rather_than_assumed_personal  → kept

# tests/…/openapi_v1/engine_runtime/test_sessions.py, test_engine_models.py,
# test_approvals.py — the shared-bot 501 pins flip to 200-for-operators /
# 404-for-strangers; the draft-device pins become stage=draft pins. The stale
# "Only sessions is personal-only" docstring at test_engine_models.py:202 is
# corrected in passing.

# new: tests/…/openapi_v1/engine_runtime/test_operator_access.py
def test_owner_operates_public_personal_bot(): ...
def test_member_collaborator_is_served_all_sixteen_routes(): ...
def test_below_member_and_stranger_get_the_masked_404(): ...       # byte-identical trio
def test_public_visibility_grants_nothing(): ...
def test_refusal_logs_caller_and_owner(): ...
def test_collaborator_lookup_failure_refuses(): ...

# new: tests/…/openapi_v1/engine_runtime/test_stage_addressing.py
def test_default_stage_is_draft_byte_for_byte(): ...               # no-param compat
def test_online_resolves_the_success_records_online_binding(): ...
def test_verify_resolves_only_while_validating(): ...
def test_superseded_records_do_not_resolve(): ...
def test_personal_bot_verify_online_is_409(): ...
def test_stage_never_falls_back(): ...
def test_connection_and_sessions_address_the_same_binding(): ...
def test_unknown_stage_value_is_422(): ...

# document-level, in the shape of test_explicit_user_id.py
def test_owner_id_and_stage_on_exactly_the_sixteen(): ...
def test_both_are_optional_query_parameters(): ...
def test_the_409_is_documented_on_the_engine_runtime_groups(): ...
```

`tests/community/core/engine_runtime/test_relay.py`'s `bot_is_shared` suite
retires with `sharing.py`; its published/draft resolution suite gains the
stage parameterization cases. Then the module gates from `AGENTS.md`: backend
SAST, `tests/community` in full, changed-line coverage, and singlebox
coverage (every `/openapi/v1` route answers 401 in singlebox, so the E2E
surface is unaffected by optional parameters).
