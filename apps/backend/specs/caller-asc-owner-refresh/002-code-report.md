---
agent: tc-code
status: completed
created: 2026-11-03
iteration: 1
---

# Avernet Caller refresh callback implementation

## Worktree and boundary

- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-asc-owner-refresh-20261103`
- Branch: `feat/caller-asc-owner-refresh-20261103`, based on GitHub `origin/dev` at `3405cedf51`.
- Existing chain: owner renewal callback updates owner runtime credentials. This patch invokes the scheduled Caller refresh service afterwards for service Bots only.
- Allowed additions: public refresh port, community no-op, DI, exact ARCA Caller repository query and behavior tests. No SDK, enterprise imports, new scheduler, IAM flow, owner renewal interval change, outbound installation code or production operations.

## Changes

- Added `ScheduledCallerRefreshProtocol` v1 in `core/caller_identity/scheduled_refresh.py` and the flat Service API re-export. Its implementation must attempt eligible targets and propagate failures safely. Community binds an explicit no-op; corp overrides the same type.
- Added an optional constructor capability to `BotService` for legacy direct construction. Production composition always injects the selected implementation. Service Bot owner installation must finish before the refresh service runs. Errors fail the callback with a fixed safe `BotServiceError`, preserving the existing owner notification retry behavior.
- Caller refresh receives the stable Bot entity even for `default`; current human owner remains a separate field. The callback scopes only the new Caller invocation to the resolved Bot row's Avernet tenant and restores the previous tenant on both success and failure. No downstream tenant guard is bypassed.
- Added `DeviceBindingRepository.list_active_caller_refresh_bindings(bot_id, entity_id, env)` and its unified ORM implementation. It filters exact Bot reason, stable entity, environment, staff type, ACTIVE status and ARCA provider; it retains the latest binding per device. Actual Caller remains `applied_by`, not the entity. Existing BaaS owner-token fanout query is unchanged.
- Updated API/domain boundary docs. The already large BotService file remains an existing architecture exception; other modified production modules remain under 1,000 lines (BotManagementModule 994, DeviceRepository 948). No allowlist or coverage exclusions were changed.

## TDD and verification

RED observations before implementation:

1. Two new owner-to-Caller delegation tests failed because no Caller service was invoked / failure did not propagate.
2. The real SQLite ARCA query test failed because the new repository method was absent.
3. Both cross-tenant callback tests observed the default tenant instead of the Bot's tenant.
4. The default service-Bot test showed current owner used instead of stable entity.

Final verification used Python 3.12.13 from the existing OCB environment against this worktree's explicit `PYTHONPATH`:

- Focused callback, DI, repository, existing Passport callback/service, API-layer, module-boundary and protocol-contract suites: **136 passed**, zero failed. JUnit: `/tmp/caller-asc-avernet-verified.xml`.
- Additional architecture compliance and Protocol base-order suites: **8 passed**, zero failed.
- Ruff `--select F` on changed production Python/new tests: passed.
- `git diff --check`: passed.

The first architecture invocation lacked exported `PYTHONPATH` for a cold-import subprocess; corrected the invocation. Its other failure identified the missing API context declaration, which was added. Existing Pydantic deprecations and AsyncMock-not-awaited warnings remain in unchanged fixtures. No remote CI, total coverage or live product test is claimed here.

## Diagnostic events

- `scheduled_caller_refresh_requested`, `_succeeded`, `_failed`: Bot, current owner, stable entity, environment; duration and failure category. Raw provider exceptions are not logged or returned, and neither owner nor Caller credential enters these events. Tests assert success/failure events and secret absence.
- `caller_refresh_bindings_requested`, `_succeeded`, `_failed`: Bot/entity/environment/provider, result count or exception type. Real SQLite scope tests and database-failure tests exercise the query and propagation.

## Limitations

The ARCA query does not create missing Caller instances. The inspected current creation flow creates BaaS Caller bindings; whether target production data contains ARCA Caller bindings is not verified. Empty eligible sets therefore remain empty. No new timer means no promise that a one-hour Caller stays valid between owner renewal callbacks. Main agent owns integration, independent Review/regression, final dev rebase and PR/CI convergence.

## Independent-review correction

The actual product creates Caller bindings through BaaS even when the physical runtime is ARCA. A new real-SQLite regression reproduced the ARCA-only filter returning no product targets. The dedicated refresh query now includes BaaS and direct ARCA; TeClaw is excluded and the existing owner fanout query is unchanged. After rebasing onto dev a66ef97e65, the focused repository/callback/DI suites passed 95/95 (JUnit /tmp/asc-avernet-product-target.xml).

## PR #3513 Backend CI fixture correction (2026-11-03)

Remote head `450f86480a` reported **4 failed, 28,987 passed, 40 skipped**. The actual failure log is `/tmp/asc-avernet-backend-ci-failed.log`; these failures were test-fixture issues, not a coverage failure:

- Two new log assertions expected INFO events without explicitly configuring `caplog`, so the full CI capture level omitted them.
- The existing handoff lifecycle fixture used `BotService.__new__` and skipped constructor initialization of the optional scheduled refresh port. Its B/C parameter cases therefore raised `AttributeError`.

The four failures were reproduced together locally with `--log-level=WARNING`: **4 failed**, 26 deselected. The minimal fix adds `caplog.set_level("INFO")` in the two tests and initializes `_scheduled_caller_refresh=None` in the legacy fixture. No production fallback or runtime code changed; no runtime logging was added for this test-only correction.

Affected full callback, repository, handoff lifecycle, Passport and DI suites with `--log-level=WARNING`: **151 passed**, zero failed. JUnit: `/tmp/asc-avernet-ci-fixtures-fixed.xml`. Ruff F and `git diff --check` passed. This is local repair evidence; the parent agent must push and observe a new remote CI result before declaring the PR gate passed.
