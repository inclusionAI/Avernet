# Tasks — MCP config lifecycle + bot-scoped activation

Spec: `spec.md` · Plan: `plan.md`

Groups run in order. Within a group, tasks may be done together. Each group ends
green — `uv run pytest` on the touched suites plus lint — before the next starts.

---

## Group 1 — Config lifecycle: shared flow (Part 1 core)

- [ ] **1.1** Add `list_unified_configs(*, user_id, page, page_size,
      config_service)` to `core/mcp/config_flow.py`. Pages
      `UserMCPConfigRepository.list_by_user`, returns rows in the same
      `UnifiedConfig` shape `read_unified_config` returns, with a total. Masking
      stays `mask_api_key` — the same function, not a second implementation.
- [ ] **1.2** Add `delete_unified_config(*, user_id, server_code, config_service,
      market_service, sync_service)`. Sequence, mirroring `write_unified_config`
      in reverse: confirm the server exists via `market_service.get_mcp_detail`
      (raise `McpServerNotFoundError` if not) → read the row for rollback →
      delete → `sync_service.remove_mcp_detail` → restore the row and raise
      `McpSyncFailedError` if the push fails. An absent row returns
      `deleted: False` without touching the device.
- [ ] **1.3** Unit tests: paging and totals; masking parity with
      `read_unified_config` including short keys; empty result is an empty page;
      delete-absent is success-with-`false`; unknown server code is not-found;
      push failure restores the row and raises.

## Group 2 — Config lifecycle: public endpoints

- [ ] **2.1** Response models in `openapi_v1/mcp/schemas.py` (reuse the shared
      `Deleted` contract if it fits).
- [ ] **2.2** `GET /openapi/v1/bots/mcp/configs` → `Envelope[Page[McpConfig]]`,
      using `PageParamsDep` + `UserIdDep`, projecting through the existing
      `_to_config`, carrying `USER_SCOPED_403`.
- [ ] **2.3** `DELETE /openapi/v1/bots/mcp/servers/{server_code}/config` →
      `Envelope[Deleted]`. `McpSyncFailedError` maps to `502`, as on the write
      path.
- [ ] **2.4** Add both to `admission.py` as `REFUSED`, in the config block,
      extending the existing reason comment rather than duplicating it.
- [ ] **2.5** Endpoint tests: both operations; `403` when `user_id` names another
      user; `401` with no principal; app-only caller refused; delete leaves bot
      activation untouched.

## Group 3 — Persistence: per-MCP active state

- [ ] **3.1** Add `is_active = Column(Boolean, nullable=False, server_default="1")`
      to `SkillSetMCPServer` in `core/models/mcp.py`. `server_default`, not a
      Python `default=`, so `create_all` emits the same `DEFAULT 1` the
      out-of-band DDL applies and non-ORM inserts are covered.
- [ ] **3.2** Record the DDL in the spec directory and in the docs' migration
      list: `ALTER TABLE ac_skill_set_mcp ADD COLUMN is_active TINYINT(1) NOT
      NULL DEFAULT 1;`. Additive with a default — safe in either deploy order.
- [ ] **3.3** `core/repository/implementations/skill_center/skill.py`:
      `add_mcp_to_set` accepts `is_active` defaulting to `True`;
      `get_mcp_servers_in_set` and `_for_env` include the field; new
      `set_mcp_active_in_set(skill_set_id, server_code, active) -> bool`.
- [ ] **3.4** `get_set_mcp_servers` carries the field out as `active`.
      Synthesised engine defaults get `active = True` — they have no row, and
      exclusion already removed the ones the caller turned off.
- [ ] **3.5** Filter `active is True` in **`collect_bot_active_mcps` only**.
      `collect_bot_mcps` (the all-MCPs variant) must not filter — it is the
      listing source.
- [ ] **3.6** Regression tests: a row created without an explicit `is_active`
      reads as active (this is the guard against silently removing capability
      from every existing bot); an inactive row is absent from
      `collect_bot_active_mcps` and present in `collect_bot_mcps`; the internal
      skill-set API's behaviour is byte-identical.

## Group 4 — Bot-scoped state service

- [ ] **4.1** `BotMcpStateServiceProtocol` in `api/bot_mcp_state_service.py` —
      the five methods in `plan.md`.
- [ ] **4.2** Implement `core/mcp/services/bot_mcp_state_service.py`. Resolve the
      bot with `get_by_id_and_owner` (masked `404`); resolve the container with
      `skill_set_repo.get_default(user_id=owner_id, bolt_id=bot_id,
      engine_type=bot["active_engine"])`, treating `None` as not-found — never an
      implicit create.
- [ ] **4.3** Merged read: default-set rows (`get_mcp_servers_in_set`, carrying
      `active`) unioned with `get_default_mcp_servers(engine, template_type)`
      minus `get_excluded_mcps`. Mark each `is_default`. Dedupe by `server_code`,
      row wins.
- [ ] **4.4** `add_bot_server` — validate via `market_service.get_mcp_detail` +
      `is_network_type_visible` (one raise site), then `add_mcp_to_set(...,
      is_active=False)`. Already present → `changed: false`.
- [ ] **4.5** `set_bot_server_active` — for a stored row, `set_mcp_active_in_set`;
      for an engine default, `remove_default_mcp_exclusion` (activate) /
      `add_default_mcp_exclusion` (deactivate). `changed` computed against the
      pre-state. A server not on the bot is not-found.
- [ ] **4.6** `remove_bot_server` — `remove_mcp_from_set` for a stored row;
      raise the new `McpDefaultServerNotRemovableError` (`409`, added to
      `core/mcp/errors.py`) for an engine default. Absent → `deleted: false`.
- [ ] **4.7** End every mutation with `sync_service.refresh_mcp_scope(...)`; on
      `success: false`, roll the state write back and raise. One rollback test per
      mutation, with a stubbed sync service — the community device plugins are
      no-ops and cannot fail.
- [ ] **4.8** Bind the service in `di/modules/mcp_module.py`.
- [ ] **4.9** Service tests: add is idempotent and lands **inactive**;
      activate/deactivate idempotent with correct `changed`; activating a server
      not on the bot is not-found; defaults read active until excluded;
      remove-default is `409`; remove leaves `ac_user_mcp_config` intact.

## Group 5 — Bot-scoped endpoints

- [ ] **5.1** `openapi_v1/bot_mcp/schemas.py` — `BotMcpServer`,
      `BotMcpServerState`, `BotMcpServerAdd`, with `extra="forbid"` on the body
      model (the `sync_mode`-rejection precedent from PR #610).
- [ ] **5.2** `openapi_v1/bot_mcp/router.py` — the six operations at prefix
      `/openapi/v1/bots/{bot_id}/mcp`, using `BotIdPath`, `UserIdDep`,
      `PageParamsDep`, `@envelope_errors`. `POST ""` answers `201`.
- [ ] **5.3** Mount with `_GRANT_CHECKED_SUBGROUPS` in `openapi_v1/__init__.py`
      — before `bots_router`.
- [ ] **5.4** Add all six to `admission.py` as `GRANT_CHECKED_OWN_BOT`, with the
      comment recording why this is narrower than `skills`.
- [ ] **5.5** Endpoint tests: full lifecycle add → activate → deactivate →
      remove; an added server reads `active: false` until activated; unowned
      `bot_id` is `404` and byte-identical to an unknown one; unknown/hidden
      `server_code` is `404`; `403` on a mismatched `user_id`; unknown body field
      is `422`.
- [ ] **5.6** Routing test: `/openapi/v1/bots/mcp/servers`, `/…/tenants` and
      `/…/configs` still resolve to the account-level group with the new
      `{bot_id}` group mounted.

## Group 6 — Edge and gateway agreement

- [ ] **6.1** `route_security` rules for the two new `REFUSED` config operations
      in `src/gateway/configs/application.yaml`.
- [ ] **6.2** Update the gateway's `tests/unit/core/authn/test_route_security.py`.
- [ ] **6.3** Regenerate `src/gateway/configs/schemas/bots.openapi.json` with the
      existing dump/publish scripts — do not hand-edit.
- [ ] **6.4** Confirm `test_principal_seam.py` passes: surface and admission
      table agree in both directions.

## Group 7 — Docs and close-out

- [ ] **7.1** Update `src/backend/docs/openapi-v1/README.md`: the `mcp` endpoint
      table (6 → 14), the "**MCP** addresses no bot at all" note under *Per-bot
      device surfaces that are still draft-only* (now false), the
      delivered-decisions paragraph, and the DDL list with 3.2's `ALTER TABLE`.
- [ ] **7.2** Module docstring in `bot_mcp_state_service.py` recording the three
      decisions and pointing at `plan.md` — in particular why the exclusion table
      is *not* the activation mechanism, so the correction is not re-litigated.
- [ ] **7.3** Full module gates: `OCB_PRE_PUSH_RUN_CI=1` per the `AGENTS.md`
      pre-push contract.
- [ ] **7.4** PR titled `feat(openapi-v1): MCP config lifecycle and bot-scoped
      activation`, body using the `Problem` / `Solution` / `Validation` sections
      from `.github/pull_request_template.md`.

---

## Notes for the implementer

- **The one change to the shared collect path is 3.5.** Everything else in
  `skill_set_service.py` is passing a field through. A diff that touches the sync
  push paths, `config_compose/collector.py`, `write_unified_config` or
  `build_mcp_sync_payload` means the design drifted.
- **`DEFAULT 1` on `is_active` is the safety property.** Task 3.6's first test is
  what stands between this change and silently disabling every MCP on every
  existing bot.
- **The exclusion table is only for engine defaults.** It is not a general on/off
  switch — see Decision 1 in `plan.md`. Reaching for it on a stored row is the
  mistake this plan was corrected to avoid.
- **"Skill set" must not appear** in any path, parameter, schema field or
  description on the public surface. It is expected in the storage layer.
- `AGENTS.md`: no `T | None` unless `None` is a real contract state.
