# Publish lifecycle

## Scope and participants

BaaS owns the publish state machine and per-device execution; the selected sandbox provider owns its implementation of sandbox operations. An accepted API request, a completed batch, and a terminal publish are separate observations. The target revision's enum transitions and service implementation are authoritative.

## Path and state

1. A publish type selects a gated or direct pipeline. CREATE and UPDATE use staged approval; RESTART has its own gate, while the direct types use a single batch.
2. The service advances publish and batch states and records each device attempt. Retry runs the per-device lifecycle with an explicit budget; the sweep handles timed-out attempts.
3. Confirm terminal status and per-device results before concluding the operation succeeded. Failed or rejected transitions have their own semantics; compare the relevant enum and service before retrying.

## Evidence and boundaries

- [Publish pipeline ADR](../adr/0005-gated-publish-pipeline.md), [lifecycle ADR](../adr/0004-lifecycle-state-machines.md), and [provider abstraction ADR](../adr/0006-paas-provider-abstraction.md).
- [Publish enums](../../src/secbaas/community/api/publish_manage/_enums.py) and [publish service](../../src/secbaas/community/core/service/publish_manage/_publish_service.py).
- [Tests](../../tests/README.md). Deployment-specific provider configuration and runtime observations must be checked separately; this source-level path does not prove a historical publish completed.
