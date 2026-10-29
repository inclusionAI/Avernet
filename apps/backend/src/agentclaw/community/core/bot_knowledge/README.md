# Bot knowledge

External platforms own grants. The platform catalog uses the existing business
Bot ID and target user identifier; no new UUID contract. Service Bots edit only
in draft and have separate working (`enabled`) and production (`online_enabled`)
selection. Draft and verification share the working selection. Personal Bots
keep their immediately effective current selection. Knowledge content is not
version-frozen and no independent knowledge publication workflow is introduced.

## Context Boundary

```yaml
purpose: Persist working and production knowledge selection independently of platform grants.
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

Following release PR #3260, the unified Singlebox manifest lists
`bot_knowledge` under `pending_modules`, not the default measured modules. The
legacy SQL-seeded story Bots have no device binding and cannot exercise the new
`AGENTS.md` projection through its identity-file seam. A passing default
Singlebox run therefore does not establish complete Bot knowledge acceptance.
Restore measured registration only after device-bound acceptance exists and the
complete module has been measured again; prior Core/Router/Plugin percentages
are historical evidence, not current gating results.

The retained three live HTTP/DB stories cover selection/CAS/pagination, Personal
editor access, Team revocation, draft lifecycle and tenant-isolated legacy
publication selection. Their historical Plugin API measurement exercised
`list_authorized`, not `list_in_space`, which has no production caller; no
test-only business call is added to manufacture coverage.

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

## Draft and production selection (2026-10-26)

`GET /api/bot-knowledge/{bot_id}?selection_stage=draft|online` explicitly selects
the view; the default remains draft for compatibility. `items[].enabled` is the
selected value. A service online view reports `runtime_selection=online`; personal
Bots report current in both views. `list_enabled_current` remains a compatibility
working-selection API; new consumers use `list_enabled(..., selection_stage=...)`.
Production state is never writable through the settings API.

`ac_bot_knowledge_binding` retains its identity and draft revision CAS. Production
advancement does not increment that editor revision. Publication owns freezing
the bounded enabled KB-ID set into its existing ext, and atomically replacing
production selection when actual online deployment succeeds. Failed deployment
or rollback admission is not production success. Snapshots cannot cross source
Bot, tenant, environment or space. Reclaiming a binding after a space move clears
its old production flag. Platform grants and native user ACL remain live gates,
not snapshot fields. Actual MCP consumption wiring is outside this change.

Fresh schemas use the updated create script. Existing databases must apply the
review-only `apps/backend/sql/2026_10_26_bot_knowledge_online_selection.sql` before
deploying the new code. It initializes production to false, not from draft:
historical service publications without a trustworthy snapshot produce UNKNOWN
knowledge state (no draft fallback); native publication/rollback may still finish.
Republishing with a trustworthy snapshot restores an explicit selection.
No migration is executed at startup or against any running environment here.

### Publication integration configuration

`user_config.bot_knowledge_publication.enabled` is true in the shipped
`application.yaml`. It controls only publication snapshot/promotion/restore
effects, not knowledge management APIs. Deployments may explicitly set false;
missing configuration retains the false compatibility fallback. Disabled publication
never queries the binding table; enabled integration requires the additive `online_enabled` column and fails closed on DB errors.
Do not toggle off in the middle of active publication or restore operations.

### Short local completion transaction (approved 2026-10-27)

The implementation adds no fields/indexes to `ac_bot_publish`, no publication
side-table, no worker, and no queue integration. `knowledge_selection_snapshot`
and the small `knowledge_selection_result` use the existing publication `ext`.
The result distinguishes a valid empty selection from UNKNOWN history.

Capture is bounded to 100 enabled IDs and contains Bot PK, Owner, environment,
tenant, space, publication ID and a fresh generation token. A durable callback
may restore the snapshot tenant context, but all Bot/Binding tenant guards and
source identity checks still apply. Missing or invalid historical snapshots and
removed bindings produce UNKNOWN, never a later draft selection or an implicit
inheritance of previous production flags. Flags are changed only under a
trustworthy current source/Owner scope; unresolved scopes never revoke a new
Owner's selection. Version snapshots/results stay in publication.ext; the
reserved `knowledge_online_selection` entry in existing `ac_bot_common_config`
holds the current observed result, actual workflow watermark and at most 100
current/previous materialized KB IDs. It is not editable via generic config APIs.
The unique key uses the business Bot ID, environment, and a source-PK namespace
with a SHA-256 representation of the immutable entity identity. Native entity
IDs are never truncated into the config table's narrower field. A source PK is
globally unique and is the tenant authority even for unresolved legacy callbacks;
payload tenant, current Owner and space must match the guarded source on reads.
Owner handoff does not create a second watermark or resurrect an older result.

Online reads load this one row by its existing unique key, then query only its
bounded KB IDs. The flags SQL rechecks the exact current summary value, so a
concurrent change fails closed rather than combining two completion results.
MySQL compares the summary bytes, not a case-insensitive TEXT collation. Pages
use the same fence and mask flags not in the current ID set. No publication
history or JSON-workflow ordering is involved. Personal/draft reads don't query
publication summaries.

Missing/corrupt summaries are UNKNOWN on reads, not an implicit permission to
use old flags. No history/backfill scan is hidden in request or completion paths.
A trusted successful republication initializes an absent summary. Pre-summary
physical flags without a tracked ID may remain in the binding table, but are
not effective: service online readers require both summary membership and the
flag. This is deliberately fail-closed rather than an unbounded cleanup UPDATE.

The ONLINE_PUB → SUCCESS status/ext CAS and binding flag replacement use ONE
real transaction/Session. SQL/connection/commit errors propagate and roll back
both, unlike knowledge business states. The existing progress-poll task retries
local completion after observing the same external workflow; it does not invoke
another deploy. No knowledge-specific queue job or table is needed. Native
ledger/attempt/release gates remain unchanged. Current locking reads of observed
completion IDs fence late outcomes even when the target device UUID changed;
there is no new scan or lock over native operation history.

Rollback admission only performs the native two-record flip, with no knowledge
history validation. Selection changes on observed target deployment success.
Draft restore retains native operation identity, latest-attempt, source, draft
and deadline checks. Unknown history preserves working selection and records
UNKNOWN in the restore marker while completing the native ledger. Completed
restore replay cannot overwrite subsequent edits. A live source PK remains the
native address authority even for legacy publication Owner semantics. If either
publication's Owner metadata differs from the live immutable entity, knowledge
restore is UNKNOWN (no binding edits), not a native restoration failure. This
does not relax entity validation for build or online completion.

Changed content-addressed artifacts are staged before added row locks, unchanged
objects are reused, and PUT failure precedes any local transition. An exact CAS
that races after staging may leave an unreferenced immutable object under the
existing per-record cleanup prefix. Build/deploy/grant/OSS work is never wrapped
in a knowledge transaction. Disabled integration retains native session and
upload behavior and accesses no knowledge table. See
`specs/2026-10-28-knowledge-current-summary/` for current acceptance evidence.

#### Production enablement gate

Publication integration is enabled by the shipped configuration. Deployments
must provision the required schema before deploying it. The new summary entry and
per-KB changes share the native successful-completion transaction. Clearing uses
only previous tracked keys; unknown unresolved scopes keep those keys for later
safe cleanup and never touch a foreign tenant's bindings. Generic configuration
methods which open their own Session are not used inside completion.

The existing common-config table and its unique key must exist before deploying
online consumers or enabling integration. Code presence/fresh SQLite schema is
not proof of deployed schema or MySQL/OceanBase locking/performance. No new DDL,
startup provisioning, production operation or enablement is performed here.
The growing-history/disabled-binding regression records bounded local work;
production plans, contention and latency remain separate rollout evidence.

#### Native identity and completion proof compatibility

Native `ac_bot_publish.owner_id` is the immutable resource `entity_id`; it is not
rewritten on current-Owner handoff. Knowledge snapshots independently capture
`source_entity_id` and current `owner_id`. Completion results record the locked
current Owner. The current summary is keyed by immutable resource identity, then validated
against the current Owner; Owner-specific history selection is never used and
cannot resurrect an older result after A → B → A handoff. Native resource identity,
status, queue and deployment behavior are unchanged.

The real success observer forwards its `baas_publish_id` through a backward-
compatible optional `observed_workflow_id` CAS argument. This is required for
retry/restart completion: the ledger-preferred workflow actually observed may
be different from the old `ext.publish.online` or a missing/stale best-effort
restart marker. The argument is not a new DB field and does not rewrite native
release/restart handles. When publication integration is enabled, successful
status writes require explicit observation; stored release slots are not proof.
The legacy `/status` endpoint cannot supply that argument and returns an explicit
`KNOWLEDGE_OBSERVATION_REQUIRED` conflict rather than synthesizing SUCCESS.
Other transitions and disabled integrations retain native behavior. A failed
build retry retains its frozen generation; a new DRAFT admission captures anew.

A failed restart of an already successful online version is different from a new
observed completion. Native retry restores `FAILED` (`ext.source_status=success`)
to `SUCCESS` before re-issuing restart. It uses the internal
`restore_failed_online_restart(publish_id, expected_ext)` entry, never a fabricated
observed workflow. Enabled recovery locks and checks status, exact ext, environment
and the latest failed online restart ledger; a recreated target must match the
observer's abandoned-RESTART / failed-FIRST_RELEASE workflow. Only native status
and `retry` change: no knowledge table, result, summary or binding write. Ordinary
status APIs cannot use this exception. Stale proof/DB failures precede re-issue.

The publication switch does not disable explicitly requested online API reads.
Their summary table prerequisite and rollout cadence must be checked separately.
Historical publication-history probes in the 2026-10-27 verification are
superseded by the 2026-10-28 current-summary evidence, not rewritten as old passes.

#### Bounded key access and encoding

A bounded `IN` list alone is not a scan bound: at 100 IDs SQLite could choose
the broad space/enabled index prefix. Completion now resolves the full unique
KB key without space/enabled predicates, checks these fields on current locked
rows, and executes at most 200 PK-equality updates in bounded driver batches.
Every write retains explicit Bot/environment/tenant/space/prior-flag predicates
in the same Session; no raw connection or tenant-free mutation is used. Already
correct bindings cause no UPDATE. The boundary test inspects all emitted plans,
including executemany, with 100 removed and 100 replacement IDs.

Applied summaries encode the selected IDs once; decoding reconstructs the equal
materialized set. UNKNOWN can retain an explicit previous materialized set, but
its current set is empty. This preserves 100 valid 128-character multibyte IDs
inside existing TEXT capacity without adding columns or reducing the ID limit.

#### Review fixes: admission, capacity and lock bounds (2026-10-28)

Every allowed DRAFT → BUILDING status/ext writer freezes through the same boundary,
including the legacy status endpoint. Invalid publication policy is resolved at
root container construction, before lifecycle discovery can swallow its failure.

The artifact offloader checks the complete inline ext TEXT budget, not just the
artifact size; the native 60-KiB artifact threshold remains compatible. If a
freeze needs a new object, it captures a candidate under the shared Bot lock,
releases locks for the immutable PUT, then reacquires and recaptures with the same
generation. Only matching native ext, selection and scope may commit BUILDING.
Contention retries are bounded to three; upload failure commits no admission.
Delete/PUT races retain prefix cleanup. The inline/no-artifact path stays one
short transaction. Existing markers are resolved outside locks and normalized
against the new snapshot budget before revalidated admission; reducing a selection
therefore cannot break the next exact ext CAS. Caller-supplied ext is never replayed
against a concurrently changed native ext; that admission returns a CAS miss.
Later writers include protected metadata in pre-lock offload decisions. Completion
also reserves 2 KiB for its compact Owner/status/reason/workflow result. Every opted-in
write checks its final pending object key against the pre-lock staged/reference key;
a new unstaged marker causes rollback rather than committing an unreadable record.

Source locking uses the immutable PK with tenant/env/native-identity validation
and the existing SQLite writer reservation. Draft restore separates the indexed
enabled-set seek (at most 101) from the full unique-key snapshot seek (at most
100), rather than OR-scanning accumulated disabled bindings. SQLite VM evidence
is local only; deployed MySQL/OceanBase plans and contention remain unverified.
