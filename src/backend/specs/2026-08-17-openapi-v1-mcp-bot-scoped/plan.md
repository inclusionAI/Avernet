# Plan — MCP config lifecycle + bot-scoped activation

Spec: `spec.md`. Two parts, one PR — they share the category, the docs entry and
the admission-table edit, and shipping activation without the config lifecycle
publishes state a caller cannot inspect or undo.

## Decision 1 — per-MCP activation is a new column on `ac_skill_set_mcp`

**Chosen.** Add `is_active BOOLEAN NOT NULL DEFAULT 1` to `ac_skill_set_mcp`.
The four public verbs become plain row operations against the **bot's default
skill set**:

| Public verb | Storage effect |
|---|---|
| add | `INSERT` into `ac_skill_set_mcp` with `is_active = 0` |
| activate | `UPDATE … SET is_active = 1` |
| deactivate | `UPDATE … SET is_active = 0` |
| remove | `DELETE` the row |

`DEFAULT 1` is load-bearing: every existing row and every row the internal
skill-set API creates reads as active, so nothing about today's behaviour
changes. Only the public API creates inactive rows.

### Why a column, and not the exclusion table

An earlier revision of this plan proposed `add_default_mcp_exclusion` /
`remove_default_mcp_exclusion` as deactivate/activate. **That was wrong**, and
the correction is the reason this design exists.

`ac_default_skillset_mcp_exclusion` is not a general on/off switch. In
`get_set_mcp_servers` it is consulted only inside `if skill_set.get('is_default')`
and applied only to the *engine-supplied* codes:

```python
for code in default_codes:          # engine-supplied codes ONLY
    if code in excluded_codes:
        continue                    # skip *synthesising* this default
    if code not in db_codes:
        associations.append({...})
```

`associations` — the real `ac_skill_set_mcp` rows — is never filtered by
`excluded_codes`. `collect_bot_mcps` applies exclusions the same narrow way,
only to `default_mcp_configs`. So an exclusion row written for a server the
public API added is a **silent no-op**: the row is already in `db_codes` and
survives untouched.

The exclusion table means exactly one thing — *"do not synthesise this engine
default"* — and Decision 3 keeps it doing only that.

### Why the default skill set is the container

`ac_skill_set_mcp` rows must hang off some skill set. It cannot be a dedicated
one, because **skill-set activation is exclusive**: `set_active_skill_set`
(`core/repository/implementations/skill_center/skill.py:2040-2087`) clears
`is_active = 0` on every non-default skill set for the (user, bot, engine)
before activating its target. A dedicated "openapi" skill set would be
deactivated the moment anyone activated a skill set in the workbench, and every
MCP the public API added would stop reaching the agent with no signal to
either surface.

The default skill set is immune: `get_all_active_skill_sets:2151-2161` appends
it separately from the `is_active == True` query, so the exclusivity sweep never
touches it. It is also already where synthesised engine defaults and the
exclusion table live, which is what Decision 3 needs.

The cost is that public-API MCPs share a container with whatever the workbench
puts in the default set. Acceptable: the column, not the container, carries the
state this surface owns, and `is_default` on each entry tells the two apart in
the listing.

### Read path

One filter, one field passed through:

- `get_mcp_servers_in_set` returns `is_active` alongside the existing fields.
- `get_set_mcp_servers` carries it out as `active`; synthesised defaults get
  `active = True` (they have no row, and exclusion already removed the ones the
  caller turned off).
- **`collect_bot_active_mcps` filters `active is True`.** This is the one change
  to the shared collect path, and existing rows default to active, so the
  internal surface and artifact compose behave exactly as they do today.
- `collect_bot_mcps` — the "all MCPs including inactive" variant — does **not**
  filter. It is the listing source.

Because `collect_bot_active_mcps` remains the single input to both
`_declare_mcp_scope` and `config_compose/services/collector.py:195`, the device
whitelist and the composed artifact pick activation up with no further changes.

## Decision 2 — the credential stays user-scoped

`ac_user_mcp_config` stays keyed `(user_id, server_code)`: one credential per
user per server, shared by that user's bots. Activation is the per-bot axis; the
credential is not.

`build_mcp_sync_payload` (`core/mcp/services/config_service.py:179-218`) looks
the row up by exactly that key on every device sync and every artifact compose —
making it per-bot changes that lookup and every call site, for a data model the
third party does not have (an API key is an account fact at the MCP server, not
a per-bot fact). Keeping the axes orthogonal is what lets `DELETE config` leave
activation alone and `DELETE bot server` leave the credential alone.

## Decision 3 — engine defaults are listed, deactivatable, not removable

Engine defaults appear in the bot listing with `is_default: true`, reading
`active: true` until the caller turns one off.

- **deactivate** on a default writes an exclusion row
  (`add_default_mcp_exclusion`) — the one thing that table is for, used exactly
  as designed.
- **activate** removes it (`remove_default_mcp_exclusion`).
- **remove** is refused with `McpDefaultServerNotRemovableError` → `409`. A
  default is synthesised per request from `get_default_mcp_servers`; there is no
  row to delete, so "not on the bot" is not a state it can hold.

So the service dispatches on `is_default` for exactly these three verbs, and the
public contract stays uniform apart from the documented `409`.

## Surface

### Part 1 — account-level, existing group (`openapi_v1/mcp/router.py`)

| Method | Path | Success |
|---|---|---|
| GET | `/openapi/v1/bots/mcp/configs` | `Envelope[Page[McpConfig]]` |
| DELETE | `/openapi/v1/bots/mcp/servers/{server_code}/config` | `Envelope[Deleted]` |

`GET configs` pages `UserMCPConfigRepository.list_by_user`, projecting through
the existing `_to_config` so masking is the same function, not the same
intent.

`DELETE config` mirrors `write_unified_config` in reverse and reuses its
atomicity: confirm the server exists in the marketplace (404 if not), read the
row for rollback, delete it, push the removal via
`MCPSyncServiceProtocol.remove_mcp_detail`, restore the row and fail (502) if
the push fails. Deleting an absent row is a success with `deleted: false`.

New shared flow functions go in `core/mcp/config_flow.py` beside
`read_unified_config` / `write_unified_config`: `delete_unified_config` and
`list_unified_configs`.

### Part 2 — bot-scoped, new group (`openapi_v1/bot_mcp/`)

Prefix `/openapi/v1/bots/{bot_id}/mcp`, mirroring `skills`.

| Method | Path | Success |
|---|---|---|
| GET | `""` | `Envelope[Page[BotMcpServer]]` |
| GET | `/{server_code}` | `Envelope[BotMcpServer]` |
| POST | `""` (body `{server_code}`) | `201 Envelope[BotMcpServerState]` |
| POST | `/{server_code}/activate` | `Envelope[BotMcpServerState]` |
| POST | `/{server_code}/deactivate` | `Envelope[BotMcpServerState]` |
| DELETE | `/{server_code}` | `Envelope[Deleted]` |

`BotMcpServer` = `server_code`, `name`, `description`, `active`, `is_default`.
`BotMcpServerState` = `server`, `changed` — the `SkillState` shape.

**Routing.** The new group is `{bot_id}`-parameterised, so it mounts with
`_GRANT_CHECKED_SUBGROUPS` in `openapi_v1/__init__.py`, before `bots_router`
(which stays last "for the wildcard-ordering rule"). No path in the new group
collides with `/openapi/v1/bots/mcp/**` — segment counts and literals differ on
every pair — but a test pins it, because the near-miss is not obvious to a
reader adding a route later.

## Service layer

New `core/mcp/services/bot_mcp_state_service.py`, with
`BotMcpStateServiceProtocol` in `api/`:

```
list_bot_servers(bot_id, owner_id)                    -> list[dict]
get_bot_server(bot_id, owner_id, code)                -> dict
add_bot_server(bot_id, owner_id, code)                -> {server, changed}
set_bot_server_active(bot_id, owner_id, code, active) -> {server, changed}
remove_bot_server(bot_id, owner_id, code)             -> {deleted}
```

Bot authorisation is `get_by_id_and_owner(bot_id, owner_id)` — a bot the caller
does not own is a masked `404`. The default set comes from
`skill_set_repo.get_default(user_id=owner_id, bolt_id=bot_id,
engine_type=bot["active_engine"])`; `None` is not-found, never an implicit
create (mirroring `local_skill_state_service.py:108`).

Marketplace validation on add reuses `market_service.get_mcp_detail` +
`is_network_type_visible`, raising `McpServerNotFoundError` from one site so
hidden and unknown are indistinguishable.

Every mutation ends with `sync_service.refresh_mcp_scope(user_id, entity_id,
bot_id, entity_type="staff", engine_type=bot["active_engine"])` —
`core/mcp/services/sync_service.py:325`, whose docstring already names this
caller: *"skill set 切换、激活/取消激活后调用"*. It declares the device whitelist
and updates the passport. On `success: false` the state write is rolled back and
the call fails, mirroring `write_unified_config` and `set_local_skill_active`.

## Persistence changes

`ac_skill_set_mcp` gains one column. Repository methods to add or extend in
`core/repository/implementations/skill_center/skill.py`:

- `add_mcp_to_set` — accept `is_active` (default `True`, so existing callers
  are unchanged).
- `set_mcp_active_in_set(skill_set_id, server_code, active)` — new.
- `get_mcp_servers_in_set` / `_for_env` — include `is_active`.

DDL, applied out of band per the repo's convention:

```sql
ALTER TABLE ac_skill_set_mcp
  ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1;
```

Additive with a default, so a code-first deploy is safe in either order.

## Admission

`openapi_v1/admission.py` — the table refuses anything absent, and
`test_principal_seam.py` fails if the surface and table disagree either way.

- `GET /openapi/v1/bots/mcp/configs` → `REFUSED`
- `DELETE /openapi/v1/bots/mcp/servers/{server_code}/config` → `REFUSED`

  Same reason as the config operations they join: account-level state, and a
  grant is consent to reach a bot, not to reconfigure an account.

- All six bot-scoped operations → `GRANT_CHECKED_OWN_BOT`

  **Deliberately narrower than `skills`,** which uses
  `GRANT_CHECKED_ADDRESSED_BOT` on its two collection routes because that group
  serves shared bots. MCP state here resolves through `get_by_id_and_owner`, so
  there is no addressed-owner dimension and the spec asks for own-bots-only.

## Gateway

`src/gateway/configs/application.yaml` `route_security` enumerates the
*refusals*, so the two new `REFUSED` config operations need rules there; the six
bot-scoped ones are already covered by `/openapi/v1/bots/**`. The gateway's
`tests/unit/core/authn/test_route_security.py` pins the agreement and is the
test that fails if only one side is edited.

`src/gateway/configs/schemas/bots.openapi.json` is regenerated via the existing
dump/publish scripts, not hand-edited.

## Files

**New**
- `openapi_v1/bot_mcp/{__init__,router,schemas}.py`
- `core/mcp/services/bot_mcp_state_service.py`
- `api/bot_mcp_state_service.py` (protocol)
- tests: `tests/community/adapters/http/openapi_v1/bot_mcp/`, plus config
  lifecycle cases beside the existing MCP suite

**Changed**
- `core/models/mcp.py` — the `is_active` column
- `core/repository/implementations/skill_center/skill.py` — three methods
- `core/skill_center/services/skill_set_service.py` — carry `active` through
  `get_set_mcp_servers`; filter it in `collect_bot_active_mcps` only
- `openapi_v1/mcp/{router,schemas}.py` — two operations
- `core/mcp/config_flow.py` — `delete_unified_config`, `list_unified_configs`
- `core/mcp/errors.py` — `McpDefaultServerNotRemovableError`
- `openapi_v1/__init__.py`, `openapi_v1/admission.py`, `di/modules/mcp_module.py`
- `src/gateway/configs/application.yaml` + regenerated `bots.openapi.json`
- `src/backend/docs/openapi-v1/README.md`

**Untouched, on purpose:** the sync push paths, `config_compose/collector.py`,
`write_unified_config`, `build_mcp_sync_payload`, and the internal `/api/mcp`
and `/api/skillsets` routers.

## Risks

1. **`collect_bot_active_mcps` is now filtered.** It is the input to the device
   whitelist and to artifact compose, so a wrong default silently removes
   capability from every existing bot. Mitigated by the `DEFAULT 1` column and a
   test asserting an unmigrated-shaped row (no explicit `is_active`) reads as
   active.
2. **Public MCPs share the default skill set with the workbench.** A workbench
   user could remove a row the public API created, through
   `DELETE /api/skillsets/{id}/mcps/{server_code}`. Accepted — it is the same
   bot and the same owner, and the public listing reflects reality on the next
   read.
3. **`refresh_mcp_scope` failure leaves inconsistent state.** Mitigated by
   rolling the state write back on `success: false`, with a test per mutation.
4. **Community/local device plugins are no-ops** (`has_mcp` / `sync_*` return
   `True`), so reconciliation failure cannot be exercised end to end in OSS
   tests. Cover it with a stubbed sync service at the service-test level.
