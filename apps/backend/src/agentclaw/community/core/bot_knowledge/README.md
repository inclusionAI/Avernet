# Bot knowledge

Current selection and best-effort publication observations; external platforms own grants. The platform catalog uses
the existing business Bot ID and target user identifier; no new UUID contract.
Service Bots require the current draft editing state. Version observations are
optional display data, not a new knowledge lifecycle or publication authority.
No new tables or membership synchronization are introduced.

## Context Boundary

```yaml
purpose: Persist current knowledge selection independently of platform grants.
provides:
  - BotKnowledgeService
  - BotKnowledgeRepository
  - BotKnowledgeAgentsMdSync
  - BotKnowledgePublicationSelection
consumes:
  - DatabasePlugin
  - BotCommonConfigRepositoryProtocol
  - KnowledgeCatalogPlugin
  - KnowledgeCatalog
internal_dependencies:
  - agentclaw.community.kernel
  - agentclaw.community.plugin_api
  - agentclaw.community.core.base
  - agentclaw.community.core.repository.protocols.config
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

## Publication selection observations

The existing build task attempts `record_build_selection` only while BUILDING,
using a separate session before the native build. Source Bot PK, current tenant,
environment and space scope the capture. It rereads at most 101 enabled IDs,
stores at most 100, and never turns an invalid source/space into an empty record.
`ac_bot_common_config` holds one last observation per source/tenant/env/publication;
no `online_enabled`, Bot ext or publication ext is added or modified.

`GET /api/bot-knowledge/{bot_id}/publications/{publication_id}/selection`
requires the same current Owner/Editor standing as current-selection reads. The
publication must belong to that source Bot and environment. Callers use the
native publication card's ID/status; this endpoint never chooses a live version,
advances progress, grants access, or asserts that deployed files match the record.

- `last_recorded` + IDs + `captured_at`: last successful capture, including `[]`
  for explicit all-off. It is not proof that the current build was captured.
- `unrecorded` + null: absent, malformed or wrong-scope observation; never fall
  back to current draft or interpret this as all-off.
- `unavailable` + null: optional storage failed. Current access/resource-check
  failures remain ordinary errors, not optional-data degradation.

Historical Bots receive no migration, backfill or file refresh. Their existing
files, draft choices and original lifecycle keep their semantics. Only later
normal BUILDING executions attempt a record. Online promotion, restart and
rollback do not copy configuration; a rollback-selected historical version may
have no record. Draft restore never restores its checkboxes through this feature.

A reused publication can be rebuilt: successful capture replaces its observation;
failure may leave an older record and its original timestamp. No generation,
compensation, retry job or consistency guarantee with AGENTS.md is introduced.
Generic common-config operators retain their existing management privileges;
this is neither a tamper-proof audit log nor runtime authorization.

Failures are logged and cannot alter native task results or publication state.
The task-level guard includes lazy-provider resolution, but deliberately excludes
native build/release execution. Exception isolation is not time isolation: SQL
uses existing connection/driver waits, and the entire DB being unavailable can
still break native publication. Live production timeout/topology validation and
device-bound AGENTS.md acceptance remain separate from local HTTP/DB evidence.

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

## Legacy personal Bot selection

For a Bot with NULL `space_id`, knowledge operations resolve the Owner's
existing, undeleted PERSONAL space in the Bot's environment. They do not create
a space or persist the effective space back to the Bot. Missing/deleted spaces
remain unavailable. Explicit assignments never fall back, including invalid or
deleted assignments. Native Owner/Editor and service-Bot draft rules remain.

Current selections are stored and read using the effective space ID; the write
transaction locks and re-resolves both Bot and space after the external grant
check. A real intervening move is rejected, while normalizing NULL to the same
personal space preserves selections and CAS revisions. Limits, binding pages
and runtime selection reads use the same effective scope.
