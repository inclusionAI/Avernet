# Expert market

Controlled Git content is reconciled into an independent, scope-bound catalogue.
`ExpertScanner.scan_git` reads immutable Git objects at a complete commit; the
existing GitSync lifecycle invokes it after a successful agents subtree result,
on unchanged trees and during bootstrap. Local seeds require real Git provenance.
A complete enumeration precedes any writes. Per-expert invalid input records an
error and retains the last successful input; missing or explicitly disabled
entries stop discovery without deleting history. `first_added_at` never resets.

`ExpertCatalogServiceProtocol` exposes list, categories and detail. The HTTP DTO
selects display fields only: no Manifest, credentials, repository configuration or
commit is returned. Searches match literal name/description/individual tags,
combine a single category, and sort the complete filtered set before pagination.
Historical creation counts come from persisted Bot origins including soft-deleted
rows; catalogue updates cannot change those facts.

## Context Boundary

```yaml
purpose: "Discover governed Git expert templates while preserving last valid inputs and historical source identity."
provides:
  - ExpertScanner
  - ExpertCatalogService
  - ExpertCatalogServiceProtocol
  - ExpertCatalogRepository
  - ExpertScope
consumes:
  - "DatabasePlugin — catalogue persistence and historical Bot aggregates"
  - "Manifest schema — existing validation without capability downgrade"
  - "SkillPackageValidator — standard Skill package metadata"
consumed_by:
  - "core.skill_center.services.git_sync — existing synchronization lifecycle"
  - "adapters.http.openapi_v1.experts — authenticated discovery DTOs"
internal_dependencies:
  - agentclaw.community.core.apply_engine  # capability resolution and CLI verifier
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.bot_config_manifest
  - agentclaw.community.core.skill_center
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils
```

The additive MySQL migration is `sql/2026_10_27_expert_catalog.sql`; local schema
bootstrap imports the same ORM model. Disable the expert entry point/sync to roll
back while retaining catalogue timestamps and source history. No ordinary Bot or
Skill data is migrated or cleared.

### PostgreSQL legacy JSON parsing

PostgreSQL stores historical `ac_bots.ext` as TEXT. The same-version
`sql/2026_10_27_expert_json_postgresql.sql` installs
`ac_expert_try_jsonb(text)` for safe extraction, including malformed legacy
values. Automatic `core.schema.create_all` executes this exact migration before
bootstrapping tables on PostgreSQL; deployments with `create_schema=false` must
apply it and `sql/2026_10_27_expert_catalog_postgresql.sql` as schema-owner
migrations before exposing expert discovery. Runtime
needs only schema USAGE, function EXECUTE and its ordinary table SELECT grants.
The helper is STRICT/IMMUTABLE/SECURITY INVOKER, has a fixed `pg_catalog` search
path and performs no dynamic SQL or data access. Conversion errors become NULL,
not inferred origin facts. This avoids a PostgreSQL-16-only parser assumption.

A complete commit without an `agents` entry is a legitimate empty catalogue
(Git does not retain empty directories). An `agents` blob or symlink is invalid
structure and cannot withdraw prior entries. Failed fetch/tree enumeration never
reconciles removals.

## Creation

`POST /openapi/v1/experts/{expert_id}/bots` accepts the original `BotCreate` body
and delegates to the original Manifest submission policy, quota and Passport
flow. The deployment supplies `user_config.expert_market.creation_source.url`
(a credential-free HTTPS Git URL) and optional `auth` (an existing Manifest
source-credential name). GitSync's secret URL is deliberately not reused: its
transport credential and the Manifest resolver credential have different owners.
The configured source must correspond to the catalogue's `source_repo_id`.

The original task spec binds the final stored Manifest key and canonical digest;
its workers verify before each read and never reconstruct from the latest Git.
An internal `ExpertOrigin` travels into the initial Bot INSERT with avatar and
Passport ext fields. Ordinary Bot metadata updates reject that system key.
The public Bot DTO exposes only `expert_name`; internal provenance remains in
`ext.expert_origin`. Teclaw expert creation is explicitly refused until runtime
consumption can be proven for that identity adapter.
