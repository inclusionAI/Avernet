# Tenant Isolation — MCP Configuration (Track A, Stage 5)

## Summary

MCP configuration is stored in one undifferentiated pool: nothing records which
tenant a configuration row belongs to, and no read is scoped to a tenant. This
applies the Stage 1 isolation mechanism to that data — the tenant is recorded
when a configuration is created, and every read, update and delete is confined
to the request's tenant — so the public API's `mcp` category can be implemented
without any handler being able to reach the internal tenant's configuration.

This is Track A only. It implements no endpoint, wires no handler, and adds no
route. It is the prerequisite for the `mcp` half of Track B.

## Motivation

The public API's `mcp` category exposes the caller's own per-server
configuration: read it, write it, and have it pushed to the caller's devices.
That configuration is the most sensitive data in the category — it holds API
keys and authorization headers for third-party MCP servers.

Today it is keyed by a user identifier alone. Two things follow. First, a public
endpoint wired to the existing service would read and write the *internal*
tenant's configuration, exposing its credentials. Second, a user identifier is
only meaningful within a tenant: two tenants may each know a "12345", and
nothing distinguishes their rows.

The public-API handoff (`src/backend/docs/openapi-v1/README.md`) records this as
Track A Stage 5, and records that a category's endpoints must not be implemented
before its data is isolated. Stage 1 built and proved the mechanism on bot
records; this stage reuses it unchanged.

Separately, the same handoff currently assigns priority P2 to channels. The
product does not need channels at this point, so the board must stop presenting
them as the next thing to pick up.

## User Stories

- As a caller of the existing internal API, I want every MCP-related response to
  be exactly what it is today, so that adding isolation is invisible to me.
- As an external tenant calling the public API, I want my MCP server
  configuration — API keys and headers included — confined to my own tenant, so
  that no other tenant can read or overwrite my credentials and I cannot reach
  theirs.
- As an external tenant, I want to configure an MCP server for a user identifier
  that another tenant happens to use too, so that my configuration is not
  rejected because of a row I am not allowed to see.
- As an engineer implementing the `mcp` Track B endpoints, I want tenant scoping
  already enforced beneath my handler, so that I cannot leak configuration by
  forgetting to add a filter.
- As a reviewer, I want a model that carries a tenant and the enforcement of
  that tenant to sit together, so I can tell at a glance that they cannot drift
  apart.
- As someone picking up the next slice of the public-API effort, I want the
  handoff board to show what is actually wanted next, so I do not start on
  channels.

## Acceptance Criteria

### Isolation

- [ ] An MCP configuration created during a request belongs to that request's
      tenant.
- [ ] MCP configuration reads — by identifier, by user and server, and listing a
      user's configurations — return only rows belonging to the request's
      tenant.
- [ ] MCP configuration updates and deletes affect only rows belonging to the
      request's tenant. An attempt to modify another tenant's configuration
      behaves as though that configuration does not exist.
- [ ] A bot's per-server MCP call identity (owner/caller) is read and written
      under the request's tenant, including the aggregate read that decides a
      bot's overall call identity.
- [ ] Two tenants can each hold a configuration for the same user identifier and
      the same MCP server, and neither can see or displace the other's.
- [ ] Every MCP configuration row that exists before this change belongs to the
      default tenant, so every current internal API response is unchanged.
- [ ] The tenant is never present in any API response body.
- [ ] The existing internal API test suite passes without modification.
- [ ] Cross-tenant isolation is demonstrated, for each isolated data set, by
      tests that fail without this change and pass with it.

### Handoff board

- [ ] The handoff README records Stage 5 as done, in both its English and
      Chinese editions.
- [ ] The handoff README shows channels as deprioritized rather than P2, in both
      editions, for both its Track A stage and its Track B endpoint group, with
      the reason recorded.
- [ ] Any production schema change this stage requires is recorded where the
      team will find it, including whether it must land before deploy or before
      a second tenant writes.

## In Scope

- The caller's per-MCP-server configuration (API key, headers, endpoint
  selection): tenant recorded at creation, enforced on every read, update and
  delete.
- A bot's per-MCP-server call identity (owner/caller): the same treatment. These
  rows hang off a bot, which Stage 1 already isolates, so this is a second
  independent barrier — but it is the only one covering the reads that never
  mention a bot record.
- Whatever generalization of the Stage 1 mechanism is needed to cover more than
  one model, provided the mechanism's behavior for bot records is unchanged.
- Tests for cross-tenant isolation, for tenant stamping on creation, and for the
  internal response shape being unchanged.
- The production schema change stated exactly, with its ordering constraint
  against a deploy.
- The handoff README updates described above.

## Out of Scope

- Implementing any `/openapi/v1` endpoint, including the `mcp` ones. That is
  Track B and lands separately.
- The MCP marketplace, MCP tenants, and server permissions. These are served by
  MCP Center, an external service; no local table backs them, so there is
  nothing here to isolate. (The "tenants" in that endpoint's name is MCP
  Center's own concept, unrelated to the isolation tenant.)
- The skill-set ↔ MCP server association. It is owned by skill_center and
  belongs to Track A Stage 4 (skills).
- Tenant-leading indexes. Mandatory corp policy, deferred to the dedicated index
  work as an explicit cross-cutting item, exactly as Stage 1 deferred them.
- The real caller-identity verifier. This stage changes nothing about how a
  request's tenant is determined; it consumes the seam Stage 1 established.
- Background and scheduled work that runs outside any request. It resolves to
  the default tenant, which is correct while all data belongs to the default
  tenant, and is already tracked as a cross-cutting item.
- Any change to channels beyond the handoff board's priority and state. No
  channel code, no channel schema.

## Open Questions

- **Is a bot's MCP call identity in this stage, or is it covered by bots
  isolation?** This spec puts it in, on the grounds that its aggregate reads
  never mention a bot record and so are not reached by Stage 1's enforcement.
  The alternative — treating it like the `identity` category, which has no Track
  A stage because it is purely a bot sub-resource — would leave those reads
  unguarded. Worth confirming, because it decides whether one production table
  or two change.
- **How should channels be marked?** This spec assumes "deprioritized, not
  cancelled": the rows stay on the board with their scope intact, so the work is
  recoverable if the product wants it later. If channels are actually cancelled,
  the rows should be removed instead.
- **Does anything outside this repository write MCP configuration rows?** If a
  non-ORM writer exists (a script, a data job, another service), it would bypass
  tenant stamping and fall back to the column's default. The default is the
  correct answer while all data is internal, so this is not blocking — but it
  determines whether a second tenant can ever be written to by that path.
