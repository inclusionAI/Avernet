# Plan: Open API Bot Access Check

## Approach
Add one read-only, **undocumented** operation:
`GET /openapi/v1/bots/{bot_id}/access`. The caller passes `entity_id` and
`user_id`, plus an optional `required_level`.

- **Who may call:** only applications whose `app_name` is on a deployment
  allowlist. The handler checks this first.
- **Finding the Bot:** the existing `BotAddressResolver` looks up the exact
  `(bot_id, entity_id)` row.
- **The level:** the same `resolve_operable_permission_level` that the
  `/openapi/v1` access gate uses (`openapi_v1/bot_access.py:301`), so the two
  always agree.
- **Route tables:** the operation is `AdmissionMode.OPEN` and `NoCheck(...)`,
  because there is no human caller and no Bot grant to check. The real gate is
  the allowlist check in the handler.
- **Hidden:** the route is mounted with `include_in_schema=False`, so it stays
  out of the published Open API description.
- **No stage:** access is per Bot (collaborator rows are keyed by `bot_pk`), so
  the operation takes no stage input.

The answer fails closed in only one direction:
- no Bot at that address returns `has_access=false`;
- a failed collaborator lookup becomes a 500, never a made-up answer.

## Affected Components
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/bot_access_check/` (new) — router and schemas.
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/errors.py` — new `BotAccessCheckForbiddenError`.
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py` — map that error to 403.
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/{admission_routes,authorization_rules}.py` — one row each. These inventories walk the served routes (`tests/.../_route_walk.py`), so a route hidden from the schema still needs its rows.
- `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py` — mount the router in `_OPEN_SUBGROUPS`, before the `{bot_id}` wildcard group.
- `apps/backend/src/agentclaw/community/di/openapi_bot_access_check_config.py` + `di/modules/openapi_bot_access_check_config_module.py` (new) — the allowlist config.
- `apps/backend/src/agentclaw/community/di/modules/openapi_user_id_bypass_config_module.py` — accept the new key in the shared `openapi` block.
- `apps/backend/src/agentclaw/community/di/container.py` — register the module and resolve it eagerly at startup.
- `apps/backend/src/agentclaw/community/configs/application.yaml` — ship an empty allowlist.

No gateway change:
- `"/openapi/v1/bots/**"` is already `user: optional, app: optional` (`apps/gateway/configs/application.yaml:242`).
- `bots.openapi.json` is not republished.

## Data Model Changes
None.

## API / Interface Changes

```jsonc
// GET /openapi/v1/bots/{bot_id}/access?entity_id=e-1&user_id=u-123&required_level=member
// entity_id      required — together with bot_id names exactly one Bot row
// user_id        required — whose access to report (a subject, NOT the acting user)
// required_level optional — member (default) | admin | owner
// → 200
{ "code": 0, "data": {
    "bot_id": "20260813_a7k2m9p1", "entity_id": "e-1", "user_id": "u-123",
    "has_access": true, "level": "member", "required_level": "member" } }
// no Bot at (bot_id, entity_id) → 200, has_access=false, level="none"
// caller app_name not allowlisted (or no app on the credential) → 403 "Forbidden"
// collaborator lookup fails → 500 (unmapped; app-level handler)
```

```python
# openapi_v1/bot_access_check/schemas.py (new)
class AccessLevel(_DocumentedEnum):  # none | member | admin | owner
    ...
class RequiredLevel(_DocumentedEnum):  # member | admin | owner
    ...
class BotAccess(BaseModel):
    bot_id: str
    entity_id: str
    user_id: str
    has_access: bool
    level: AccessLevel
    required_level: RequiredLevel
```

## Key Files & Functions

```python
# openapi_v1/bot_access_check/router.py (new)
# Transitional: built for one integration's interim needs; remove when it migrates.
router = APIRouter(prefix="/openapi/v1/bots", tags=["Bots"], route_class=PublicAPIRoute)

@router.get("/{bot_id}/access", response_model=Envelope[BotAccess], include_in_schema=False)
@envelope_errors
async def get_bot_access(request: Request, bot_id: BotIdPath, principal: PrincipalDep,
                         entity_id: Annotated[str, Query(min_length=1, max_length=1024)],
                         user_id: Annotated[str, Query(min_length=1, max_length=256)],
                         required_level: RequiredLevel = RequiredLevel.MEMBER,
                         config: OpenApiBotAccessCheckConfig = Injected(...),
                         bots: BotRepository = Injected(BotRepository),
                         collaborators: CollaboratorServiceProtocol = Injected(...)) -> Envelope[BotAccess]:
    if caller_app_name(principal) not in config.app_names:
        raise BotAccessCheckForbiddenError("caller app is not allowlisted")
    level = await asyncio.to_thread(_level, bots, collaborators, bot_id=..., entity_id=..., user_id=...)
    ...

def _level(...) -> PermissionLevel:
    try:
        ctx = BotAddressResolver(bots).resolve(bot_id=bot_id, entity_id=entity_id)
    except BotAddressNotFoundError:
        return PermissionLevel.NONE
    return resolve_operable_permission_level(collaborators, bot=ctx.bot, user_id=user_id,
                                             owner_id=ctx.current_owner_id)  # exceptions propagate → 500
```

```python
# di/openapi_bot_access_check_config.py (new)
@dataclass(frozen=True)
class OpenApiBotAccessCheckConfig:
    app_names: frozenset[str] = frozenset()
```

```python
# di/modules/openapi_bot_access_check_config_module.py (new) — mirrors the bypass module
BLOCK, KEY, APP_NAMES = "openapi", "bot_access_check", "allowed_app_names"
class OpenApiBotAccessCheckConfigModule(Module):
    @singleton
    @provider
    def openapi_bot_access_check(self) -> OpenApiBotAccessCheckConfig: ...
# strict: mapping with only `allowed_app_names`; list of non-blank strings
```

```diff
# di/modules/openapi_user_id_bypass_config_module.py:51
-_ALLOWED_KEYS = frozenset({KEY})
+_ALLOWED_KEYS = frozenset({KEY, "bot_access_check"})
```

```diff
# configs/application.yaml:215
   openapi:
     user_id_bypass: []
+    # Transitional: applications (by app_name) allowed to call the undocumented
+    # GET /openapi/v1/bots/{bot_id}/access. Empty = nobody.
+    bot_access_check:
+      allowed_app_names: []
```

```diff
# openapi_v1/admission_routes.py (beside metadata/queries)
+    # Transitional app-only lookup; the handler enforces the configured app allowlist.
+    ("GET", "/openapi/v1/bots/{bot_id}/access"): AdmissionMode.OPEN,
# openapi_v1/authorization_rules.py
+    ("GET", "/openapi/v1/bots/{bot_id}/access"): NoCheck(
+        "allowlisted service apps; the handler checks the configured app_name allowlist"
+    ),
```

```diff
# openapi_v1/errors.py
+class BotAccessCheckForbiddenError(Exception):
+    """The caller is not an allowlisted application (→ 403, bot-independent)."""
# openapi_v1/responses.py (ENVELOPE_ERRORS)
+    BotAccessCheckForbiddenError: (403, "Forbidden"),
# openapi_v1/__init__.py:312 (_OPEN_SUBGROUPS)
+    bot_access_check_router,
# di/container.py:200 and :321
+        OpenApiBotAccessCheckConfigModule(),
+    _app_injector.get(OpenApiBotAccessCheckConfig)
```

Line headroom: `responses.py` 993 → 995 and `authorization_rules.py` 986 → 989,
both under the 1000-line cap.

## Dependencies
None.

## Risks & Mitigations
- **Risk:** The response shows who collaborates on any Bot, to any allowlisted app.
  **Mitigation:** The allowlist ships empty and is set only in deployment
  config. The refusal does not depend on which Bot or user is asked about, so it
  reveals nothing.
- **Risk:** `app_name` is a display value. An app that renames itself loses
  access, and two apps sharing a name would both be admitted.
  **Mitigation:** This was the user's choice for a transitional operation. Match
  the name exactly (case-sensitive) and document this in the config comment.
- **Risk:** The endpoint and the gate drift apart over time.
  **Mitigation:** Both use the same `resolve_operable_permission_level`.
  Owner, Space-member and COSEC-revocation cases are each tested.
- **Risk:** The transitional route is never removed.
  **Mitigation:** The router module, config comment and `NoCheck` reason all say
  it is transitional. Removing it means deleting that module, its two table rows
  and the config block.

## Alternatives Considered
- **Promote the internal `POST /api/bot/collaborator/check_permission`.** Rejected:
  it uses session auth and returns errors inside a 200.
- **Allowlist by `app_id`.** More stable, but the user chose `app_name`.
- **A `stage` input.** Dropped. Access is per Bot, so stage would never change
  the answer.
- **Optional `entity_id` (unique `bot_id` lookup).** Rejected. `bot_id` is not
  unique across owners (`core/engine_runtime/stage.py:205`), and requiring
  `entity_id` always names exactly one row.
- **Publish in `bots.openapi.json`.** Rejected: the operation is transitional
  and undocumented.
- **Masked 404 for unknown Bots.** The user chose `has_access=false` instead.

## Rollout
Additive. Nothing changes until a deployment overlay sets
`openapi.bot_access_check.allowed_app_names`. No schema publish is needed.

## Test Strategy
```python
# tests/community/adapters/http/openapi_v1/test_bot_access_check.py (new)
def test_allowlisted_app_gets_owner_level_for_the_owner(): ...
def test_explicit_admin_editor_passes_admin_bar(): ...
def test_member_fails_admin_bar_but_reports_member_level(): ...
def test_space_member_gets_member_access(): ...
def test_unknown_bot_is_200_without_access(): ...
def test_entity_id_and_user_id_are_required(): ...
def test_non_allowlisted_app_and_human_caller_get_403(): ...
def test_collaborator_lookup_failure_is_500_not_false(): ...
def test_route_is_absent_from_the_openapi_document(): ...
# tests/community/di/test_openapi_bot_access_check_config.py (new)
def test_default_is_empty(): ...
def test_rejects_non_list_blank_non_string_and_unknown_keys(): ...
```
`test_admission_inventory.py`, `test_authorization_inventory.py`,
`test_path_convention.py` and `test_explicit_user_id.py` must stay green. The
last two read the generated schema, so they should not see the hidden route.
