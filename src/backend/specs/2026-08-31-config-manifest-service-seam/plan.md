# Plan: One Seam for the Five Categories Apply Touches

## Approach

Backend-only, no DDL, no new endpoint, no schema change. Three moves:

1. **Each check moves to its category's existing core home** — beside the code
   that already owns that domain, not into a new grab-bag. `_safe_path` lands
   next to `is_readonly` in `resource_file_service.py`; the engine-config target
   resolution lands next to the engine-config service. This keeps Rule 9 (one
   reason to change per file) and means the moved function's neighbours are the
   code it guards.

2. **One new module holds only the table.** `core/bot_config_surface/` declares
   which checks govern which category and nothing else — the seam's index, not
   its body. This mirrors the collaborator precedent exactly, where
   `authorization.py` is a table module and `bot_access.py` is the mechanism it
   drives.

3. **The five routers import the moved functions** and keep only what Rule 7
   permits an adapter to keep: error mapping, and auth interpretation.

The change is inert because the moved functions raise the **same domain error
types they raise today** — `InvalidResourcePathError`, `LocalSkillNotFoundError`,
`BotNotFoundError` already live in `core` — so `ENVELOPE_ERRORS` maps them to the
same status and message without being touched. Where a check raises
`HTTPException` today (resources' read-only 403), the *decision* moves and the
`raise` stays in the router, so the response body is bit-identical rather than
argued to be equivalent.

## What Moves, and What Deliberately Does Not

The line is Rule 7's: **domain policy** moves to core, **auth interpretation**
and **error mapping** stay in the adapter. Applied case by case:

| Today | Where | Verdict |
| --- | --- | --- |
| `_safe_path` — reject `..` | `resources/router.py:123` | **Moves.** A workspace path rule, true of the workspace |
| `_require_path` — reject empty | `resources/router.py:148` | **Moves.** Same |
| `_reject_read_only` — dotfiles, root identity files, every ancestor | `resources/router.py:160` | **Decision moves**, `raise HTTPException` stays |
| `_file_coords` — `(staff, owner_id, engine)` | `resources/router.py:186` | **Moves** |
| `_require_addressed_bot` — skill belongs to the addressed bot | `skills/router.py:195` | **Moves.** A statement about the skill record |
| `_require_skills_grant` — bind the app grant to the record's `(bot, owner)` | `skills/router.py:216` | **Stays.** Takes `ActingCaller`, an adapter type; app grants are auth interpretation and apply's grant is already checked at its own door |
| `_directory_relative_paths` — multipart path list | `skills/router.py:254` | **Stays.** Parses a multipart wire format; apply has no multipart |
| `application/zip` content-type check | `skills/router.py:497` | **Stays.** Protocol validation, literally a header read |
| `_engine_config_target` + `bot_service.get_bot` guard | `bots/engine_config.py:65,110` | **Moves.** Ownership and addressing |
| `entity_type = "staff"`, `<type>.md` re-suffixing | `identity/router.py:84,131` | **Moves** |
| `_ENTITY_TYPE = "staff"` | `mcp/router.py:118` | **Moves** |

Four rows say "stays", and each names why. That column is the point: a criterion
reading "no check reachable only from inside a router body" is satisfied by
moving domain policy out, not by evacuating the adapter of everything it is
allowed to do. A reviewer should be able to check the four.

## Affected Components

**New**

- `src/backend/.../core/bot_config_surface/README.md` — Context Boundary block
  per `docs/arch/context-boundary-format.md`
- `src/backend/.../core/bot_config_surface/__init__.py` — the `CONFIG_SURFACE`
  table and the `CategoryChecks` row type

**Moved into (existing files)**

- `core/services/resource_file_service.py` — `safe_workspace_path`,
  `require_workspace_path`, `is_write_forbidden`; joins `is_readonly`, which is
  already there
- `core/services/resource_addressing.py` — `resource_file_coords`
- `core/services/identity.py` — `identity_file_coords`, `physical_file_name`
- `core/services/engine_config.py` — `engine_config_coords`
- `core/skill_center/errors.py` → no change; the check lands in
  `core/skill_center/services/skill_query_service.py` as
  `require_addressed_bot`
- `core/mcp/config_flow.py` — `mcp_config_coords`

**Rewired (call the moved function; no behaviour change)**

- `adapters/http/openapi_v1/resources/router.py`
- `adapters/http/openapi_v1/skills/router.py`
- `adapters/http/openapi_v1/identity/router.py`
- `adapters/http/openapi_v1/mcp/router.py`
- `adapters/http/openapi_v1/bots/engine_config.py`

**Not touched:** `authorization.py`, `bot_access.py`, `admission.py`,
`principal.py`, every other router group, the internal API, the console routers,
`src/gateway/configs/schemas/bots.openapi.json` (no published contract changes —
the seam is invisible in the document).

## The Table

```python
# core/bot_config_surface/__init__.py

@dataclass(frozen=True)
class CategoryChecks:
    """Every rule the public surface enforces for one config category."""
    category: str
    resolve_coords: Callable[..., BotConfigCoords]
    validators: tuple[Callable[..., Any], ...]

CONFIG_SURFACE: dict[str, CategoryChecks] = {
    "identity": CategoryChecks("identity", identity_file_coords, (physical_file_name,)),
    "resources": CategoryChecks("resources", resource_file_coords,
                                (safe_workspace_path, require_workspace_path,
                                 is_write_forbidden)),
    "skills": CategoryChecks("skills", skill_coords, (require_addressed_bot,)),
    "mcp": CategoryChecks("mcp", mcp_config_coords, ()),
    "engine_config": CategoryChecks("engine_config", engine_config_coords, ()),
}
```

`BotConfigCoords` is a frozen dataclass carrying `(bot_id, owner_id,
entity_type, entity_id, engine_type)`. `engine_type` is `None` for the two
categories that do not address an engine — it is **not** defaulted, because a
default would quietly hand `identity` an engine it never had.

`resolve_coords` differs per row on purpose (spec *Decisions* 3). The table is
where that finally shows: five rows, four different resolutions, visible in one
screen for the first time.

## Why the Table Is Load-Bearing and Not Decoration

The criterion that matters is **router and table hold the same function
object**. A table naming functions that merely resemble the router's would be
worse than no table — it would document a guarantee that does not hold. So:

```python
def test_router_and_table_share_one_object():
    from ...openapi_v1.resources import router as resources_router
    assert resources_router._safe_path is CONFIG_SURFACE["resources"].validators[0]
```

`is`, not `==`. One such assertion per moved function. A future edit to the
router's copy is impossible because there is no router copy — the name in the
router module is a binding to the core function, and this test is what stops
someone reintroducing a local one.

The second structural test is the omission guard, the precedent's contribution:

```python
def test_no_handler_only_checks_remain():
    """Every module-private callable in the five routers is either called from
    outside a handler, or is on the reviewed exception list with its reason."""
```

The exception list is the four "stays" rows above, each with its reason string.
Adding a fifth private check to one of these routers fails the test until
someone writes down which side of Rule 7's line it is on. That is the same
"omission is not survivable" property `PublicAPIRoute` gives the collaborator
seam, obtained by test rather than by assembly because there is no assembly step
to hook here.

## Import Direction

`core/bot_config_surface` imports from `core` only. Nothing in it imports
`fastapi`, `adapters`, or any HTTP type — checked by the existing core-purity
CI gate as well as by review. The routers import *from* it, which is the
permitted direction.

`core/bot_config_surface` importing from six other `core` packages is worth
naming: it is an index over them, so the fan-out is the module's purpose rather
than a smell. It exposes no new behaviour of its own and must not grow any —
the README's Context Boundary block says so, so that the next person does not
find it a convenient place to put logic.

## Testing

- **The existing endpoint tests are the inertness proof, and they are not
  edited.** `tests/community/endpoints/test_openapi_resources.py`,
  `test_openapi_bot_skills_read.py`,
  `tests/community/adapters/http/openapi_v1/resources/test_resources_handlers.py`
  and the identity / mcp / engine-config suites all pass unchanged. If any needs
  a change, the move was not inert and that is a finding, not a fixup.
- **New:** the same-object test above, one assertion per moved function.
- **New:** the handler-only-check guard with its reasoned exception list.
- **New:** direct unit tests on the moved functions called with no request at
  all — this is the thing the feature exists to make possible, so it is proven
  rather than assumed.
- Three test files carry comments naming `_safe_path`, `_file_coords`,
  `_require_addressed_bot` and `_require_skills_grant`. None *imports* them, so
  none breaks. The names stay bound in the router modules, so the comments stay
  accurate; they are not edited.
- `OCB_PRE_PUSH_RUN_CI=1` on push, per work-items §8 — this touches the apply
  path's foundations.

## Risks

- **A moved function's error escapes a different way.** The mitigation is that
  the error *types* do not change; only the module the function lives in does.
  The one place a type would have changed (read-only 403) is the one place the
  `raise` deliberately stays in the router.
- **`resolve_runtime_engine_for_bot` in `resource_file_coords` needs
  `BotRepository`**, injected today at the route. Passed as an argument rather
  than resolved inside, so the core function stays free of the DI container and
  apply can hand it the same instance.
- **Scope pressure.** The budget is 0.25 day against criteria written for more
  (work-items §7 says this outright, for every item). If something is cut it is
  the `mcp` and `engine_config` rows' validators — both are empty tuples today,
  so their rows are coords-only and cheap; cutting them would save nothing.
  The realistic cut is the handler-only-check guard test, and the cost of
  cutting it is the omission property, which is the half of the precedent worth
  copying. It should be the last thing to go.

## Follow-up (named, not done here)

The four divergent `resolve_coords` implementations. `resources` performs no
ownership guard while `engine_config` does; `identity` and `mcp` hardcode
`"staff"` against `owner_id` while `engine_config` reads the bot record. Once
the table shows them side by side, whether they *should* differ is answerable —
and answering it changes who is admitted, so it is a separate spec with its own
argument, not a tidy-up to fold in here.
