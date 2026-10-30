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
commit is returned. Detail includes declared Registry MCP codes, derived from the
stored Manifest so historical catalogue rows need no migration. These codes are
template facts, not authorization or runtime status; MCP connection overrides
are never included. Searches match literal name/description/individual tags,
combine a single category, and sort the complete filtered set before pagination.
Historical creation counts come from persisted Bot origins including soft-deleted
rows; catalogue updates cannot change those facts.

Detail also exposes workspace-relative resource destinations, without resource
content or source paths. These are declarations, not execution status: `null`
means omitted/no operation, `[]` clears non-protected workspace items, and a
nonempty array clears those items before writing the declared files. Ordinary
user output can be removed by an explicit resource declaration.

## Context Boundary

```yaml
purpose: "Discover governed Git expert templates while preserving last valid inputs and historical source identity."
provides:
  - ExpertScanner
  - ExpertCatalogService
  - ExpertCatalogServiceProtocol
  - ExpertCatalogRepository
  - ExpertScope
  - ExpertCreationService
  - ExpertCreationServiceProtocol
  - CompiledExpert
consumes:
  - "DatabasePlugin — catalogue persistence and historical Bot aggregates"
  - "Manifest schema — existing validation without capability downgrade"
  - "SkillPackageValidator — standard Skill package metadata"
consumed_by:
  - "core.skill_center.services.git_sync — existing synchronization lifecycle"
  - "adapters.http.openapi_v1.experts — authenticated discovery DTOs"
internal_dependencies:
  - agentclaw.community.core.apply_engine  # capability resolution
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

Expert templates support standard Local Skill packages from the same repository
and commit. Explicit Skill and MCP categories keep the existing Replace contract;
omitted and empty categories remain distinct, without default-set inheritance.
`apply_policy` is explicitly refused both during scanning and when compiling a
historical effective template. It is never stripped to create with weaker semantics.


`POST /openapi/v1/experts/{expert_id}/bots` accepts the original `BotCreate` body
and delegates to the original Manifest submission policy, quota and Passport
flow. The deployment supplies `user_config.expert_market.creation_source.url`
(a credential-free HTTPS Git URL) and optional `auth` (an existing Manifest
source-credential name). GitSync's secret URL is deliberately not reused: its
transport credential and the Manifest resolver credential have different owners.
The configured source must correspond to the catalogue's `source_repo_id`.

The original task spec binds the final stored Manifest key and canonical digest.
Creation passes a Manifest-owned `StateRef("manifest", "sha256-json-v1:<digest>")`
through the existing Apply run and queue for each delivery phase. The Manifest
compiler verifies the exact parsed document it consumes, both at admission and
again in the worker, before any fetch or materialisation. Missing or changed
documents refuse the run without restoring old content. No document or private
storage coordinates travel in this reference. Ordinary subsequent Apply keeps
`MANIFEST_STATE` (an unbound reference) and may use the owner's updated document;
the origin is history, not a permanent lock on configuration. No task recompiles
from the latest expert catalogue or Git.
An internal `ExpertOrigin` travels into the initial Bot INSERT with avatar and
Passport ext fields. Ordinary Bot metadata updates reject that system key.
The public Bot DTO exposes only `expert_name`; internal provenance remains in
`ext.expert_origin`. Teclaw keeps the same native identity declarations and uses
its existing device or platform delivery selected by the deployment. Target
creation preflight, filename validation and Factory checks remain authoritative;
this adapter neither switches engines nor changes the delivery mode.

Expert templates do not support `cli_tools`, including an empty array. Scanning
and creation compilation both refuse the category rather than removing it.
This restriction does not change general Manifest CLI tools or management APIs.
ACLI integration will be specified after Manifest supports it. Skills can still
use commands already available in the runtime; resources retain their ordinary
workspace-relative destination semantics.
