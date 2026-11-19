# Avernet context map

This is the entry point for product, architecture, and implementation work.
Read only the sources relevant to the boundary being changed.

## System-wide domain

- [`CONTEXT.md`](CONTEXT.md) defines shared product vocabulary, currently
  centred on the Skill lifecycle, Spaces, Bot consumption, and external
  publication.
- [`docs/adr/`](docs/adr/) contains accepted and superseded system decisions.
- [`docs/arch/`](docs/arch/) remains the binding architecture constitution and
  CI contract.

## Module-local context

| Area | Read before changing it |
| --- | --- |
| Backend | [Backend guide](apps/backend/README.md), its domain index and diagnostic routes; read the affected domain boundary before its task-specific spec. |
| Backend MCP configuration | [`apps/backend/src/agentclaw/community/core/mcp/CONTEXT.md`](apps/backend/src/agentclaw/community/core/mcp/CONTEXT.md), [ADR 0015](docs/adr/0015-merge-user-and-bot-mcp-headers-per-key.md), and [ADR 0016](docs/adr/0016-mcp-url-and-explicit-headers-are-independent.md) |
| Backend apply engine, Bot Config Manifest and Bot Genome | [`core/apply_engine/README.md`](apps/backend/src/agentclaw/community/core/apply_engine/README.md) (the engine and its typed desired state), [`core/bot_config_manifest/README.md`](apps/backend/src/agentclaw/community/core/bot_config_manifest/README.md) and [`core/bot_genome/README.md`](apps/backend/src/agentclaw/community/core/bot_genome/README.md) (the two compilers; the genome also subscribes to its applies' ends to settle promotions, RSI-04), [ADR 0019](docs/adr/0019-bot-genome-is-the-unit-of-evolution.md), [ADR 0020](docs/adr/0020-promotion-is-platform-owned.md), and [ADR 0022](docs/adr/0022-apply-engine-desired-state-and-observed-drift.md) |
| BaaS | [BaaS guide](apps/baas/README.md), its flow and diagnostic navigation, and the affected contract |
| Engine | root `AGENTS.md`, the closest local guidance, and the relevant runtime-layout contract |
| BCS | [BCS guide](apps/bcs/README.md), `apps/bcs/AGENTS.md`, `apps/bcs/CLAUDE.md`, then the closest crate `CONTEXT.md` |
| BCSFuse | [BCSFuse guide](apps/bcsfuse/README.md), its existing document index, flow and diagnostic navigation |
| Frontend | `apps/frontend/AGENTS.md` and the affected API/UI contract |
| Gateway | [Gateway guide](apps/gateway/README.md) — teamclawgw community edition, open-source gateway skeleton (DI/SPI plugin pattern). |
| Proxy | [Proxy guide](apps/proxy/README.md) — sandboxproxy-community, standalone reverse proxy delegating client traffic to Aliyun ACK pods. |
| Frontend nextgen | [Nextgen README](apps/frontend-nextgen/README.md) and [CLAUDE.md](apps/frontend-nextgen/CLAUDE.md) — open-core export from TeamClaw; boundary rules in root `CLAUDE.md`. |
| Evolution RSI control plane | [Evolution guide](apps/evolution/README.md), [shared Genome contracts](packages/genome-contracts/README.md), [ADR 0021](docs/adr/0021-evolution-control-plane-in-apps-evolution.md); standalone execution and migration limitations, not the evaluation prototype below. |
