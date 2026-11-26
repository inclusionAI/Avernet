# Engine-config endpoints: classify structured device-offline as 503 INFO

- Date: 2026-11-26
- Trigger: TeamClaw CDO online-error cluster `[bot_router.get_engine_config] Error: No active device for binding=<id>` (two samples, 2026-11-25 22:30–22:31, important=False, self-healing).
- Scope: `apps/backend` — `bot_management` router's `GET/PUT /api/bots/{bot_id}/engine-config` only.

## Problem

`DeviceOfflineError` / `DeviceConnectionUnavailableError` are the trusted
structured offline contract: `BaasConnInfoBuilder` raises them only when the
provider answers with the structured offline payload (BaaS 404/503
`detail.error=NO_ACTIVE_DEVICES`). `BaasService` deliberately logs that state
at **INFO** — "expected, self-healing business states, not faults; logging
them as ERROR only floods the alarm/ticket pipeline."

The two engine-config endpoints were the only read/write paths to this
contract without an explicit branch: both states fell into the generic
`except Exception`, which produced

1. an **ERROR** log line (`[bot_router.get_engine_config] Error: …`) — the
   exact line that fed this cluster into the CDO alarm pipeline, and
2. `error_code=500` with the internal exception text in the user-facing
   message (leaking `binding=<id>`).

## Solution

Catch `(DeviceOfflineError, DeviceConnectionUnavailableError)` explicitly in
both handlers and classify them the way the sibling surfaces already do
(`channel/router.py`, `resources/file_router.py` → 503 "Device temporarily
unavailable"; `openapi_v1/responses.py` envelope → 502):

- `logger.info` with the endpoint prefix and the error detail (observable,
  but not an alarm),
- `error_code=503` with message `Bot 设备暂时不可用，请稍后重试` — no
  internal exception text, no binding id.

`DeviceNotBoundError` (409), `BotNotFoundError` (404) and every other branch
keep their existing behavior. `RuntimeBindingChangedError` subclasses
`DeviceConnectionUnavailableError` and therefore inherits the same
retry-later classification — consistent with the channel/file-router
precedent.

## Validation

- `DEPLOY_PROFILE=test uv run pytest tests/community/api/bot_management/` → 221 passed
- `DEPLOY_PROFILE=test uv run pytest tests/community/contracts` → 588 passed
- New cases in `tests/community/api/bot_management/test_router_part3.py`:
  read side 503 + stable message + no binding-id leak + INFO-not-ERROR log
  assertion for both states; write side 503 + same log-level assertion.

## Out of scope / known gaps

- The BaaS-side offline cause itself (container upgrade/recycle windows) is
  platform-side self-healing; not addressable from this repo.
- `update_engine_config` still lacks a dedicated `DeviceNotBoundError`
  branch (it currently surfaces as 500 in the generic handler, covered by the
  existing `test_write_failure_surfaces_error`). Changing that is a separate,
  user-facing semantic decision (409 vs 500) left to its own change.
- `bot_management/router.py` is over the 1000-line guard but sits on the
  architecture test allowlist; the +36 lines do not add a new violation.
