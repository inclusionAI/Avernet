# Runtime target and resource failures

Use this page when a Cron or session/resource operation cannot find its runtime,
addresses an unexpected instance, or times out after target selection. Start with
the actual API contract; these operations do not all use an identical resolver.
The [cross-component map](../../../../docs/flows/backend-runtime-targeting.md)
explains the Cron example.

## Establish the request

Record the operation, environment, timestamp/timezone, request/trace identifier,
Bot ID, owner and authenticated actor. Also record the requested stage, Caller
and device target when the API supports them. Keep identifiers and their sources
distinct: a Bot ID, binding ID, device UUID and provider sandbox ID are not
interchangeable.

## Distinguish candidate causes

| Observation | Evidence to obtain | Next action |
| --- | --- | --- |
| Bot or runtime binding unavailable | Request identity and stage; Bot and publish/Caller binding chosen by the affected API; [binding resolver](../../src/agentclaw/community/core/runtime_binding/service.py) when used. | Resolve authorization/target preconditions before investigating an adapter request that may never have occurred. |
| Binding ACTIVE but connection fails | Same-instance transport request, provider endpoint and adapter readiness at the failure time. | Trace the selected provider and runtime; metadata status alone cannot establish readiness. |
| Cron list/detail disagree | Compare stage, device UUID, operator/Caller and the instance that created the Cron resource; read [Cron semantics](../../src/agentclaw/community/core/cron/README.md). | Confirm resource and request belong to the same target before declaring data loss or changing fallback behavior. |
| Some targets fail in a list | Per-target result and `failed_targets`, including target and failure reason. | Investigate the failed target separately; do not interpret a partial result as a complete empty list. |
| Read/write times out | Selected endpoint, request/response timeline and operation outcome from the receiver where available. | Preserve unknown outcome; determine idempotency and side effects before retrying a write. |

Current bindings describe the time of observation. For a historical incident,
use lifecycle/request evidence to identify the instance at the failure time;
record unresolved mapping rather than substituting the current container.

## Follow the owning boundary

- [Runtime binding](../../src/agentclaw/community/core/runtime_binding/README.md)
  resolves an existing binding; it is not a provisioning or session-affinity service.
- [Devices](../../src/agentclaw/community/core/devices/README.md) and
  [engine runtime](../../src/agentclaw/community/core/engine_runtime/README.md)
  describe the next domain boundaries.
- The [transport Protocol](../../src/agentclaw/community/plugin_api/device_adapter_transport.py)
  and the selected profile implementation determine forwarding. Host-specific
  log/database/provider lookup belongs in the host's diagnostic documentation.
- For manager projection into BCS, use the separate
  [manager-sync runbook](../flows/bot-manager-sync.md); local admission and remote
  delivery have different meanings there.

## Evidence and verification

Keep the repository revisions, actual target, relevant source/test links,
observations that support or reject each candidate, and remaining gaps in the
task report. Empty or truncated logs establish only their queried coverage.
Source inspection explains possible behavior; it does not prove a past event.

Focused verification starts at [binding tests](../../tests/community/core/runtime_binding/test_service.py),
[Cron targeting tests](../../tests/community/core/cron/services/test_cron_targeted_operations.py)
and [live Cron acceptance](../../tests/community/acceptance/cron/).
Choose the test for the affected contract and record any external runtime that
was not exercised.
