# Shared-table membership migration

`2026_07_23_entity_user_list.sql` creates the current model for a new database;
`2026_10_22_membership_scopes.sql` is a one-time, offline-reviewed migration template
for an existing legacy table. Neither is an automatic runtime migration.

Before execution, inspect the real table using SHOW CREATE TABLE, SHOW INDEX and
information_schema.COLUMNS. Verify full-length composite unique-index support,
column charset/collation, engine/row-format/index byte limits, existing index names,
server DDL atomicity, and migration lock/online-DDL behavior. Source VARCHAR(1024)
plus scope columns can exceed ordinary MySQL index-byte limits; SQLite success or
an old CREATE script does not prove this ALTER is supported. Do not shrink paths,
use a unique prefix index, or change legacy entity_id collation to make it pass.
If the target rejects the full key, stop for a concrete reviewed storage/index plan.

Back up schema and rows before an approved migration. Pause membership writes,
apply the verified ALTER and compare all old rows/IDs/values and counts. Existing
rows receive subject_type=user and bot_type=all. Deploy all related readers and
writers before permitting department or Bot-specific records: old processes ignore
these dimensions and can misread/delete/upsert new records. The same requirement
applies to ad-hoc operations and external consumers.

Keep legacy user comparisons unchanged. Department reads/deletes use exact binary
comparison. With a legacy case-insensitive unique index, distinct department paths
that collate equally cannot coexist in the same scope. Writes explicitly reject
this conflict (HTTP 409); an upsert is rechecked before commit, including a competing
insert after preflight, so a different spelling is never silently reported as stored.
SQLite with a case-sensitive index may permit both paths; this is a documented
storage constraint, not implicit case-insensitive department authorization.

Rollback is not a blind code downgrade. Pause writes and identify new-scope records
and collisions that would violate the old three-column unique key. Preserve a
backup, obtain approval for any record disposition, and restore compatible data and
schema before re-enabling an old process. No automatic DELETE or DROP rollback is
provided. Removing a rule affects future eligibility, not already-issued tokens.
