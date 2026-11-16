# Publish or lifecycle investigation

## Scope and evidence

For an incomplete publish, collect the operation, target Bot and device, environment, requested publish type, revision and same-attempt timestamps. Distinguish submission, approval, batch execution and per-device result. Avoid copying credentials or production payloads into repository documentation.

| Observation | Distinguishing evidence | Next place to inspect |
| --- | --- | --- |
| Waiting or rejected | Current publish status, stage and approval action | [Pipeline ADR](../adr/0005-gated-publish-pipeline.md) and [publish flow](../flows/publish-lifecycle.md). |
| One device fails | Per-device result, selected provider and error for the same attempt | [Sandbox provider contract](../adr/0006-paas-provider-abstraction.md), provider implementation and matching tests. |
| State appears inconsistent | Entity type, prior and observed states, operation and timing | [Lifecycle ADR](../adr/0004-lifecycle-state-machines.md) and the enum/service in the target revision. |

A retry request or current ACTIVE state does not prove the earlier attempt succeeded. Determine retry safety and authorization from the actual operation; record unknown outcomes rather than assuming success or a root cause.
