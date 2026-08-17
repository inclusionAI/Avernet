# Tasks — MCP config lifecycle + bot-scoped activation

Spec: `spec.md` · Plan: `plan.md`

Groups run in order. Within a group, tasks may be done together. Each group ends
green — `uv run pytest` on the touched suites plus lint — before the next starts.

---

## Group 1 — Config lifecycle: shared flow (Part 1 core)

- [x] **1.1** Add `list_unified_configs(*, user_id, page, page_size,
      config_service)` to `core/mcp/config_flow.py`. Pages
      `UserMCPConfigRepository.list_by_user`, returns rows in the same
      `UnifiedConfig` shape `read_unified_config` returns, with a total. Masking
      stays `mask_api_key` — the same function, not a second implementation.
- [x] **1.2** Add `delete_unified_config(*, user_id, server_code, entity_id,
      entity_type, config_service, market_service, sync_service)`. Sequence,
      mirroring `write_unified_config` in reverse: confirm the server exists via
      `market_service.get_mcp_detail` (raise `McpServerNotFoundError` if not) →
      delete the row, keeping it for rollback → push → restore the row and raise
      `McpSyncFailedError` if the push fails. An absent row returns `False`
      without touching the device.

      **Corrected during implementation:** the push is
      `sync_mcp_detail_to_all_bots(..., api_key=None)`, *not*
      `remove_mcp_detail` as originally written. `remove_mcp_detail` un-installs
      the MCP from the device, which would make deleting a credential silently
      deactivate the server on every bot — contradicting the spec's "deleting a
      configuration does not deactivate the server on any bot". Re-syncing with
      the credential cleared is what expresses "the server stays, its credential
      does not". The sequence and its atomicity are unchanged.
- [x] **1.3** Unit tests: paging and totals; masking parity with
      `read_unified_config` including short keys; empty result is an empty page;
      delete-absent is success-with-`false`; unknown server code is not-found;
      push failure restores the row and raises.

## Group 2 — Config lifecycle: public endpoints

- [x] **2.1** Response models in `openapi_v1/mcp/schemas.py` (reuse the shared
      `Deleted` contract if it fits).
- [x] **2.2** `GET /openapi/v1/bots/mcp/configs` → `Envelope[Page[McpConfig]]`,
      using `PageParamsDep` + `UserIdDep`, projecting through the existing
      `_to_config`, carrying `USER_SCOPED_403`.
- [x] **2.3** `DELETE /openapi/v1/bots/mcp/servers/{server_code}/config` →
      `Envelope[Deleted]`. `McpSyncFailedError` maps to `502`, as on the write
      path.
- [x] **2.4** Add both to `admission.py` as `REFUSED`, in the config block,
      extending the existing reason comment rather than duplicating it.
- [x] **2.5** Endpoint tests: both operations; `403` when `user_id` names another
      user; `401` with no principal; app-only caller refused; delete leaves bot
      activation untouched.

## Group 3 — Skills parity in the MCP storage layer

This group makes the MCP half behave like the skill half. No schema change.

- [x] **3.1** Add `remove_all_default_mcp_exclusions(user_id, bot_id,
      server_code) -> bool` to
      `core/repository/implementations/skill_center/skill.py`, mirroring
      `remove_all_default_skill_exclusions:1912` — **no `skill_set_id`**, so it
      clears exclusions stranded on a former default set. Without it,
      `collect_bot_mcps`' `get_all_excluded_mcps` read would keep an MCP off
      forever and activate would appear to do nothing.
- [x] **3.2** In `get_set_mcp_servers` (`core/skill_center/services/
      skill_set_service.py`), apply `excluded_codes` to `associations` as well as
      to the synthesised default codes — the missing line that makes exclusions
      mean the same thing they mean for skills (`skill.py:545-562`).
- [x] **3.3** Tests for the corrected filter: an exclusion on a **stored** row
      now hides it (this is the behaviour change — assert it deliberately); an
      exclusion on a synthesised default still hides it; no exclusion leaves both
      visible; `collect_bot_active_mcps` and `collect_bot_mcps` both reflect it.
- [x] **3.4** Confirm the internal surface still behaves as its own suite
      expects: run the existing skill-set and MCP test suites unmodified. A
      needed change there is a signal to re-read Decision 1's risk 1 before
      proceeding.

## Group 4 — Bot-scoped state service

- [x] **4.1** `BotMcpStateServiceProtocol` in `api/bot_mcp_state_service.py` —
      the five methods in `plan.md`.
- [x] **4.2** Implement `core/mcp/services/bot_mcp_state_service.py`, modelled on
      `local_skill_state_service.py`. Resolve the bot with `get_by_id_and_owner`
      (masked `404`); resolve the container with `skill_set_repo.get_default(
      user_id=owner_id, bolt_id=bot_id, engine_type=bot["active_engine"])`,
      treating `None` as not-found — never an implicit create. No skills-pool
      edit guard.
- [x] **4.3** Merged read: default-set rows (`get_mcp_servers_in_set`) unioned
      with `get_default_mcp_servers(engine, template_type)`, `active` = not in
      `get_all_excluded_mcps`, each marked `is_default`. Dedupe by `server_code`,
      stored row wins.
- [x] **4.4** `add_bot_server` — validate via `market_service.get_mcp_detail` +
      `is_network_type_visible` (one raise site), then `skill_set_repo
      .add_mcp_to_set` **directly** (the service refuses the default set), then
      `add_default_mcp_exclusion` so it lands **inactive**. Already present →
      `changed: false`.
- [x] **4.5** `set_bot_server_active` — activate is
      `remove_all_default_mcp_exclusions`, deactivate is
      `add_default_mcp_exclusion`. Identical for stored rows and engine defaults;
      no branch needed. `changed` computed against the pre-state. A server not on
      the bot at all is not-found.
- [x] **4.6** `remove_bot_server` — `remove_mcp_from_set` plus clearing its
      exclusions for a stored row; raise `McpDefaultServerNotRemovableError`
      (`409`, added to `core/mcp/errors.py`) for an engine default. Absent →
      `deleted: false`.
- [x] **4.7** End every mutation with `sync_service.refresh_mcp_scope(...)`; on
      `success: false`, roll the state write back and raise. One rollback test per
      mutation, with a stubbed sync service — the community device plugins are
      no-ops and cannot fail.
- [x] **4.8** Bind the service in `di/modules/mcp_module.py`.
- [x] **4.9** Service tests: add is idempotent and lands **inactive**;
      activate/deactivate idempotent with correct `changed`; activating a server
      not on the bot is not-found; defaults read active until excluded;
      remove-default is `409`; remove leaves `ac_user_mcp_config` intact; an
      exclusion stranded on a former default set does not survive activate (the
      3.1 case).

## Group 5 — Bot-scoped endpoints

- [x] **5.1** `openapi_v1/bot_mcp/schemas.py` — `BotMcpServer`,
      `BotMcpServerState`, `BotMcpServerAdd`, with `extra="forbid"` on the body
      model (the `sync_mode`-rejection precedent from PR #610).
- [x] **5.2** `openapi_v1/bot_mcp/router.py` — the six operations at prefix
      `/openapi/v1/bots/{bot_id}/mcp`, using `BotIdPath`, `UserIdDep`,
      `PageParamsDep`, `@envelope_errors`. `POST ""` answers `201`.
- [x] **5.3** Mount with `_GRANT_CHECKED_SUBGROUPS` in `openapi_v1/__init__.py`
      — before `bots_router`.
- [x] **5.4** Add all six to `admission.py` as `GRANT_CHECKED_OWN_BOT`, with the
      comment recording why this is narrower than `skills`.
- [x] **5.5** Endpoint tests: full lifecycle add → activate → deactivate →
      remove; an added server reads `active: false` until activated; unowned
      `bot_id` is `404` and byte-identical to an unknown one; unknown/hidden
      `server_code` is `404`; `403` on a mismatched `user_id`; unknown body field
      is `422`.
- [x] **5.6** Routing test: `/openapi/v1/bots/mcp/servers`, `/…/tenants` and
      `/…/configs` still resolve to the account-level group with the new
      `{bot_id}` group mounted.

## Group 6 — Edge and gateway agreement

- [x] **6.1** `route_security` rules for the two new `REFUSED` config operations
      in `src/gateway/configs/application.yaml`.
- [x] **6.2** Update the gateway's `tests/unit/core/authn/test_route_security.py`.
- [x] **6.3** Regenerate `src/gateway/configs/schemas/bots.openapi.json` with the
      existing dump/publish scripts — do not hand-edit.
- [x] **6.4** Confirm `test_principal_seam.py` passes: surface and admission
      table agree in both directions.

## Group 7 — Docs and close-out

- [x] **7.1** Update `src/backend/docs/openapi-v1/README.md`: the `mcp` endpoint
      table (6 → 14), the "**MCP** addresses no bot at all" note under *Per-bot
      device surfaces that are still draft-only* (now false), and the
      delivered-decisions paragraph. Record the 3.2 filter fix as an intentional
      internal behaviour change.
- [x] **7.2** Module docstring in `bot_mcp_state_service.py` recording that this
      is the `skills` mechanism applied to MCP, and pointing at `plan.md` — in
      particular why a dedicated skill set cannot be used, so that is not
      re-derived.
- [x] **7.3** Full module gates: `OCB_PRE_PUSH_RUN_CI=1` per the `AGENTS.md`
      pre-push contract.
- [x] **7.4** PR titled `feat(openapi-v1): MCP config lifecycle and bot-scoped
      activation`, body using the `Problem` / `Solution` / `Validation` sections
      from `.github/pull_request_template.md`.

---

## Notes for the implementer

- **Read `local_skill_state_service.py` before writing Group 4.** This service is
  its MCP twin; where the two differ, the difference should be deliberate and
  explainable (no edit guard, no filesystem layout).
- **Task 3.2 is the only change to existing behaviour.** Everything else is
  additive. If the existing internal suites need edits to pass it, stop and
  re-read Decision 1's risk 1 — that is the signal the change is wider than
  intended.
- **No schema change in this work.** A migration in the diff means the design
  drifted back to the rejected `is_active` column.
- **Do not touch** the sync push paths, `config_compose/collector.py`,
  `write_unified_config` or `build_mcp_sync_payload`.
- **"Skill set" must not appear** in any path, parameter, schema field or
  description on the public surface. It is expected in the storage layer.
- `AGENTS.md`: no `T | None` unless `None` is a real contract state.
