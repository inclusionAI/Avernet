# Plan — MCP config lifecycle + bot-scoped activation

Spec: `spec.md`. Two parts, one PR — they share the category, the docs entry and
the admission-table edit, and shipping activation without the config lifecycle
publishes state a caller cannot inspect or undo.

## Decision 1 — activation reuses the default skill set. No new table.

**Chosen.** "On the bot" means present in the bot's *default skill set* MCP
associations (`ac_skill_set_mcp_server`) or supplied by the engine defaults.
"Active" means present and *not* carrying a row in
`ac_default_skillset_mcp_exclusion`.

| Public verb | Storage effect |
|---|---|
| add | `add_mcp_to_set(default_set.id, …)`, then clear any exclusion |
| activate | `remove_default_mcp_exclusion(...)` |
| deactivate | `add_default_mcp_exclusion(...)` |
| remove | `remove_mcp_from_set(...)` + clear its exclusion |

**Why, and it is the whole reason this slice is small.** `collect_bot_active_mcps`
(`core/skill_center/services/skill_set_service.py:1828`) already computes exactly
this: active skill sets' MCPs + engine defaults − exclusions. It is the *single*
input to both device-whitelist declaration (`_declare_mcp_scope` →
`plugin.sync_all_mcp_servers`) and artifact compose
(`config_compose/services/collector.py:195`). Writing through the tables it
already reads means **the sync and compose paths need no changes at all**.

A new `ac_bot_mcp_activation` table was the obvious alternative and is rejected:
it would require editing `collect_bot_active_mcps` to union a second source,
which changes the input to both consumers above, and would leave two overlapping
representations of "is this MCP on this bot" that must agree forever.

**The precedent is exact.** Public `skills` publishes `activate`/`deactivate`
with no skill set on the contract, by resolving the default set inside the
service — `core/skill_center/services/local_skill_state_service.py:103-135`
(`get_default` → `_ensure_default_set_membership` → `_write_desired_state`
against `DefaultSkillsetSkillExclusion`). Every method that flow needs has an MCP
twin already implemented in
`core/repository/implementations/skill_center/skill.py`: `get_default:1193`,
`add_mcp_to_set:1594`, `get_mcp_servers_in_set:1630`, `remove_mcp_from_set:1660`,
`add_default_mcp_exclusion:1676`, `remove_default_mcp_exclusion:1757`,
`get_excluded_mcps:1781`, `get_all_excluded_mcps:1799`.

The user's constraint — no skill-set concept — is about the **contract**, and
this satisfies it: the word never reaches a path, a parameter or a schema.

### Consequence: engine defaults cannot be removed

A default MCP is synthesised per request in `collect_bot_mcps` from
`get_default_mcp_servers(engine, template_type)`; there is no row to delete. The
only representable state for it is excluded-or-not. So `DELETE
.../mcp/{server_code}` on an engine default raises a domain error
(`McpDefaultServerNotRemovableError` → `409`) telling the caller to deactivate
instead. This refines the spec's "removing takes it out of the listing", which
holds for added servers only.

## Decision 2 — the credential stays user-scoped

`ac_user_mcp_config` stays keyed `(user_id, server_code)`: one credential per
user per server, shared by that user's bots. Activation is the per-bot axis; the
credential is not.

Two reasons. `build_mcp_sync_payload`
(`core/mcp/services/config_service.py:179-218`) looks the row up by exactly that
key on every device sync and every artifact compose — making it per-bot changes
that lookup and every call site, for a data model the third party does not have
(an API key is an account fact at the MCP server, not a per-bot fact). And it
keeps the two axes orthogonal, which is what lets `DELETE config` leave
activation alone and `DELETE bot server` leave the credential alone.

## Surface

### Part 1 — account-level, existing group (`openapi_v1/mcp/router.py`)

| Method | Path | Success |
|---|---|---|
| GET | `/openapi/v1/bots/mcp/configs` | `Envelope[Page[McpConfig]]` |
| DELETE | `/openapi/v1/bots/mcp/servers/{server_code}/config` | `Envelope[Deleted]` |

`GET configs` pages `UserMCPConfigRepository.list_by_user`, projecting each row
through the existing `_to_config` so masking is the same function, not the same
intent.

`DELETE config` mirrors `write_unified_config`'s sequence in reverse and reuses
its atomicity: confirm the server exists in the marketplace (404 if not), read
the row for rollback, delete it, push the removal via
`MCPSyncServiceProtocol.remove_mcp_detail`, and restore the row if the push
fails (502). Deleting an absent row is a success with `deleted: false`.

New shared flow functions go in `core/mcp/config_flow.py` beside
`read_unified_config` / `write_unified_config`: `delete_unified_config` and
`list_unified_configs`. The internal router does not have to adopt them; the
extraction exists so both surfaces *can* share one implementation.

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
collides with `/openapi/v1/bots/mcp/**` — the segment counts and literals differ
on every pair — but a test pins it, because the near-miss is not obvious to a
reader adding a route later.

## Service layer

New `core/mcp/services/bot_mcp_state_service.py` with
`BotMcpStateServiceProtocol` in `api/`, modelled on
`local_skill_state_service.py` but without the skills-pool edit guard (MCP
activation writes rows; it does not restructure a filesystem layout).

```
list_bot_servers(bot_id, owner_id)          -> list[dict]   # merged view + active
get_bot_server(bot_id, owner_id, code)      -> dict
add_bot_server(bot_id, owner_id, code)      -> {server, changed}
set_bot_server_active(bot_id, owner_id, code, active) -> {server, changed}
remove_bot_server(bot_id, owner_id, code)   -> {deleted}
```

Every mutation ends with `refresh_mcp_scope(user_id, entity_id, bot_id,
entity_type="staff", engine_type=bot.active_engine)` —
`core/mcp/services/sync_service.py:325`, whose docstring already names this
caller: *"skill set 切换、激活/取消激活后调用"*. It declares the device whitelist
and updates the passport. If it returns `success: false`, the state write is
rolled back and the call fails, mirroring both `write_unified_config` and
`set_local_skill_active`.

Authorisation is `get_by_id_and_owner(bot_id, owner_id)` — a bot the caller does
not own is a masked 404, per `AdmissionMode.GRANT_CHECKED_OWN_BOT`.

Marketplace validation on add reuses `market_service.get_mcp_detail` +
`is_network_type_visible`, raising the existing `McpServerNotFoundError` from one
site so hidden and unknown are indistinguishable.

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
  serves shared bots. MCP state here is resolved through
  `get_by_id_and_owner`, so there is no addressed-owner dimension and the spec
  asks for own-bots-only.

## Gateway

`src/gateway/configs/application.yaml` `route_security` enumerates the
*refusals*, so the two new `REFUSED` config operations need rules there; the six
bot-scoped ones are already covered by `/openapi/v1/bots/**`. The gateway's
`tests/unit/core/authn/test_route_security.py` pins the agreement and is the test
that fails if only one side is edited.

The published schema `src/gateway/configs/schemas/bots.openapi.json` is
regenerated via the existing dump/publish scripts, not hand-edited.

## Files

**New**
- `openapi_v1/bot_mcp/{__init__,router,schemas}.py`
- `core/mcp/services/bot_mcp_state_service.py`
- `api/bot_mcp_state_service.py` (protocol)
- tests: `tests/community/adapters/http/openapi_v1/bot_mcp/`, plus config
  lifecycle cases beside the existing MCP suite

**Changed**
- `openapi_v1/mcp/router.py` — two operations
- `openapi_v1/mcp/schemas.py` — list/delete response models
- `core/mcp/config_flow.py` — `delete_unified_config`, `list_unified_configs`
- `core/mcp/errors.py` — `McpDefaultServerNotRemovableError`
- `openapi_v1/__init__.py` — mount the new group
- `openapi_v1/admission.py` — eight entries
- `di/modules/mcp_module.py` — bind the new service
- `src/gateway/configs/application.yaml` + regenerated `bots.openapi.json`
- `src/backend/docs/openapi-v1/README.md` — the `mcp` row and its table

**Untouched, on purpose:** `collect_bot_active_mcps`, `sync_service`'s push
paths, `config_compose/collector.py`, `write_unified_config`,
`build_mcp_sync_payload`, and the internal `/api/mcp` and `/api/skillsets`
routers.

## Risks

1. **Writing through skill-set tables from a surface that denies skill sets.**
   Mitigated by precedent (`skills` does exactly this) and by keeping the leak
   surface at zero — no path, parameter or field names one. The honest cost is
   that a reader of the storage sees skill sets; the plan's first section is
   where that is explained.
2. **`refresh_mcp_scope` failure leaves inconsistent state.** Mitigated by
   rolling the state write back on `success: false`, the same shape
   `set_local_skill_active` uses, with an explicit test per mutation.
3. **Default-set resolution returning `None`.** `local_skill_state_service:108`
   treats it as not-found; do the same rather than creating a set implicitly.
4. **Community/local device plugins are no-ops** (`has_mcp`/`sync_*` return
   `True`), so reconciliation failures cannot be exercised end-to-end in OSS
   tests. Cover them with a stubbed sync service at the service-test level.
