# Public API — Bots Category (Track B)

## Summary

The public `/openapi/v1/bots` surface exists today only as route definitions:
thirteen handlers that raise `NotImplementedError` so FastAPI can generate the
contract. This feature makes those routes real. Each handler will serve an
external registered tenant by delegating to the same bot logic the internal
`/api` surface already uses, returning results in the public API's standard
response envelope. Because Track A Stage 1 already confines every bot read and
write to the request's tenant, a correctly wired handler serves only the
caller's own bots — no new isolation work is needed here, only faithful wiring.

## Motivation

Tenant isolation for bots (Track A Stage 1) has landed. That was the gate: it
had to exist beneath both API surfaces before any public endpoint could return
real data, because an unscoped public read would otherwise surface internal
tenant data. With the gate cleared, bots is the first category ready to deliver
end-to-end, and the public-API handoff lists it as the highest priority (P1).

Delivering bots first is worthwhile on its own terms — it is the category every
other one references (identity is a bot sub-resource; channels, mcp, skills,
routines all hang off a bot) — and it proves the Track B pattern (envelope,
pagination, principal + tenant seam, delegation to existing services) that the
remaining six categories will copy.

Today a caller hitting any `/openapi/v1/bots` route gets a `500` from the
unimplemented stub. The route shapes, request/response schemas, and the
`Envelope`/`Page` contract are already fixed by the stubs and the handoff
checklist; what is missing is the body of each handler and the mapping from
public request to internal service call and back to the public schema.

## User Stories

- As an external tenant, I want to create, read, update, list, and delete my
  bots through the public API, so that I can manage agents programmatically
  without the internal console.
- As an external tenant, I want every bots response wrapped in the same
  envelope with a trace id, so that my client handles success, empty, and error
  results uniformly across all public endpoints.
- As an external tenant, I want my bot listing and every by-id lookup to return
  only my own bots, so that no other tenant's agents are ever visible to me.
- As an external tenant creating a bot that needs user authorization, I want a
  `202` with a Passport authorization handle plus a way to poll its status, so
  that I can complete creation once authorization is granted.
- As an engineer implementing the next public category, I want bots to
  establish the Track B wiring pattern, so that I can copy a proven shape rather
  than invent one.

## Acceptance Criteria

- All thirteen `/openapi/v1/bots` routes are implemented; none raises
  `NotImplementedError`.
- Every non-streaming response is the standard `Envelope[...]`; list endpoints
  return `Envelope[Page[...]]` with an accurate `total`. Each response carries a
  `code`, a `message`, the `data` payload (present, possibly null), and a
  `request_id` that mirrors the trace header.
- Success codes match the contract: create returns `201` (or `202` with a
  Passport pending payload when authorization is required); delete returns its
  `Deleted` payload; reads and updates return `200`.
- Every handler resolves the caller via the principal seam and the tenant via
  the tenant seam, and serves only the resolved tenant's bots. A request for a
  bot that exists under a different tenant is indistinguishable from one that
  does not exist (not found), never a cross-tenant read.
- `engine` is rejected as an update field, consistent with it being fixed at
  creation.
- Filters and pagination on the list endpoint (`keyword`, `engine`, `status`,
  page, page size) narrow results as described and never widen them past the
  caller's tenant.
- Name-availability and quota-ceiling checks are evaluated within the caller's
  tenant.
- The existing internal API behavior is unchanged: no internal test is modified,
  and the internal surface returns exactly what it did before.
- Each implemented endpoint has a test proving its success shape, and at least
  the tenant-scoping guarantee is covered (a bot in another tenant is not
  reachable through any read, update, or delete route).

## In Scope

- Handler bodies for all thirteen routes in the bots group, delegating to
  existing internal bot services/repositories.
- Mapping internal domain/service results to the public `bots/schemas.py`
  response models, and public request bodies to internal service inputs.
- Consistent error-to-envelope mapping for the not-found, validation, and
  authorization-pending cases the routes expose.
- Tests for each endpoint's success shape and for the cross-tenant
  non-reachability guarantee.

## Out of Scope

- The real caller authentication verifier. `require_principal` and
  `resolve_avernet_tenant` remain the seams they are today; this feature wires
  against them but does not replace their stub bodies. (Cross-cutting auth
  workstream.)
- Any change to Track A isolation mechanism, guards, or the tenant middleware.
- The other six public categories (mcp, resources, routines, channels,
  identity, skills) and any change to their stubs.
- New bot capabilities not already expressed by an internal service; this is
  wiring, not new domain behavior.
- The path-shape question (`/openapi/v1/bots/...` nesting vs. top-level) for
  the other categories; bots is already top-level and unaffected.
- F2 tenant-leading indexes and background/scheduled-job tenant review
  (tracked cross-cutting items).

## Open Questions

- For each route, which existing internal service method is the correct
  delegate, and does it already accept everything the public request carries?
  (Resolved in the plan by mapping every route to a concrete service call.)
- Do the create/restart/auth-status flows that involve Passport authorization
  have a single internal entry point, or must the handler orchestrate several
  service calls? (Plan phase.)
- Are there internal responses whose fields don't line up 1:1 with the public
  schema (naming, nesting, or absent fields), requiring an explicit adapter?
  (Plan phase.)
