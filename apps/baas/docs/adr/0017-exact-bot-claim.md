# ADR-0017: Exact bot claim for service principals

## Status

accepted

## Date

2026-11-17

## Applies to

all

## Context

ADR 0016 let a resource key authorize every bot of its tenant by inserting a
mapping row whose `bot_id` is the literal `*`. It recorded the reason: OCB's
agentswarm mints a transient persona bot per agent attempt, with an id derived
from a digest of `(run, agent)`, so no mapping row can name them ahead of time.

It also recorded the cost:

> A wildcard row is a broad grant: it authorizes every bot in the tenant, so a
> compromised key has a larger blast radius than one scoped to named bots.

The grant is broader than the feature requires. A chat leg addresses exactly one
bot, and it knows which: `BaasGatewayChatTransport._resolve_target` resolves
`<bot_id>:<entity_id>` against the run owner *before* minting the principal, and
the request body carries that same string. The wildcard compensates for a
*static* row being unable to name a *dynamic* id — but the grant does not have
to be static. It can travel with the token that already authorizes the request.

## Decision

A BaaS service principal states the bot it addresses in
`principals[0].app.bot_id`, and `exists_bot_mapping` treats that claimed id as
an additional match **inside** the existing resource-key scope:

- the claim is one more branch of the same `or_`, never a bypass: a claimed bot
  still requires a grant naming it (an exact row, or a wildcard);
- the claim is normalized with the platform's own rule before being written, so
  a zero-padded `<entity_id>` matches the normalized row the request looks up;
- an absent or blank claim emits no `bot_id` key at all, so a principal minted
  without a target produces the same token it did before this decision;
- `WILDCARD_BOT_ID` remains supported. Only the default recommendation changes:
  a key scoped to one service identity should carry exact grants, and a
  wildcard is now a deliberate exception rather than the mechanism required for
  dynamic bots.

## Consequences

**Positive:**

- The one claim BaaS authorizes against now carries the narrowest true scope:
  the bot this leg addresses, not the tenant.
- Grant and revoke stay data operations; no schema change, no new column, no
  per-bot row written by another component.
- Adoption is independent on each side. Old BaaS ignores an unknown claim; new
  BaaS treats an absent claim exactly as before.

**Negative:**

- The claim narrows *what a token may reach*, not *what a key may reach*. A
  principal that fabricates `app.bot_id` gains only bots the key already
  reaches; a key holding a wildcard is still tenant-wide.
- Two components now share a normalization rule that must stay in step
  (`normalize_bot_id` in BaaS, its mirror in the mint). A divergence would make
  a legitimate claim miss, which fails closed but is hard to diagnose.
- The authorization predicate grew a parameter; every caller must thread the
  claim through or silently fall back to the pre-claim behaviour.

**Risks:**

- ADR 0016 warned that any path re-deriving the predicate would miss its
  semantics. Adding a branch keeps one predicate but raises the cost of a new
  authorization surface: it must accept and forward the claim too.
- The token's audience is still unchecked (`verify_aud=False`), so token
  issuance remains the single gate. This decision does not change that.

## Evidence

- community: `src/secbaas/community/core/repository/resource_key/_orm_repository.py`
  — `exists_bot_mapping` with `claimed_bot_id`, the single authorization
  predicate.
- community: `src/secbaas/community/adapters/web/routers/gateway/dependencies.py`
  — `GatewayAuthContext.claimed_bot_id`, its extraction from
  `principals[0].app.bot_id`, and the `normalize_bot_id` call it must agree with.
- community: `tests/unit/core/repository/resource_key/test_exists_bot_mapping.py`
  — claim matching an exact grant, claim denied without one, cross-key
  isolation, and absent/blank claims preserving the prior predicate.
- community: `tests/unit/adapters/web/test_gateway_jwt_dependencies.py` —
  claim surfaced on the auth context, absent claim and absent `app` block both
  reading as `None`.
- Avernet `apps/backend` (public):
  `apps/backend/src/agentclaw/community/core/gateway_principal/baas_service_principal.py`
  — the mint, including the normalization mirror and the omit-when-absent rule.
- Avernet `apps/backend` (public):
  `apps/backend/src/agentclaw/community/plugins/community/agentswarm/chat/baas_gateway.py`
  — `_chat_once` passes the resolved target into the mint.
- Supersedes: `ADR-0016-wildcard-bot-mapping.md` — the wildcard's blast-radius
  caveat is what this decision addresses; the wildcard itself is retained.