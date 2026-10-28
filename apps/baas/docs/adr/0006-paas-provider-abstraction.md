# ADR-0006: PaaS provider abstraction for sandboxes and devices

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

Bots run inside provider-managed compute (Arca sandboxes, K8s/ECS, Docker on a
laptop, a desktop machine, internal VM pools). Provider APIs, credential
shapes, and lifecycles differ, and the enterprise set differs from the
self-hosted set. Device records must also survive asynchronous provider
operations — a create call returning before the container is actually ready.

## Decision

Hide every provider behind the sandbox/device SPI with one plugin per
provider, and keep device records authoritative in the BaaS database:

- contracts and a consolidated plugin registry live in
  `spi/sandbox/` (`PaasSandboxPlugins`): `ArcaSandboxPlugin`,
  `DesktopSandboxPlugin`, `DockerSandboxPlugin`, `K8sSandboxPlugin`,
  `PoolabSandboxPlugin` (+ `TeClawBotPlugin` for bot-type devices) — each
  selected via credential-typed factories;
- community implementations in `plugins/sandbox/{arca,desktop,docker,k8s,poolab}/`
  (each with `_stub.py` for testing; Arca ships the `aliyun_ack`,
  `local_k8s`, `local_proc` deployment variants); enterprise options in
  `enterprise: src/secbaas/enterprise/plugins/sandbox/{arca,poolab,teclaw}/`;
- device records are pre-created and settle asynchronously: a restart/update
  with an async callback leaves the device `PENDING` until the callback/health
  check completes, then flips to `ACTIVE` (`DeviceOperationOutcome.HOOK_PENDING`);
  record state is the source of truth for routing decisions, not a transient
  provider response.

Adding a provider = new plugin package + registry entry + credential type in
`api/device_manage`. No `core/` service learns provider specifics.

## Consequences

**Positive:** identical orchestration across heterogenous runtimes; stubs make
provider-less local dev and tests possible; async settle makes provider lag
explicit instead of latent.

**Negative:** every new provider capability needs SPI generalization first;
the registry constructor grows with provider count.

**Risks:** semantic drift between providers (some "destroy" calls are async,
some are final); mitigated by the record-settle rule and per-provider
conformance testing of the SPI.

## Evidence

- community: `src/secbaas/community/spi/sandbox/__init__.py` — `PaasSandboxPlugins` registry and plugin types
- community: `src/secbaas/community/plugins/sandbox/{arca,desktop,docker,k8s,poolab}/` — provider implementations (+ `_stub.py` per provider)
- community: `src/secbaas/community/plugins/sandbox/arca/` — `aliyun_ack / local_k8s / local_proc` variants
- community: `src/secbaas/community/core/service/device_manage/_device_service.py` — `HOOK_PENDING`, record-first update semantics
- enterprise: `src/secbaas/enterprise/plugins/sandbox/{arca,poolab,teclaw}/` — mesh-side providers