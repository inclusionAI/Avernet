---
agent: tc-code
status: completed
iteration: 1
---

# 编码报告

## Worktree 与边界
- Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-create-desktop-rel20261015
- Existing chain: BotService.create_bot → initialize capabilities → desktop/deferred return or provision_bot. Desktop HTTP create independently uses DesktopCreationLifecycle; desktop restart independently uses DesktopBotService.restart.
- Allowed additions: lifecycle orchestration, shared resolver, existing Desktop hooks and composition-root lazy factory wiring, affected tests/docstrings. No BCS/schema/runtime/deployment changes.

## 实现
- All persisted generic creates register before desktop/deferred returns with respect_drm=False, bypassing old template/capability qualification. Removed provisioning-time duplicate registration and orphaned private qualification helper/imports.
- Shared resolver selects plugin for OpenClaw personal/desktop, gateway wire omission for other combinations; start/restart and coding BaaS preparation share the resolver.
- Actual Desktop lifecycle registers after BaaS handoff and final binding/ext persistence. This ordering prevents its full ext snapshot overwriting bcn_registered. A retained create retries missing registration and skips a true success marker.
- Actual desktop restart registers after BaaS acceptance/approval, regardless of existing marker. Owner is the trusted user_id from the owner-scoped lookup; binding.entity_id may have a staff_ prefix and is not owner_workno.
- Desktop service uses the existing lazy Callable[[], BotService] factory wired through DI, TYPE_CHECKING avoids runtime import cycles; no duplicate HTTP/credential logic.
- Updated directly affected eligibility docstrings and stale test-only mocks.

## 测试
- TDD RED: applicationCoding and disabled-capability creation made zero calls; real desktop missing provider; real desktop restart made zero calls. Each observed before implementation.
- Focused final pytest: 271 passed, 17 pre-existing dependency warnings, 0.99s. Files: create_bcn_register, desktop_bot_service, stop_start, restart_baas_envs, desktop_bcn_wiring.
- Matrix covers seven engines × personal/desktop/service; deferred create followed by provision registers once; DRM/capabilities bypass; success marker and environment skip; plugin/gateway real Desktop create/restart; best-effort failures; no raw runtime tokens in logs; success marker survives publish status update; retained success skips and missing marker retries; lazy DI resolves actual shared BotService without cycles.
- git diff --check: PASS.
- Local flake8 is not installed in reused backend venv; independent lint/full regression and remote CI remain orchestrator gates.
- Source line counts: bot_service.py 6294 (pre-existing large file, decreased), desktop_bot_service.py 1926 (pre-existing large file; narrow lifecycle wiring), desktop_creation_lifecycle.py 448, desktop_bot_module.py 74. No unrelated split performed.

## 外部边界日志
- Existing BcnService request/success/failure events and credential checks remain authoritative and unchanged. Lifecycle request events include bot_id, owner, engine, bot_type, connection_mode; failure event bcn_registration_failed records error_type without exception body. Existing helper emits success/skipped/failure and marker persistence.
- Added Desktop diagnostic event bcn_registration_requested and bcn_registration_failed; no new transport boundary or bespoke redaction layer. Tests inject token-bearing result/error values and ensure raw values do not appear.

## 兼容边界
- No live migration/backfill/deployment. Existing BCS 409 replay semantics do not migrate an already registered connection mode.
- Legacy pending records created before this release that only enter provision_bot do not get the moved create attempt; supported start/restart still retry.
- Desktop BotService restart refusal and dedicated desktop restart validation remain unchanged.
