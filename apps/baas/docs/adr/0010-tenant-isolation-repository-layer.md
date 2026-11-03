# ADR-0010: Tenant isolation enforced at the repository layer

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

BaaS is multi-tenant: every table carries a `tenant` column and every API
call arrives with an explicit tenant parameter. The question is where
isolation is enforced. Doing it per API route duplicates a filter across
dozens of routers and guarantees the one forgotten route becomes a
cross-tenant leak.

## Decision

Enforce tenant scoping **once, in the repository layer**, not per route:

- every repository method that reads or writes a tenant-owned aggregate takes
  an explicit `tenant` parameter — e.g. `bot` repo:
  `insert_bot(..., tenant: str, ...)`; `publish` repo:
  `insert_publish(..., tenant: str, ...)` — and applies `tenant` equality to
  every query and insert it performs for that aggregate;
- API routes pass their caller's tenant through; no route constructs
  cross-tenant queries, so a route cannot "forget" isolation;
- tenant lifecycle itself is a managed domain (`api/tenant_manage`,
  core service + repository) because tenants are data (env-invariant keys,
  配置), not just a column.

This makes isolation a property of persistence rather than of surface code
(reviewers check repositories, of which there are tens, not routes, of which
there are hundreds).

## Consequences

**Positive:** one place to audit for cross-tenant leaks; tests assert on
repository contract, which covers every caller; adding routes cannot weaken
isolation.

**Negative:** repository signatures are noisier (`tenant` threading
everywhere); a genuinely cross-tenant admin feature must go out of its way to
be built, by design.

**Risks:** a new repository written without a tenant parameter would silently
lack isolation — code review convention: tenant-owned aggregates must thread
`tenant` in their repo protocol; conformance tests per repository (quartet
`_protocol.py`, ADR-0013) are the natural place to assert it.

## Evidence

- community: `src/secbaas/community/core/repository/bot/_orm_repository.py` — tenant-scoped insert/query
- community: `src/secbaas/community/core/repository/publish/_orm_repository.py` — tenant-scoped insert
- community: `src/secbaas/community/core/repository/` — per-aggregate repository tree
- community: `src/secbaas/community/api/tenant_manage/` — tenant lifecycle API
- enterprise: `README.md` — legacy description of tenant field/query-param model (consistent with this decision)