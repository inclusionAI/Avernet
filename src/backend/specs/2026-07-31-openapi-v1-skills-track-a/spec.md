# OpenAPI v1 Skills — Track A Tenant Isolation

## Problem Statement

Skills data currently sits in a shared persistence domain. The public OpenAPI
will eventually let an external tenant upload and use its own skills, but the
current skill catalog, skill-set associations, exclusion state, and Skills Pool
control records have no tenant boundary. Wiring any public Skills endpoint on
top of that state would risk exposing `teamclaw` records, modifying another
tenant's records, or rejecting valid writes because a legacy unique key is
shared across tenants.

This Track A slice establishes the data boundary only. It makes the existing
Skills persistence safe for future public endpoints while keeping internal
`teamclaw` behavior unchanged. It does not define the public upload, activation,
or lifecycle workflow; that is Track B.

## Solution

Apply the established tenant-isolation mechanism to the nine confirmed Skills
tables. Each row records its tenant, reads and writes are enforced by the
shared ORM tenant guard, and existing rows remain visible to the internal
tenant through the database default. The production DDL, including all
necessary unique-key replacements, is applied as one release gate before the
application code that reads the new columns is deployed.

An external tenant starts with an empty tenant-scoped skill catalog: it does
not receive Git-market catalog entries automatically. Shared skill categories
remain outside this Track A boundary.

## User Stories

1. As a caller of the existing internal API, I want Skills responses and
   behavior to remain unchanged, so that adding tenant isolation is invisible
   to the current `teamclaw` tenant.

2. As an external tenant, I want every skill catalog record I create or later
   access through a public API to belong to my tenant, so that another tenant
   cannot read, update, or delete it.

3. As an external tenant, I want to start with no automatically provisioned
   Git-market skill entries, so that my catalog contains only records added by
   my own future public API workflow.

4. As an external tenant, I want to use the same identifiers as another tenant
   where the business identity is tenant-local, so that another tenant's
   exclusion, layout, audit, or quarantine record cannot block my write.

5. As an engineer implementing Skills Track B, I want tenant scoping to be
   enforced below the HTTP handler, so that an endpoint cannot leak data merely
   because its implementation omits a tenant predicate.

6. As an operator, I want existing rows and writers that omit the tenant column
   to resolve to `teamclaw` during this cutover, so that the current platform
   continues to work while it is the only tenant with real data.

7. As a reviewer, I want relation loading to preserve the same tenant boundary
   as the parent query, so that a tenant-scoped Skill record cannot lazily
   expose another tenant's association row.

8. As a release owner, I want column additions and unique-key changes to land
   together before the code release, so that there is no interval in which a
   second tenant can write through an incomplete schema boundary.

## Implementation Decisions

- The Track A table scope is exactly:
  `ac_skill`, `ac_skill_set`, `ac_skill_set_skill`, `ac_skill_set_mcp`,
  `ac_default_skillset_mcp_exclusion`,
  `ac_default_skillset_skill_exclusion`, `ac_bot_skill_layout_state`,
  `ac_skills_pool_rollout_audit`, and `ac_skill_migration_quarantine`.

- Each scoped table receives a non-null `avernet_tenant` column with database
  server default `teamclaw`. The mapped model declares the matching tenant
  field and registers with the existing shared tenant guard. This reuses the
  established single enforcement mechanism; it does not add model-specific
  session listeners or endpoint-level filtering.

- The tenant field is persistence metadata, not API data. Existing serializers
  must not expose it and internal response shapes must remain unchanged.

- The production schema change is out-of-band DDL and is one release gate. For
  each affected business unique key, create its tenant-leading replacement
  first, then remove the legacy key. The five replacements are on
  `ac_default_skillset_mcp_exclusion`,
  `ac_default_skillset_skill_exclusion`, `ac_bot_skill_layout_state`,
  `ac_skills_pool_rollout_audit`, and `ac_skill_migration_quarantine`.

- The current production DDL is authoritative for key changes. Track A does
  not introduce a new business unique key for tables that do not currently
  have one merely because a source model declaration suggests one.

- `ac_skill_category` is a shared classification hierarchy and is excluded.
  The legacy, backup, and inactive skill-related tables confirmed outside the
  scope remain unchanged.

- External tenants receive no automatic Git-market catalog seeding. This is a
  catalog-boundary decision only; the upload and activation workflow remains
  Track B.

- Skills Pool audit and quarantine records are tenant-scoped now, despite the
  current cutover being internal-only. Background and scheduled Skills Pool
  jobs do not yet iterate tenants: this is acceptable only until a second
  tenant holds real data, and becomes a release gate before that point.

- Tenant-leading non-unique query indexes are deferred to the cross-cutting
  index work. They are required before opening sustained multi-tenant traffic,
  but do not change the correctness boundary delivered by this slice.

- Any architecture-boundary documentation affected by new dependency edges is
  updated together with the implementation.

## Testing Decisions

- Prove insert behavior: a request-scoped write stamps the current tenant, and
  a raw write omitting the column receives the `teamclaw` server default.

- For each scoped data set, prove tenant A can read and mutate its own rows,
  while the same read, update, and delete against tenant B's rows behaves as if
  the other rows do not exist.

- Prove that each of the five replaced unique keys permits equivalent
  tenant-local records in two tenants and still rejects a duplicate within one
  tenant.

- Prove direct ORM queries are tenant-filtered and that lazy-loaded Skill
  relationships retain the parent query's tenant criteria. The latter is a
  shared-guard regression seam, already covered by a focused guard test.

- Prove the tenant field is absent from serialized output and that existing
  internal Skills tests continue to pass without response-contract changes.

- Run the closest Skills, model, and shared-tenant-guard tests; run required
  architecture checks for every changed boundary declaration. Record any
  platform DDL validation separately because production schema execution is
  not a repository migration.

## Out of Scope

- Implementing or changing the OpenAPI v1 Skills endpoints, including upload,
  activation, patch, assignment, and deletion semantics. Those are Track B.

- Defining public caller authentication or the source of a request tenant.

- Automatically generating, seeding, sharing, or managing a Git skill market
  for external tenants.

- Tenant-isolating `ac_skill_category`, legacy aliases, backup tables, and
  inactive historical skill tables excluded from the confirmed scope.

- Refactoring background or scheduled Skills Pool jobs to enumerate tenants.
  That work is deferred but must complete before a second tenant writes real
  data.

- Tenant-leading non-unique performance indexes, except where a replacement
  unique key requires the tenant-leading shape for correctness.

## Further Notes

- The domain terms and catalog decision are recorded in `CONTEXT.md` and the
  tenant-scoped catalog ADR. The one-gate DDL decision and the five affected
  keys are recorded in the DDL ADR.

- The production DDL run needs platform review and an execution window. Its
  order is mandatory: add tenant columns, create tenant-leading unique keys,
  remove legacy unique keys, then deploy code that reads the new columns.

- Background work resolving to `teamclaw` is a temporary compatibility state,
  not a multi-tenant design. Before the first non-`teamclaw` tenant acquires
  real Skills data, its scan, reconciliation, cleanup, and recovery paths must
  have an explicit tenant-iteration design and test coverage.
