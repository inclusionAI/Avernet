# ADR-0016: Wildcard bot grant in resource-key authorization

## Status

accepted

## Date

2026-11-04

## Applies to

all

## Context

A BaaS request authenticated with a resource key is authorized for a target bot
only when a row of `baas_resource_key_bot_mapping` links that key to that bot
(`exists_bot_mapping`). The rule is deliberately deny-by-default: no row, no
access.

That is the wrong shape for a caller that creates bots dynamically. OCB's
agentswarm provisions a transient persona bot per agent attempt, minting an id
from a digest of `(run, agent, attempt)` — the ids cannot be enumerated ahead of
time, so an operator cannot pre-register a mapping row for each one. The
alternative (writing a mapping row from agentswarm's create path) couples BaaS
storage to another component's lifecycle and leaves stale rows behind whenever a
persona is deleted.

## Decision

Treat a mapping row whose `bot_id` is the literal `*` as authorizing that
resource key for every bot:

- `exists_bot_mapping` matches `bot_id = :bot_id OR bot_id = '*'` — still scoped
  by `resource_key_id`, so one key's wildcard never authorizes another;
- the wildcard is **only** an explicitly recorded row, inserted deliberately by
  an operator. It is never inferred from a missing or empty mapping set; that
  would silently convert "never configured" into "allow everything" and destroy
  the deny-by-default property;
- the constant is exported (`WILDCARD_BOT_ID`) and shared by repository and
  tests rather than spelled as a literal in each place.

No schema change is required: `bot_id` is `varchar(128)`, which holds `*`.

## Consequences

**Positive:**

- A caller with dynamically minted bot ids gets access through one row, without
  per-bot registration and without a write path from another component.
- Grant and revoke are data operations; no code change or configuration deploy.
- The deny-by-default property survives: keys with no rows still authorize
  nothing, and the wildcard cannot be reached by omission.

**Negative:**

- A wildcard row is a broad grant: it authorizes every bot in the tenant, so a
  compromised key has a larger blast radius than one scoped to named bots.
  Operators should grant it only to keys that genuinely represent a tenant-wide
  service identity.
- The wildcard is matched per key, not per tenant or per bot type; finer
  scoping would need a new column, which this decision avoids.

**Risks:**

- A future query path that reads the mapping table without going through
  `exists_bot_mapping` would not honor the wildcard. Any new authorization
  surface must reuse this method rather than re-deriving the predicate.

## Evidence

- community: `src/secbaas/community/core/repository/resource_key/_orm_model.py`
  — the `WILDCARD_BOT_ID` constant and the `bot_id varchar(128)` column it is
  stored in.
- community: `src/secbaas/community/core/repository/resource_key/_orm_repository.py`
  — `exists_bot_mapping`, the single authorization predicate.
- community: `tests/unit/core/repository/resource_key/test_exists_bot_mapping.py`
  — exact hit, wildcard hit, cross-key isolation, and deny-by-default on an
  empty mapping set.