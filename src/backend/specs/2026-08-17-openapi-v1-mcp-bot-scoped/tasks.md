# Tasks — MCP config lifecycle + bot-scoped activation

Spec: `spec.md` · Plan: `plan.md`

Groups run in order. Within a group, tasks may be done together. Each group ends
green — `uv run pytest` on the touched suites plus lint — before the next starts.

---

## Group 1 — Config lifecycle: shared flow (Part 1 core)

- [ ] **1.1** Add `list_unified_configs(*, user_id, page, page_size,
      config_service)` to `core/mcp/config_flow.py`. Pages
      `UserMCPConfigRepository.list_by_user`, returns rows in the same
      `UnifiedConfig` shape `read_unified_config` returns, with a total.
      Masking stays `mask_api_key` — same function, not a second implementation.
- [ ] **1.2** Add `delete_unified_config(*, user_id, server_code, config_service,
      market_service, sync_service)` to the same module. Sequence, mirroring
      `write_unified_config` in reverse: confirm the server exists via
      `market_service.get_mcp_detail` (raise `McpServerNotFoundError` if not) →
      read the row for rollback → delete → `sync_service.remove_mcp_detail` →
      restore the row and raise `McpSyncFailedError` if the push fails.
      Deleting an absent row returns `deleted: False` without touching the
      device.
- [ ] **1.3** Unit tests for both: paging and totals; masking parity with
      `read_unified_config` including short keys; empty result is an empty page,
      not an error; delete-absent is success-with-`false`; unknown server code is
      not-found; push failure restores the row and raises.

## Group 2 — Config lifecycle: public endpoints

- [ ] **2.1** Add `McpConfigDeleted` (or reuse the shared `Deleted` contract if
      it fits) to `openapi_v1/mcp/schemas.py`.
- [ ] **2.2** Add `GET /openapi/v1/bots/mcp/configs` →
      `Envelope[Page[McpConfig]]` to `openapi_v1/mcp/router.py`. Uses
      `PageParamsDep` and `UserIdDep`, projects through the existing `_to_config`,
      carries `USER_SCOPED_403`.
- [ ] **2.3** Add `DELETE /openapi/v1/bots/mcp/servers/{server_code}/config` →
      `Envelope[Deleted]`. Same dependencies; `McpSyncFailedError` maps to `502`
      exactly as the write path does.
- [ ] **2.4** Add both to `openapi_v1/admission.py` as `REFUSED`, in the config
      block, with the reason comment extended rather than duplicated.
- [ ] **2.5** Endpoint tests: both operations; `403` when `user_id` names another
      user; `401` with no principal; app-only caller refused; delete leaves bot
      activation untouched.

## Group 3 — Bot-scoped state service

- [ ] **3.1** Add `BotMcpStateServiceProtocol` to
      `api/bot_mcp_state_service.py` with the five methods in `plan.md`.
- [ ] **3.2** Implement `core/mcp/services/bot_mcp_state_service.py`. Resolve the
      bot with `get_by_id_and_owner` (masked `404`); resolve the default set with
      `skill_set_repo.get_default(user_id=owner_id, bolt_id=bot_id,
      engine_type=bot["active_engine"])`, treating `None` as not-found.
- [ ] **3.3** Implement the merged read: default-set associations
      (`get_mcp_servers_in_set`) unioned with `get_default_mcp_servers(engine,
      template_type)`, each marked `is_default`, with `active` = not present in
      `get_excluded_mcps`. Dedupe by `server_code`, association wins.
- [ ] **3.4** Implement the four mutations per the plan's table
      (`add_mcp_to_set` / `remove_default_mcp_exclusion` /
      `add_default_mcp_exclusion` / `remove_mcp_from_set`), each returning
      `changed` computed against the pre-state so a no-op reports `false`.
- [ ] **3.5** Add `McpDefaultServerNotRemovableError` to `core/mcp/errors.py` and
      raise it from `remove_bot_server` for an engine default.
- [ ] **3.6** Validate `server_code` on add via `market_service.get_mcp_detail` +
      `is_network_type_visible`, raising `McpServerNotFoundError` from one site.
- [ ] **3.7** End every mutation with `sync_service.refresh_mcp_scope(...)`;
      on `success: false` roll the state write back and raise. One test per
      mutation for the rollback, using a stubbed sync service (the community
      device plugins are no-ops and cannot fail).
- [ ] **3.8** Bind the service in `di/modules/mcp_module.py`.
- [ ] **3.9** Service tests: add is idempotent; activate/deactivate idempotent
      with correct `changed`; activating a server not on the bot is not-found;
      defaults read active until excluded; remove-default is refused;
      remove leaves `ac_user_mcp_config` intact.

## Group 4 — Bot-scoped endpoints

- [ ] **4.1** `openapi_v1/bot_mcp/schemas.py` — `BotMcpServer`,
      `BotMcpServerState`, `BotMcpServerAdd`, with `extra="forbid"` on the body
      model (matching the `sync_mode`-rejection precedent from PR #610).
- [ ] **4.2** `openapi_v1/bot_mcp/router.py` — the six operations at prefix
      `/openapi/v1/bots/{bot_id}/mcp`, using `BotIdPath`, `UserIdDep`,
      `PageParamsDep`, `@envelope_errors`. `POST ""` answers `201`.
- [ ] **4.3** Mount the group in `openapi_v1/__init__.py` with
      `_GRANT_CHECKED_SUBGROUPS` — before `bots_router`.
- [ ] **4.4** Add all six to `admission.py` as `GRANT_CHECKED_OWN_BOT`, with the
      comment recording why this is narrower than `skills`.
- [ ] **4.5** Endpoint tests: full lifecycle add → deactivate → activate →
      remove; unowned `bot_id` is `404` and byte-identical to an unknown one;
      unknown/hidden `server_code` is `404`; `403` on a mismatched `user_id`;
      unknown body field is `422`.
- [ ] **4.6** A routing test asserting `/openapi/v1/bots/mcp/servers`,
      `/openapi/v1/bots/mcp/tenants` and `/openapi/v1/bots/mcp/configs` still
      resolve to the account-level group with the new `{bot_id}` group mounted.

## Group 5 — Edge and gateway agreement

- [ ] **5.1** Add `route_security` rules for the two new `REFUSED` config
      operations in `src/gateway/configs/application.yaml`.
- [ ] **5.2** Update the gateway's
      `tests/unit/core/authn/test_route_security.py` expectations.
- [ ] **5.3** Regenerate `src/gateway/configs/schemas/bots.openapi.json` with the
      existing dump/publish scripts — do not hand-edit.
- [ ] **5.4** Confirm `test_principal_seam.py` passes: surface and admission
      table agree in both directions.

## Group 6 — Docs and close-out

- [ ] **6.1** Update the `mcp` section of `src/backend/docs/openapi-v1/README.md`
      — endpoint table (6 → 14), the "addresses no bot at all" note in *Per-bot
      device surfaces that are still draft-only*, which is now false, and the
      delivered-decisions paragraph.
- [ ] **6.2** Record the two design decisions (default-set reuse, user-scoped
      credential) where a future reader meets them: a module docstring in
      `bot_mcp_state_service.py` pointing at `plan.md`.
- [ ] **6.3** Full module gates: `OCB_PRE_PUSH_RUN_CI=1` per the `AGENTS.md`
      pre-push contract.
- [ ] **6.4** PR titled `feat(openapi-v1): MCP config lifecycle and bot-scoped
      activation`, body using the `Problem` / `Solution` / `Validation` sections
      from `.github/pull_request_template.md`.

---

## Notes for the implementer

- **Do not touch** `collect_bot_active_mcps`, the sync push paths,
  `config_compose/collector.py`, `write_unified_config`, or
  `build_mcp_sync_payload`. The design's value is that they need no changes; a
  diff touching them means Decision 1 drifted.
- **"Skill set" must not appear** in any path, parameter, schema field or
  description on the public surface. It is expected in the storage layer.
- `AGENTS.md`: no `T | None` unless `None` is a real contract state.
