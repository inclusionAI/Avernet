# Publish or lifecycle investigation

## Scope and evidence

For an incomplete publish, collect the operation, target Bot and device, environment, requested publish type, revision and same-attempt timestamps. Distinguish submission, approval, batch execution and per-device result. Avoid copying credentials or production payloads into repository documentation.

| Observation | Distinguishing evidence | Next place to inspect |
| --- | --- | --- |
| Waiting or rejected | Current publish status, stage and approval action | [Pipeline ADR](../adr/0005-gated-publish-pipeline.md) and [publish flow](../flows/publish-lifecycle.md). |
| One device fails | Per-device result, selected provider and error for the same attempt | [Sandbox provider contract](../adr/0006-paas-provider-abstraction.md), provider implementation and matching tests. |
| State appears inconsistent | Entity type, prior and observed states, operation and timing | [Lifecycle ADR](../adr/0004-lifecycle-state-machines.md) and the enum/service in the target revision. |
| Manual device retry is refused | Response detail error code: `PUBLISH_NOT_FOUND`/`DEVICE_NOT_IN_PUBLISH` (404); `PUBLISH_TERMINAL`/`RECORD_NOT_FAILED`/`PROVIDER_RETRY_UNSUPPORTED`/`MANUAL_RETRY_EXHAUSTED` (409) | [Publish flow](../flows/publish-lifecycle.md) and the [publish router](../../src/secbaas/community/adapters/web/routers/bot_service/publish_router.py). |
| Publish stays `FAILED` after a reported device success | The record's latest result compared with the failed stage; a success can trigger stage reconcile | [Retry orchestrator](../../src/secbaas/community/core/service/publish_manage/_publish_retry_orchestrator.py) and publish service. |
| A `FAILED` record looks like a device failure but never ran | `extra_config.cleanup_reason` or the standard cleanup message; the record inherited its stage's failure | [Publish flow](../flows/publish-lifecycle.md) cleanup-record note. |

A retry request or current ACTIVE state does not prove the earlier attempt succeeded. Determine retry safety and authorization from the actual operation; record unknown outcomes rather than assuming success or a root cause.

Each device record has two independent retry counters: `publish_retry_count` (automatic, bounded by `publish_max_retry_times`, default `0` = disabled) and `publish_manual_retry_count` (operator-authorized, default budget `3`). An accepted manual retry means the attempt was claimed, not completed; re-read the record and publish state. The bulk endpoint reports accepted and skipped devices separately, so one refusal does not mean the others were refused.
