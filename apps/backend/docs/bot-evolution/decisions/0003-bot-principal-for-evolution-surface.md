# Admit bot principals to the evolution surface with self-scoped permissions

> 中文版：[0003-bot-principal-for-evolution-surface.zh-CN.md](0003-bot-principal-for-evolution-surface.zh-CN.md)

Status: proposed (draft decision record; promote to `docs/adr/` on acceptance).

## Decision

OpenAPI v1 currently refuses `bot` principals, and the bot→owner fallback was
removed deliberately. This ADR re-admits bots **only** for the evolution
surface (genome read, experience, inbox, run requests, and runner jobs), with
explicit scopes instead of owner impersonation:

- `genome:read:self`, `experience:write:self`, `inbox:write:self` for a bot
  acting on itself;
- `run:request:self`, off by default, enabled by owner policy with a budget;
- `evolution:runner` for bots or workers that execute strategy jobs, limited to
  registered plugin ids and to the inputs of jobs they have claimed.

`self` is bound to the bot identity in the credential, never to a request
parameter. No bot scope allows promotion, rollback, policy changes, strategy
enablement, or access to another bot's genome. Credentials come from existing
Passport/AgentPass issuance; the gateway signs the principal with kind `bot`
and its scopes; services check scopes through the authorization hook.

Bots reach the surface through the `avn` CLI and a shipped skill, delivered by
Manifest `cli_tools`, following the `bcs-cli` precedent.

Design: [`../06-interfaces.md`](../06-interfaces.md).

## Consequences

- The general OpenAPI v1 refusal of bot callers stays in force for every
  other endpoint.
- Admission tests must prove bot principals are refused outside the
  evolution scopes and cannot cross bot boundaries.
- A new public CLI (`avn`) needs a coverage gate comparable to `bcs-cli`.

## Alternatives

- **Restore the bot→owner fallback.** Rejected: gives a bot its owner's full
  authority, including promotion.
- **Engine-native self-edit tools only.** Rejected: per-engine, unreviewed
  writes; violates engine neutrality and DR-2.
- **MCP server instead of CLI.** Deferred: can be generated from the same API
  later for engines without `exec`.
