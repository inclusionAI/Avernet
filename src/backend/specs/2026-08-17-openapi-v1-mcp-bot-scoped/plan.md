# Plan — MCP config lifecycle + bot-scoped activation

Spec: `spec.md`. Two parts, one PR — they share the category, the docs entry and
the admission-table edit, and shipping activation without the config lifecycle
publishes state a caller cannot inspect or undo.

## Decision 1 — do exactly what public `skills` does

**Chosen.** Per-MCP activation is **membership in the bot's default skill set,
with an exclusion row as the off switch** — the identical mechanism
`set_local_skill_active` has been running in production for skills.

| Public verb | Storage effect | Skills equivalent |
|---|---|---|
| add | `add_mcp_to_set` on the default set, **plus** an exclusion row so it lands inactive | `_ensure_default_set_membership` |
| activate | `remove_all_default_mcp_exclusions` | `remove_all_default_skill_exclusions` |
| deactivate | `add_default_mcp_exclusion` | `add_default_skill_exclusion` |
| remove | `remove_mcp_from_set` + clear its exclusions | `delete_skill` |

No DDL. No new table. No new column.

### Why the default skill set, and not a dedicated one

Skill-set activation is **exclusive**. `set_active_skill_set`
(`core/repository/implementations/skill_center/skill.py:2017-2087`) runs two
statements: clear `is_active = 0` on every non-default skill set for the
(user, bot, engine), then activate the target. It is "switch to this skill set",
not "turn this one on".

Its clear query filters on `bolt_id`, `env`, `is_default == False`,
`engine_type`, `user_id` — and nothing else. There is no marker it respects. So
any skill set the public API owned would be silently switched off the moment
anyone changed skill sets in the workbench, taking every MCP on it down with no
signal to either surface. One dedicated set per MCP fails the same way, and
additionally makes only one MCP activatable at a time if activation goes through
`set_active_skill_set` at all.

The default set is immune: `get_all_active_skill_sets:2151-2161` appends it
separately from the `is_active == True` query, so the exclusivity sweep never
reaches it. This is exactly why `skills` operates there and never touches
skill-set activation.

### The one code change: a missing filter

The two halves were built as twins, and the MCP half is missing one line.

**Skills — filters real member rows by exclusions** (`skill.py:545-562`):

```python
rows = skill_sets.get_skills_in_set_for_env(str(skill_set["id"]), env=env)  # real rows
if skill_set.get("is_default"):
    excluded = set(skill_sets.get_excluded_skills(...))
    rows = [row for row in rows if int(row.get("id", 0)) not in excluded]   # <-- filters them
```

**MCP — filters only the synthesised defaults**, never `associations`
(`skill_set_service.py`, `get_set_mcp_servers`):

```python
for code in default_codes:          # engine-supplied codes ONLY
    if code in excluded_codes:
        continue                    # skip *synthesising* this default
    if code not in db_codes:
        associations.append({...})
```

So today, writing an exclusion for an MCP that has a real `ac_skill_set_mcp` row
does nothing at all — the row is already in `db_codes` and survives untouched.

The fix is to apply `excluded_codes` to `associations` too, matching skills.

**This is a behaviour change on the internal surface, and it is deliberate.**
`remove_mcp_from_skill_set` already writes an exclusion *instead of deleting*
when the skill set is the default one (`skill_set_service.py:1583-1590`, "默认能
力集：写入排除表（用户隔离）"), so the platform already treats that table as the
removal mechanism there. Any existing row+exclusion pair is a remove that
silently did not take; honouring it is doing what the caller asked. Called out
here because it is visible, not incidental.

### Consequence: engine defaults cannot be removed

A default MCP is synthesised per request from `get_default_mcp_servers`; there is
no row to delete, so "not on the bot" is not a state it can hold. `DELETE
.../mcp/{server_code}` on an engine default raises
`McpDefaultServerNotRemovableError` → `409`, pointing at deactivate. Activate and
deactivate work on defaults normally, through the same exclusion rows.

### Repository parity gap to close

Skills has `remove_all_default_skill_exclusions(user_id, bot_id, skill_id)` —
note: **no `skill_set_id`**, so it clears exclusions left behind by a *former*
default set. MCP has only the per-set `remove_default_mcp_exclusion`. Add the
twin, `remove_all_default_mcp_exclusions(user_id, bot_id, server_code)`.

This matters because `collect_bot_mcps` reads exclusions with
`get_all_excluded_mcps(user_id, bot_id)` — across all sets. Without the "all"
variant, an exclusion stranded on a former default set would keep an MCP off
forever and activate would appear to do nothing. It is the same reason the skills
side has it.

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

## Surface

### Part 1 — account-level, existing group (`openapi_v1/mcp/router.py`)

| Method | Path | Success |
|---|---|---|
| GET | `/openapi/v1/bots/mcp/configs` | `Envelope[Page[McpConfig]]` |
| DELETE | `/openapi/v1/bots/mcp/servers/{server_code}/config` | `Envelope[Deleted]` |

`GET configs` pages `UserMCPConfigRepository.list_by_user`, projecting through
the existing `_to_config` so masking is the same function, not the same intent.

`DELETE config` mirrors `write_unified_config` in reverse and reuses its
atomicity: confirm the server exists in the marketplace (404 if not), read the
row for rollback, delete it, push the removal via
`MCPSyncServiceProtocol.remove_mcp_detail`, restore the row and fail (502) if the
push fails. Deleting an absent row is a success with `deleted: false`.

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
every pair — but a test pins it, because the near-miss is not obvious to a reader
adding a route later.

## Service layer

New `core/mcp/services/bot_mcp_state_service.py`, modelled directly on
`local_skill_state_service.py`, with `BotMcpStateServiceProtocol` in `api/`:

```
list_bot_servers(bot_id, owner_id)                    -> list[dict]
get_bot_server(bot_id, owner_id, code)                -> dict
add_bot_server(bot_id, owner_id, code)                -> {server, changed}
set_bot_server_active(bot_id, owner_id, code, active) -> {server, changed}
remove_bot_server(bot_id, owner_id, code)             -> {deleted}
```

Bot authorisation is `get_by_id_and_owner(bot_id, owner_id)` — a bot the caller
does not own is a masked `404`. The container is
`skill_set_repo.get_default(user_id=owner_id, bolt_id=bot_id,
engine_type=bot["active_engine"])`; `None` is not-found, never an implicit create
(mirroring `local_skill_state_service.py:108`).

**Membership is written through the repository, not the service.**
`skill_set_service.add_mcp_to_skill_set` refuses the default set outright
(`raise ValueError("默认技能集不允许修改")`), so `add_bot_server` calls
`skill_set_repo.add_mcp_to_set` directly — exactly as skills'
`_ensure_default_set_membership` calls `add_skill_to_set` rather than going
through the service.

No skills-pool edit guard: MCP activation writes rows, it does not restructure a
filesystem layout.

Marketplace validation on add reuses `market_service.get_mcp_detail` +
`is_network_type_visible`, raising `McpServerNotFoundError` from one site so
hidden and unknown are indistinguishable.

Every mutation ends with `sync_service.refresh_mcp_scope(user_id, entity_id,
bot_id, entity_type="staff", engine_type=bot["active_engine"])` —
`core/mcp/services/sync_service.py:325`, whose docstring already names this
caller: *"skill set 切换、激活/取消激活后调用"*. It declares the device whitelist
and updates the passport. On `success: false` the state write is rolled back and
the call fails, mirroring `write_unified_config` and `set_local_skill_active`.

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
`tests/unit/core/authn/test_route_security.py` pins the agreement and is the test
that fails if only one side is edited.

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
- `core/skill_center/services/skill_set_service.py` — `get_set_mcp_servers`
  applies `excluded_codes` to `associations` (the parity fix)
- `core/repository/implementations/skill_center/skill.py` —
  `remove_all_default_mcp_exclusions`
- `openapi_v1/mcp/{router,schemas}.py` — two operations
- `core/mcp/config_flow.py` — `delete_unified_config`, `list_unified_configs`
- `core/mcp/errors.py` — `McpDefaultServerNotRemovableError`
- `openapi_v1/__init__.py`, `openapi_v1/admission.py`, `di/modules/mcp_module.py`
- `src/gateway/configs/application.yaml` + regenerated `bots.openapi.json`
- `src/backend/docs/openapi-v1/README.md`

**Untouched, on purpose:** `collect_bot_active_mcps` and `collect_bot_mcps`
(they already read exclusions; only the function beneath them is corrected), the
sync push paths, `config_compose/collector.py`, `write_unified_config`,
`build_mcp_sync_payload`, and the internal `/api/mcp` and `/api/skillsets`
routers. No schema change anywhere.

## Risks

1. **The filter fix changes internal read behaviour.** An existing
   row+exclusion pair goes from "MCP still listed" to "MCP gone". Bounded to
   pairs created by `remove_mcp_from_skill_set` on a default set — i.e. removes
   that silently did not take. Mitigated by a test asserting the new behaviour
   and by a note in the docs' decision log. If this turns out to be unacceptable,
   the fallback is an `is_active` column on `ac_skill_set_mcp` (DDL), recorded
   here so the alternative is not re-derived.
2. **Public MCPs share the default skill set with the workbench.** A workbench
   user could remove a row the public API created. Accepted — same bot, same
   owner — and the public listing reflects reality on the next read. Identical to
   the position `skills` is already in.
3. **`refresh_mcp_scope` failure leaves inconsistent state.** Mitigated by
   rolling the state write back on `success: false`, with a test per mutation.
4. **Community/local device plugins are no-ops** (`has_mcp` / `sync_*` return
   `True`), so reconciliation failure cannot be exercised end to end in OSS
   tests. Cover it with a stubbed sync service at the service-test level.
