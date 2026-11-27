# Publish lifecycle

## Scope and participants

BaaS owns the publish state machine and per-device execution; the selected sandbox provider owns its implementation of sandbox operations. An accepted API request, a completed batch, and a terminal publish are separate observations. The target revision's enum transitions and service implementation are authoritative.

## Path and state

1. A publish type selects a gated or direct pipeline. CREATE and UPDATE use staged approval; RESTART has its own gate, while the direct types use a single batch.
2. The service advances publish and batch states and records each device attempt in `baas_publish_record`. The per-device record, not the publish status alone, carries the attempt outcome and retry counters.
3. Confirm terminal status and per-device results before concluding the operation succeeded. Failed or rejected transitions have their own semantics; compare the relevant enum and service before retrying.

## Retry and recovery paths

The three recovery paths are distinct and are not interchangeable:

- **Publish retry** (`POST /api/v1/publishes/{publish_id}/retry`) applies only to a `FAILED` publish and creates a new publish with the same bot, type and config in `PENDING`. Other terminal states require a new publish.
- **Automatic device retry** is bounded by `publish_max_retry_times` on the record (default `0`, disabled). The sweep claims timed-out attempts; the attempt must still be `PROCESSING` to be claimed.
- **Manual device retry** (`POST /api/v1/publishes/{publish_id}/devices/{device_uuid}/retry`, or the bulk `POST /api/v1/publishes/{publish_id}/devices/retry-failed`) is operator-authorized and re-provisions only the named device through the same per-device orchestrator instead of rebuilding the publish. Its default budget is `DEFAULT_MAX_MANUAL_RETRIES = 3`, counted in `publish_manual_retry_count` and independent of `publish_max_retry_times`, so a publish with automatic retry disabled can still be recovered.

Manual retry requires a non-terminal publish (not `SUCCESS`, `REVOKED` or `REJECTED`), a `FAILED` device record, a retry-capable provider, an unexhausted manual budget, and a won atomic claim. A successful attempt re-checks the failed stage and can reconcile the publish back from `FAILED`. The bulk endpoint reports accepted and skipped devices individually, never aborts the remaining devices on one refusal, and stops only when the publish becomes terminal. HTTP error mapping: not found → 404; `PUBLISH_TERMINAL`, `RECORD_NOT_FAILED`, `PROVIDER_RETRY_UNSUPPORTED`, `MANUAL_RETRY_EXHAUSTED` → 409.

A record flipped to `FAILED` only because its stage had already failed is a cleanup record (`extra_config.cleanup_reason`, or the standard cleanup message). It never ran, is not a real device failure, and must not block stage reconcile or be counted as a retry target.

## Evidence and boundaries

- [Publish pipeline ADR](../adr/0005-gated-publish-pipeline.md), [lifecycle ADR](../adr/0004-lifecycle-state-machines.md), and [provider abstraction ADR](../adr/0006-paas-provider-abstraction.md).
- [Publish enums](../../src/secbaas/community/api/publish_manage/_enums.py) and [publish service](../../src/secbaas/community/core/service/publish_manage/_publish_service.py) (`retry_publish`, `retry_device`, `retry_failed_devices`).
- [Retry orchestrator](../../src/secbaas/community/core/service/publish_manage/_publish_retry_orchestrator.py), [publish-record protocol](../../src/secbaas/community/core/repository/publish_record/_protocol.py), and the [publish router](../../src/secbaas/community/adapters/web/routers/bot_service/publish_router.py).
- [Tests](../../tests/README.md); the retry paths are covered by `tests/unit/core/service/publish_manage/test_manual_device_retry.py`, `test_retry_failed_devices.py`, `test_retry_device_preconditions.py`, and `tests/unit/adapters/web/test_publish_retry_*_router.py`. Deployment-specific provider configuration and runtime observations must be checked separately; this source-level path does not prove a historical publish completed.
