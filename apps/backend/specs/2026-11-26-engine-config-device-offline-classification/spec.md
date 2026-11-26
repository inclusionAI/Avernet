# Engine-config endpoints: classify device-offline vs upstream-unavailable with a stable 503

- Date: 2026-11-26
- Trigger: TeamClaw CDO online-error cluster `[bot_router.get_engine_config] Error: No active device for binding=<id>` (two samples, 2026-11-25 22:30–22:31, important=False, self-healing).
- Scope: `apps/backend` — `bot_management` router's `GET/PUT /api/bots/{bot_id}/engine-config` only.

## Problem

`DeviceOfflineError` and `DeviceConnectionUnavailableError` are distinct
states, not one structured "offline" payload:

- `DeviceOfflineError` is the provider-confirmed structured offline contract:
  `BaasConnInfoBuilder` raises it when BaaS answers `404/503` with
  `detail.error=NO_ACTIVE_DEVICES`. `BaasService` deliberately logs that state
  at **INFO** — "expected, self-healing business states, not faults; logging
  them as ERROR only floods the alarm/ticket pipeline."
- `DeviceConnectionUnavailableError` is a transient provider connection
  failure with **unknown** device state: the same builder maps
  `BaasTransientServiceError` to it, and `BaasService` raises that error for
  BaaS HTTP status `>= 500` and for `httpx.RequestError` (timeout / connection
  failure), logging those upstream failures at **WARNING**.
  `RuntimeBindingChangedError` subclasses it for a runtime-binding race.

Only the provider-confirmed `DeviceOfflineError` is the expected self-healing
offline state; an upstream 5xx/timeout must not be logged as if the device
were confirmed offline.

The two engine-config endpoints were the only read/write paths to this
contract without an explicit branch: both states fell into the generic
`except Exception`, which produced

1. an **ERROR** log line (`[bot_router.get_engine_config] Error: …`) — the
   exact line that fed this cluster into the CDO alarm pipeline, and
2. `error_code=500` with the internal exception text in the user-facing
   message (leaking `binding=<id>`).

## Solution

Catch the two states in separate branches in both handlers. The user-facing
contract is identical — `error_code=503` with message
`Bot 设备暂时不可用，请稍后重试`, no internal exception text, no binding id —
but the logs no longer collapse them:

- `except DeviceOfflineError`: `logger.info` with the endpoint prefix and a
  `device offline (provider-confirmed)` label. This is the expected,
  self-healing state and must not feed the alarm/ticket pipeline.
- `except DeviceConnectionUnavailableError`: `logger.warning` with the
  neutral text `device request unavailable (transient provider connection
  failure or runtime binding change, state unknown)`. This covers BaaS 5xx /
  transport timeouts and, through its `RuntimeBindingChangedError` subclass,
  a runtime-binding race; the device is not known to be offline.

The 503 response and stable message are unchanged for both branches and for
the `RuntimeBindingChangedError` subclass. These endpoints keep their
existing 503 envelope, consistent with the channel/file-router 503
precedent; the `openapi_v1/responses.py` 502 mapping is a separate surface.

`DeviceNotBoundError` (409), `BotNotFoundError` (404) and every other branch
keep their existing behavior. `RuntimeBindingChangedError` is handled by the
neutral WARNING branch, not the provider-confirmed offline branch.

## Validation

- `DEPLOY_PROFILE=test uv run pytest tests/community/api/bot_management/` → 221 passed
- `DEPLOY_PROFILE=test uv run pytest tests/community/contracts` → 588 passed
- New cases in `tests/community/api/bot_management/test_router_part3.py`:
  provider-confirmed `DeviceOfflineError` asserts 503 + stable message + no
  binding-id leak + INFO; connection-unavailable and
  `RuntimeBindingChangedError` assert 503 + stable message + WARNING, no
  `device offline` phrase, and the neutral `device request unavailable`
  message — on both read and write sides.
- The suite counts above are the code-bearing validation evidence for the
  implementation; the later documentation-only commit does not change them.

## Out of scope / known gaps

- The BaaS-side offline cause itself (container upgrade/recycle windows) is
  platform-side self-healing; not addressable from this repo.
- `update_engine_config` still lacks a dedicated `DeviceNotBoundError`
  branch (it currently surfaces as 500 in the generic handler, covered by the
  existing `test_write_failure_surfaces_error`). Changing that is a separate,
  user-facing semantic decision (409 vs 500) left to its own change.
- `bot_management/router.py` is over the 1000-line guard but sits on the
  architecture test allowlist; the +36 lines do not add a new violation.
