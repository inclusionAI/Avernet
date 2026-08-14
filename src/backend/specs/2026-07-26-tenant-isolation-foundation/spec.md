# Tenant Isolation Foundation (Stage 1)

## Summary

Bot data currently lives in one undifferentiated pool: nothing records which
tenant a bot belongs to, and no read is scoped to a tenant. This adds that
missing axis — every request carries a tenant, and every bot read and write is
confined to it — so the new public API can serve a registered external tenant
without ever seeing, or being seen by, the existing internal one. Stage 1
establishes the mechanism and proves it on bot records; later stages apply the
same mechanism to the remaining data.

## Motivation

The new public API surface exists today as route definitions with no
implementations. Its callers are external registered tenants; the existing
internal API's callers are not. Both are served by the same application, backed
by the same tables, repositories and services.

That is the problem. Isolation cannot be added endpoint-by-endpoint as each
public category is implemented, because the very first implemented public
endpoint would read internal data. It has to exist underneath both surfaces
before any of them is wired. The public-API handoff identifies this as the work
blocking all seven category implementations.

Isolation is currently absent, not partial. No bot record carries a tenant and
no query filters by one. The nearest existing concept — the deployment
environment (dev/pre/prod) — is fixed for the whole process and cannot vary
per caller, so it cannot stand in. The one place the word "tenant" already
appears on the bot-provisioning path is an unrelated hint forwarded to the
sandbox allocator, not a data-isolation key.

## User Stories

- As a caller of the existing internal API, I want every response to be exactly
  what it is today, so that adding isolation is invisible to me.
- As an external tenant calling the public API, I want my reads and writes
  confined to my own bots, so that no other tenant's data can reach me and mine
  cannot reach them.
- As an engineer implementing a public API category, I want tenant scoping
  already enforced beneath my endpoint, so that I cannot leak data by forgetting
  to add a filter.
- As a reviewer, I want a request's tenant to have one obvious source and one
  obvious enforcement point, so I can tell at a glance whether a change is safe.
- As the engineer delivering caller authentication, I want a single named seam
  to plug the real verifier into, so that no endpoint changes when it lands.

## Acceptance Criteria

- [ ] Every request carries a tenant. When nothing identifies one, that tenant
      is the default tenant.
- [ ] A bot created during a request belongs to that request's tenant.
- [ ] Bot reads — fetch, list, count, name-existence, search — return only
      records belonging to the request's tenant.
- [ ] Bot updates and deletes affect only records belonging to the request's
      tenant. An attempt to modify another tenant's bot behaves as though that
      bot does not exist.
- [ ] Every bot record that exists before this change belongs to the default
      tenant, so every current internal API response is unchanged.
- [ ] The existing internal API test suite passes without modification.
- [ ] A request's tenant never leaks into another request, including after a
      request fails with an error.
- [ ] The tenant a request carries is readable by the code handling that
      request, not only by the layer that established it.
- [ ] Work started during a request inherits that request's tenant.
- [ ] The public API's tenant source is a single replaceable seam: the real
      caller-identity verifier can be substituted without changing any endpoint.
- [ ] Cross-tenant isolation is demonstrated by a test that fails without this
      change and passes with it.

## In Scope

- A tenant carried through the lifetime of a request, with a defined default.
- A single named seam supplying the public API's tenant, with a placeholder
  source until the real verifier lands.
- Bot records: tenant recorded at creation, enforced on every read, update and
  delete.
- Tests for isolation, for non-leakage between requests, and for the existing
  internal API being unchanged.

## Out of Scope

- All other data — resources, channels, skills, MCP configuration, routines.
  Later stages of this same foundation, using the same mechanism.
- Implementing any public API endpoint. This change wires no handler and adds
  no route.
- The real caller-identity verifier, which is the authentication workstream's
  deliverable. This change only guarantees the seam it drops into.
- The production schema change, which is submitted and executed on the platform
  out-of-band. Because no migration file is checked in, `plan.md` states the
  exact required column shape and the ordering constraint against a deploy.
- Background and scheduled work that runs outside any request. It resolves to
  the default tenant, which is correct while all data belongs to the default
  tenant; it must be revisited before a second tenant holds real data. Work
  started *during* a request is in scope and inherits the request's tenant.
- The sandbox-allocator tenant hint on the provisioning path, which is a
  different concept and is left untouched.
- Removal of the dead workspace-file table and its unreachable service, which is
  confirmed dead but is a separate cleanup.

## Open Questions

- **What identifies the public API's tenant, and where does it come from?**
  The seam needs a concrete answer eventually — which claim or header the
  gateway forwards, and what the registered tenant's identifier looks like.
  Not blocking: Stage 1 ships the seam with a default-tenant placeholder.
- **Should a cross-tenant access attempt be indistinguishable from a missing
  record, or an explicit refusal?** This spec assumes indistinguishable, which
  leaks no information about other tenants' data. Worth confirming, since it
  determines what the public API returns.
- **Is `default` safe as the default tenant's identifier?** It must never
  collide with a real registered tenant's identifier.
- **Is the deployment environment still an independent axis under a tenant?**
  This spec assumes yes — a tenant's data is still partitioned by dev/pre/prod,
  and the two scopes compose.
