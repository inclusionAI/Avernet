# Evolution control-plane extraction

## Requirement
Implement ADR 0021: deploy Evolution separately from Backend under `apps/evolution`.
Preserve `async run(ctx) -> RunSummary` and the 01–06 Job protocol. Genome and
Promotion remain platform-owned. Agent Swarm is a possible child-operation
provider, not the authority for an Evolution run. Bot-scoped RSI configuration,
execution and evidence must remain distinguishable from chat sessions.

## Acceptance
- One canonical implementation of Run/Strategy/Ledger/Verification outside Backend;
  transitional Python imports retain type identity, not copied implementations.
- No Backend imports in the new application; Genome DTOs/codecs are shared contracts.
- Explicit persistent SQLite configuration and stable worker signing material;
  expired leases can be recovered after process replacement with fencing enforced.
- Independent HTTP lifecycle and authenticated, Bot-scoped configuration/run/evidence
  access. Public-local profile must be reproducible without company credentials.
- Worker protocol and SDK are tested independently of Backend. Existing platform
  compatibility tests continue to exercise the same implementations.
- Bot RSI panel and routing are rolled out only against a configured Evolution
  endpoint. Finished strategies are not represented as evaluated/promoted.

## Non-goals
07–09 full evaluation/promotion, SkillSet Genome compatibility (RSI-25), mid-Python
instruction checkpoint/resume, and pretending Swarm's manifest workspace is the
same as a mutable Evolution workspace.

## Compatibility and deployment
Existing Backend import paths remain transitional shims; platform adapters and the
legacy evaluation/promotion assembly stay in Backend. Do not run two authorities
against the same database. Independent SQLite deployment initially supports one
control-plane process on a persistent local volume, not a multi-replica SQL service.
Production authentication, Engine session-export and Swarm workspace mappings must
be explicitly configured/validated; no permissive production fallback.
