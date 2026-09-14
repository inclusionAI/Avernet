# Dormant Bot OpenAPI Lifecycle Implementation Plan

> **For agentic workers:** Implement test-first in small commits. Run focused
> Backend and contract gates before Standards/Spec review; run the relevant full
> suite once after review fixes.

**Goal:** Publish Owner-only personal-Bot recycle and reactivate operations under
`/openapi/v1` while preserving existing page, operations, scan, and OCB Corp
integration behavior.

**Architecture:** Keep activation and recycle as separate Core services with a
shared typed result. HTTP adapters own authentication and response translation;
dormant governance callers retain notification/dry-run policy. Exclude Teclaw at
candidate and Core capability boundaries. Reliability persistence is explicitly
deferred.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Injector, SQLAlchemy, pytest,
OpenAPI schema generation, OCB Git submodule integration

## Global Constraints

- Base Avernet work on the latest `github/dev`.
- Resolve Bots by exact `(bot_id, owner_id)` in every path.
- Require `PermissionLevel.OWNER` for both public mutations and status polling.
- Preserve all legacy paths and response shapes.
- Do not add `RECYCLING`, task-queue lifecycle execution, reconciliation, a
  feature flag, or a new audit table.
- Do not expose `dry_run`, internal reason strings, or dormant notifications in
  the public API.
- Do not include Teclaw, desktop, or service Bots.
- Regenerate OpenAPI artifacts; never hand-edit generated schema.

## Task 1: Define typed lifecycle contracts and errors

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/activate_service.py`
- Add: `src/backend/src/agentclaw/community/core/bot_dormant/recycle_service.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/bot_dormant_service_protocol.py`
- Modify: `src/backend/src/agentclaw/community/api/bot_dormant_service.py`
- Modify: focused Core tests under `src/backend/tests/community/core/bot_dormant/`

- [x] Add failing tests for the confirmed activate and recycle state matrices.
- [x] Add immutable `BotLifecycleResult` with `bot_id`, `owner_id`, `status`, and
      `changed`.
- [x] Reuse canonical Bot not-found, unsupported-operation, and invalid-state
      errors; retain `RecycleReleaseFailed` for device release failure.
- [x] Make both services enforce exact ownership, personal Bot type, and Teclaw
      exclusion independently of HTTP adapters.
- [x] Run focused Core tests and confirm green.

## Task 2: Extract and adopt `RecycleBotService`

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/service.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/ops_service.py`
- Modify: `src/backend/src/agentclaw/community/di/modules/bot_dormant_module.py`
- Modify: recycle/scan/internal endpoint tests

- [x] Add tests proving scan and `recycle-one` delegate real execution to the
      new service while preserving dry-run, notification, and audit behavior.
- [x] Move stop, RECYCLED update, and best-effort Passport freeze into
      `RecycleBotService`.
- [x] Remove calls from operations code to private `_execute_recycle`.
- [x] Keep governance notification/audit orchestration outside the Core service.
- [x] Verify existing internal endpoint response payloads remain unchanged.

## Task 3: Exclude Teclaw from every dormant lifecycle entry

**Files:**
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/candidates.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/service.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/activate_service.py`
- Modify: `src/backend/src/agentclaw/community/core/bot_dormant/recycle_service.py`
- Modify: Teclaw-focused dormant tests

- [x] Add failing tests for scheduled candidates, external input, internal
      recycle, page activation, and public lifecycle rejection.
- [x] Reuse `BotService.is_teclaw_bot` through the dormant service protocol;
      avoid scattered engine string checks.
- [x] Exclude scheduled Teclaw candidates before `/alive` calls and emit only an
      aggregate diagnostic.
- [x] Mark Teclaw external-input rows processed and write an unsupported/skipped
      audit without notification or lifecycle side effects.
- [x] Run focused scan and Teclaw tests.

## Task 4: Publish the OpenAPI lifecycle adapters

**Files:**
- Add: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dormant/__init__.py`
- Add: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dormant/router.py`
- Add: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/dormant/schemas.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bots/router.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py`
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`
- Modify: public dormant handler and authorization tests

- [x] Move the existing public activate route without changing its address or
      operation identity.
- [x] Add public recycle and optional addressed `owner_id` to activate/status.
- [x] Register `Check(PermissionLevel.OWNER)` and
      `GRANT_CHECKED_ADDRESSED_BOT` for both mutations and status.
- [x] Return HTTP 200 for completed/idempotent outcomes and HTTP 202 for
      `REACTIVATING`; preserve fixed ErrorEnvelope mappings.
- [x] Test owner, default-Bot disambiguation, collaborator refusal, and exact app
      grant ownership.

## Task 5: Preserve legacy adapters and record public recycle audit

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/bot_dormant/router.py`
- Add or modify: a narrow dormant audit service under
  `src/backend/src/agentclaw/community/core/bot_dormant/`
- Modify: legacy/internal endpoint tests

- [x] Translate typed activation results back into the existing page
      `ApiResponse` and Chinese messages.
- [x] Keep internal activate/recycle payloads, Bearer authentication, `reason`,
      and `dry_run` stable.
- [x] Write `source=openapi`, `check_result=manual`,
      `action_taken=recycled` only for real public state changes.
- [x] Do not write a dormant notification or an audit for idempotent no-op.
- [x] Treat post-recycle audit failure as logged best-effort, not lifecycle
      failure.

## Task 6: Generate schema and run Avernet gates

**Files:**
- Regenerate: `src/gateway/configs/schemas/bots.openapi.json`
- Verify all changed source, test, context, design, and plan files

- [x] Run Ruff on every changed Python file.
- [x] Run focused Core, HTTP, authorization/admission inventory, DI, legacy
      parity, architecture, and OpenAPI dump tests.
- [x] Run singlebox lifecycle acceptance for ARCA/BaaS-shaped personal Bots and
      Teclaw refusal where the local provider permits meaningful proof.
- [x] Run Standards/Spec dual-axis review and fix all high-priority findings.
- [x] Run the relevant Backend full suite once after review fixes.
- [x] Inspect `git diff --check`, the exact commit range, and generated-schema
      diff before publishing an Avernet PR to `dev`.

## Task 7: Integrate into OCB

**Files:**
- Update: `ocb-public` gitlink
- Regenerate/synchronize: `src/gateway/configs/schemas/bots.openapi.json`
- Modify tests only if OCB-specific compatibility coverage is missing

- [ ] Start from the latest OCB `origin/dev` and the merged Avernet commit.
- [ ] Update the gitlink to that exact commit.
- [ ] Run Corp PassportPlugin, DeviceService provider routing, dormant DI, and
      Gateway schema tests.
- [ ] Verify no Corp adapter change is required for freeze/unfreeze or release.
- [ ] Push with `git push --no-verify`, create the OCB PR to `dev`, and enable
      automatic merge when implementation is authorized.

## Task 8: Capture deferred reliability work

- [x] Record a follow-up scope covering durable operation ownership,
      `RECYCLING`, process recovery, Passport compensation, lifecycle mutual
      exclusion, and strict provider-release confirmation.
- [x] Do not claim any deferred behavior as implemented or validated in the
      current PR.
