# Bot knowledge

Current application selection only; external platforms own grants. The platform catalog uses
the existing business Bot ID and target user identifier; no new UUID contract.
Service Bots require the current draft editing state. No knowledge versions,
release hooks, audit tables or membership synchronization are introduced.

## Context Boundary

```yaml
purpose: Persist current knowledge selection independently of platform grants.
provides:
  - BotKnowledgeService
  - BotKnowledgeRepository
  - BotKnowledgeAgentsMdSync
consumes:
  - DatabasePlugin
  - KnowledgeCatalogPlugin
  - KnowledgeCatalog
internal_dependencies:
  - agentclaw.community.kernel
  - agentclaw.community.plugin_api
  - agentclaw.community.core.base
  - agentclaw.community.core.bot_knowledge
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.knowledge_management
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.services
  - agentclaw.community.core.spaces
  - agentclaw.community.log
  - agentclaw.community.utils
```

Only native Owner/explicit Editor may edit. Enabling consults the platform with
the business Bot ID, existing owner/user identifier and current space; disabling does not require a platform call. Revision CAS and
native editor/draft checks run in the same transaction as the current selection.
Re-read on stale revision; no request history is replayed. Deployments must apply
the reviewed current-state schema before enabling integration.

Space-mismatched selections are not inherited. A new-space write uses revision
zero (the absent view); enabling rechecks that space's external catalog. Reusing
an old row keeps its monotonically increasing revision. Only current-space rows
consume the enabled limit. Disabling an absent relation returns disabled/revision
zero without creating a row. Enabled reads/counts and cursor pages have matching
indexes; current service-Bot draft checks retain their transactional recheck.


## Native editability and tenant lineage

Personal Space Owners and explicit Editors do not need Space-member rows.
Team Space edits still require ACTIVE owner and actor membership as well as
ownership or an explicit editor grant. Space existence, editor standing and
current service-Bot draft state are rechecked inside the write transaction.

`ac_bot_publish` has no tenant column. For legacy `default` Bots, publication
candidates are restricted to source Bot rows in the current tenant/environment
before selecting the latest version. A retained historical source PK remains
valid even if it differs from the current Bot PK; it is never silently rebound.
A matching publication whose source was hard-deleted cannot be attributed to a
tenant and fails closed (`BOT_DRAFT_REQUIRED`), including when ignoring it would
otherwise reveal an older draft. No new publication ownership is inferred from
business Bot/owner IDs alone, and no publication schema or DDL is changed.

## AGENTS.md projection

`PUT /bots/{bot_id}/knowledge-bases/{kb_id}/enablement` remains the only write;
after a successful enablement change the router schedules
`BotKnowledgeAgentsMdSync` (see `agents_md_sync.py`) as a background task. The
binding table stays the single source of truth: the sync rereads the current
selection, resolves display names through the `KnowledgeCatalog.details` port
(batches of 20; a failed batch degrades to ids only), and rewrites one fenced
`KNOWLEDGE_TOOLS` section inside the Bot's draft `AGENTS.md` through
`IdentityService`'s provider-blind identity-file I/O.

The fence markers are the only anchor for locating an existing section and
must never be renamed after release — renaming would orphan every section
already written into existing bots' `AGENTS.md` files and the next sync would
append a second, stale copy. The sync is best-effort: it never fails the HTTP
response, never mutates the stored selection, and skips the update when a
broken fence (start marker without its end marker) suggests hand-editing
damage rather than risk eating user-written content. An empty selection removes
the section; an unchanged projection is not written back.

## Live local acceptance

The unified Singlebox manifest registers all production Core files, both HTTP
routes and both declared `KnowledgeCatalogPlugin` methods. `list_authorized`
is exercised; `list_in_space` currently has no production caller, remains in the
denominator and is not manufactured into coverage through test-only business
calls. The initial Plugin API threshold is the measured 1/2 (50%), not a claim
that both methods were called. The three live HTTP/DB stories cover
selection/CAS/pagination, Personal editor access, Team revocation, draft lifecycle
and tenant-isolated legacy publication selection.

The runner sets `SINGLEBOX_ACCEPTANCE_KNOWLEDGE_FIXTURE_FILE` to the reviewed JSON
catalog. Only the `SINGLEBOX` composition profile reads it; exact Bot/owner/env/
space keys select grants through the existing local Plugin API. Missing catalog
configuration still fails closed; production and unit-test profiles ignore the
fixture variable. This is an external-catalog double, not real remote-platform
integration evidence. Full shared-stack acceptance and artifact verification
must be reported separately from an isolated Backend run.

## Reviewed schema and deferred indexes (2026-10-26)

The approved OceanBase MySQL-mode table definition is
`apps/backend/sql/2026_10_22_bot_knowledge.sql`. It adds database-defaulted
`gmt_create TIMESTAMP`, uses `TIMESTAMP` for `gmt_modified`, retains signed
`BIGINT` IDs/revision and the original business columns/index keys, and supplies
Chinese comments plus OB `GLOBAL` secondary indexes. The portable ORM mirrors
these columns, timestamp defaults and BIGINT revision; it does not emit OB-only
`GLOBAL` syntax. The deployment script supplies database-side `ON UPDATE
CURRENT_TIMESTAMP`; the ORM also sets modification time for local SQLite writes.
Neither timestamp is added to the HTTP response contract.

The two existing-table publication indexes are **deferred and not executed**.
They are recorded separately in
`apps/backend/sql/2026_10_22_bot_knowledge_publish_indexes.sql`, not in the table
creation script. Inspect actual target indexes and query plans and obtain
separate deployment approval before applying them. The ORM still declares these
indexes for fresh local schemas; that is not evidence they exist in an already
running database. Deferring them does not change draft-check correctness, but
production query/lock cost without them remains unverified. No automatic
existing-database migration, production DDL, or deployment is performed here.
