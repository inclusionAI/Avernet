# BAAS knowledge standard

This app follows the [repository knowledge standard](../../../../docs/agents/knowledge-standard.md). Its [README](../../README.md) routes to app-owned knowledge; use target-version source, contracts and tests to verify claims.

| Location | Record | Do not record |
| --- | --- | --- |
| `AGENTS.md` | App-specific working rules and required reading. | Full contracts, raw incidents or duplicate repository rules. |
| `docs/agents/` | App-specific knowledge conventions and links to shared templates. | Copies of the global standard or unrelated app facts. |
| `docs/arch/` | App architecture constraints and navigation to existing contracts. | Task plans, unsupported conclusions or copies of authoritative contracts. |
| `docs/adr/` | App-scoped durable decisions, status, rationale and consequences. | Temporary task history or duplicate system-wide decisions. |
| `docs/flows/` | App-local flows, failure boundaries and evidence. | Complete incident transcripts or duplicated API specifications. |
| `docs/diagnostics/` | Symptoms, alternative causes, distinguishing evidence and limits. | Secrets, raw production data or unverified root causes. |
| `specs/` | Change intent, decisions, verification and delivery evidence. | Assumed deployment outcomes or copies of current contracts. |
| Domain README/CONTEXT | Local responsibility, contracts, dependencies and change impact. | Entire system workflows or conflicting architecture rules. |

Use [app templates](templates/README.md), retain existing authoritative paths and update knowledge and navigation with verified changes. Global architecture and cross-app decisions remain in root `docs/arch/` and `docs/adr/`; app-scoped material belongs here. Public implementation facts belong to Avernet; enterprise differences belong to OCB.
