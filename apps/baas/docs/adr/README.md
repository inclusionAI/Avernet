# BaaS Architecture Decision Records

This directory records the durable architectural decisions of the BaaS app —
`secbaas.community` (this repo, `apps/baas/`) and `secbaas.enterprise` (the
private overlay living in the internal OCB monorepo's `apps/baas/src/`).

Scope rules:

- Decisions here are **BaaS-app-level**. Hard-to-reverse, system-level decisions
  for the whole Avernet monorepo live in the repo-root
  [`docs/adr/`](../../../docs/adr/) — see `CONTEXT-MAP.md` and
  `docs/agents/domain.md` for that numbering and lifecycle. No repo-root ADR
  concerns BaaS topics today, so the app-level numbering here starts at 0001.
- The set is **byte-identical** in both repos (Avernet `apps/baas/docs/adr/` and
  the internal OCB `apps/baas/docs/adr/`). Change both copies or neither.

| Number | Title | Status | Applies to | Summary |
| ------ | ----- | ------ | ---------- | ------- |
| [0001](0001-layered-architecture.md) | Layered architecture with inward dependencies | accepted | all | api/adapters/core/bootstrap layers; transport stays in adapters; spi ↔ plugins seam |
| [0002](0002-multi-protocol-serving-runtime-modes.md) | Multi-protocol serving with dual runtime modes | accepted | all | FastAPI web adapter everywhere; SOFA via enterprise runner plugin; `--mode bare\|sofa` |
| [0003](0003-spi-plugin-backend-selection.md) | SPI-based plugin backend selection | accepted | all | contract in `spi/`, implementations in `plugins/`, config-driven switch, enterprise registers extra options |
| [0004](0004-lifecycle-state-machines.md) | Explicit lifecycle state machines | accepted | all | Bot/Device/Publish machines with transition tables in enum docstrings |
| [0005](0005-gated-publish-pipeline.md) | Multi-stage gated publish pipeline | accepted | all | 8 publish types; PREPUB → GRAY → PROD batches; 0–3 approval gates; retry orchestration |
| [0006](0006-paas-provider-abstraction.md) | PaaS provider abstraction | accepted | all | sandbox plugin registry over Arca/Desktop/Docker/K8s/Poolab (+ enterprise TeClaw); async settle |
| [0007](0007-enterprise-community-packaging-split.md) | Enterprise / Community packaging split | accepted | all | PEP 420 `secbaas` namespace; community = platform, enterprise = overlay backends |
| [0008](0008-credential-security-sm4-apikey-gateway.md) | Credential security: SM4 at rest, API-key gateway | accepted | all | GMSSL/SM4 secret plugin (enterprise); API-key + policy gateway for external calls |
| [0009](0009-distributed-lock-cron.md) | Distributed locks for singleton cron execution | accepted | all | `DistributedLockService` around scheduled tasks; enterprise lock backend = ZCache |
| [0010](0010-tenant-isolation-repository-layer.md) | Tenant isolation at the repository layer | accepted | all | `tenant` threading enforced in repositories, not per-route |
| [0011](0011-local-paas-websocket-management.md) | Local PaaS management over WebSocket | accepted | all | `machine_id` connection manager, heartbeat sweep, command routing across instances |
| [0012](0012-sse-bcn-downlink-interactions.md) | SSE downlink streaming for BCN interactions | accepted | all | SSE transport, answer headers, ask-user resolution, bot-dimension abort |
| [0013](0013-async-repository-dal.md) | Async SQLAlchemy repository pattern | accepted | all | repository-per-aggregate quartet; pluggable engines (sqlite/mariadb vs enterprise ZDAS) |
| [0014](0014-background-job-model.md) | Background job model | accepted | all | `AppScheduler` with scheduled task classes; per-bot concurrency; TTL renewal and sweeper jobs |

## Writing the next ADR

1. Next number: **0015**. Create `NNNN-kebab-title.md` here (in both repos,
   byte-identical).
2. Required sections, in order: `# ADR-NNNN: <Title>`, `## Status`,
   `## Date`, `## Applies to`, `## Context`, `## Decision`,
   `## Consequences` (**Positive:** / **Negative:** / **Risks:**),
   `## Evidence`. Body ≤ 120 lines.
3. `## Status` uses the repo lifecycle vocabulary (`docs/agents/domain.md`):
   start `accepted` (or `proposed` if the decision is not yet in force); an
   ADR is never renumbered or deleted — a replacement gets a new number and
   links back ("supersedes ADR-NNNN"), and the superseded one keeps its text
   with status updated.
4. `## Applies to`: `all`, `enterprise`, or `community` — which edition(s)
   the decision governs.
5. `## Evidence` path labeling (keeps both repos' copies byte-identical):
   - `community: <path>` resolves under the Avernet `apps/baas/` root;
   - `enterprise: <path>` resolves under the internal OCB `apps/baas/` root;
   - unlabeled paths must exist under **both** app roots.
   Every path must resolve at authoring time; code is the primary evidence,
   design notes are history.
6. Sections of this README and the ADR bodies follow the repo's ADR customs
   (accepted stays readable after replacement; see
   `docs/agents/domain.md`, "ADR lifecycle").