# Evolve schema v135

This release changes only the public Evolve database and its readers/writers.
The host adapters and Bot backend schema are unchanged. The nine first-install
CREATE statements are in `01-new-tables.mysql.sql`; they are generated from
`public/shared/server/migrations/evolve-schema.ts` using the existing MySQL dialect.
Every table has an auto-increment `id` primary key and every column has a comment.
There are no ordinary secondary indexes. Retained unique constraints enforce
identity, version allocation, registration or configuration-key semantics.

## Removed storage

| Table | Removed fields | Replacement |
| --- | --- | --- |
| `ce_stage_developments` | `stage_skill_id` | Auto-increment `id`; implementation groups and bindings reference this ID |
| `ce_skill_assets` | `current_package_ref`, `current_package_sha256` | Current immutable version, selected by asset and current version number |
| `ce_skill_versions` | `source_version_no` | Referenced source version |
| `ce_skill_events` | `event_id`, `version_from_no`, `version_to_no` | Row `id` and referenced immutable versions |
| `ce_skill_audit_events` | `event_id` | Row `id`; remaining historical audit content is preserved |

No field was removed merely because another table uses the same name. Owner and
space fields preserve access boundaries; names/descriptions and event identities
preserve historical attribution. Package baselines preserve the bytes actually
used by a task, which may differ from a later live package. Pending application
JSON preserves recovery after an external write and database failure.

Implementation IDs, interaction IDs, asset IDs and version IDs remain separate
from row IDs: existing execution/package references and retry semantics consume
them. In particular, manual version IDs are deterministically derived from the
application operation to detect completed retries. Removing these would require
a coordinated execution/API and in-flight-operation identity change, not merely
removing unused persistence. External Bot/Skill/space identifiers belong to the
host and must remain. Task and Step identifiers use the existing native schema.

## Indexes

Removed all ordinary indexes on these nine tables, including indexes duplicated
by unique constraints. Removed the audit event/idempotency unique constraints,
the event-ID/task-ID unique constraints and the version source-task unique
constraint. Current writes deduplicate events by business key and serialize
version application on the asset row. Historical audit rows have no active writer.

Remaining unique constraints (in addition to the nine primary keys):

- Implementations: `implementation_id`; `(stage_skill_id, version_no)`.
- Extension runs: `step_id`.
- Interactions: `interaction_id`; `(step_id, attempt_no)`.
- Assets: `asset_id`; `(owner_user_id, bot_id, external_skill_id)`.
- Versions: `version_id`; `(asset_id, version_no)`.
- Events: `business_key`.
- Configuration: `config_key`.

## Deployment and existing data

Back up the database and stop feature writers before changing an existing schema.
Historical migrations through v134 remain unchanged. v135 copies all nine tables
with the final definitions and validates row counts before switching them.
SQLite does the copy/switch/reference updates in one transaction. MySQL uses one
atomic multi-table rename after building complete copies; migration-owned old
tables remain until configuration/task reference updates succeed, allowing retry.
A derived package/version value that disagrees with its source aborts migration.
Do not deploy old and new writers together. Managed hosts disabling runtime DDL
must run the reviewed migration under their database change process; the fresh
CREATE file is not an upgrade script and does not update `schema_version`.

Existing Stage binding JSON and frozen task Stage IDs are converted to development
primary keys. New `stageSkillId` API/config values are decimal ID strings. Old
Stage development bookmarks/developer packages must be reopened/redownloaded.
Existing implementation IDs, execution packages, Skill asset/version IDs,
interaction IDs and uploaded artifacts are unchanged. Event API IDs now use row
IDs; existing event-only bookmarks must be reopened from the Skill history.

Rollback requires the matched schema/data backup and application version; merely
rolling back the code is not schema-compatible. Do not delete user data to bypass
migration validation. MySQL/ZDAS execution has not been validated on the target
managed engine; local SQLite tests do not constitute managed deployment acceptance.
