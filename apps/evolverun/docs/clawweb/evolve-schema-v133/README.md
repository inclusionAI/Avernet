# Evolve Schema delivery (v122–v134)

Historical delivery only. New deployments must use the
[v135 schema delivery](../evolve-schema-v135/README.md), which removes redundant
fields and indexes and documents the existing-data migration.

This bundle is for the ClawWeb database. It does not change a provider's backend
database. Canonical migrations are in `clawweb/public/shared/server/schema.ts`.
SQL types are rendered with that package's MySQL dialect. Managed hosts that
disable runtime DDL must provision the schema before deploying this feature.

## Deployment order

1. Back up the database and stop Skill/Stage writes during the upgrade. Save the
   current schema ledger, row counts and `SHOW CREATE TABLE` output.
2. Run `00-preflight.mysql.sql` read-only. Check column/index definitions as well
   as version numbers; early development histories reused some version numbers.
3. For a first deployment where all eight feature tables are absent, apply
   `01-new-tables.mysql.sql`; add `ce_steps.ext_json` with
   `02-existing-table.mysql.sql` only if missing. These definitions already
   include the neutral column names and pending application field.
4. For an existing feature database, select the missing statements from
   `03-existing-feature-upgrades.mysql.sql`. Do not execute the whole file:
   duplicate ADD COLUMN statements are not idempotent. Preserve the v128 audit
   data copy before renaming its input/output columns in v132. Existing v131
   installations only need v132 and v133.
5. Compare columns, unique constraints, indexes and row counts. v132 renames
   `ocb_skill_id` to `external_skill_id` in assets, old audit and current events;
   it must preserve every value and the asset uniqueness constraint. v133 adds
   nullable `ce_skill_assets.pending_application_json`.
6. Reconcile `schema_version` through the environment's normal schema delivery
   process only after all corresponding DDL/data migration is verified. Do not
   set MAX(version) to 133 merely to bypass migrations. If runtime migrations
   are enabled, new-table definitions with already renamed columns require the
   ledger to reflect the completed migrations before the service starts.
7. Deploy matching public package and host adapter builds, then verify capability
   discovery, registration, interaction and version read/write using the normal
   product entry points. Reopen writes after the checks pass.

The eight new tables are `ce_stage_skill_implementations`,
`ce_stage_extension_runs`, `ce_stage_interactions`, `ce_skill_assets`,
`ce_skill_versions`, `ce_stage_developments`, `ce_skill_audit_events` and
`ce_skill_events`. v126/v131 preserve pre-existing workflow ownership/archive
schema when reconciling old migration histories; they are not new Evolve tables.

MySQL/ZDAS execution must be checked on the target engine before deployment.
In particular, v132 uses `RENAME COLUMN`; if the managed engine requires another
syntax, its approved DDL must preserve the existing column definition and
indexes. Do not replace tables or copy only a subset of rows to work around it.

## Rollback

Keep new tables, columns, snapshots and events. Rolling application code back to
the pre-feature baseline must not delete feature data. Stop in-flight Skill
applications and reconcile any `pending_application_json` against the host's
live package before changing writers.

Rolling back to an older *feature* build that still reads `ocb_skill_id` is not
schema-compatible with v132. It requires an explicit reverse column rename in
all three tables plus the matching migration-ledger rollback while writers are
stopped. Do not run old and new feature builds concurrently against that schema.
Keep the nullable pending column and its data; never clear it to hide an
uncertain external upload.

## Validation record

Local real-SQLite migration tests cover fresh install, upstream v120/v121,
historical feature v120–v123 and pre-rebase feature v129. They verify ledger/data
preservation, neutral identifier columns and rerun idempotency. The 15 targeted
migration/audit tests, including delivered DDL column/unique-key parity, passed on 2026-09-23.

No target managed database was modified or validated by this bundle. The SQL
and local tests are preparation evidence, not a Pre deployment acceptance.

## v134: generic application configuration

After the earlier schema steps, apply `04-app-config.mysql.sql` on both fresh and
existing installations. Its structure matches `cm_app_config`, with
`config_json` replacing `config_yaml`. This adds a ninth Evolve feature table;
it does not change Workflow configuration or existing task snapshots. The
folder name is retained for existing delivery links.

Before deploying the new binding reader, explicitly convert the environment's
old `clawevolve.host.spacePolicies` into the `skill_task_stage_bindings` row in
`ce_app_config`. Use the actual space and Stage Skill IDs of that environment;
never copy local IDs into Pre/Prod. See [binding schema](../evolve-host-contract.md).
Review and validate the JSON with `parseSkillTaskStageBindings`, then write it
through the [administrator configuration API](../evolve-app-config.md) after
deploying the matching code, or the managed database configuration process.
There is no automatic YAML import, fallback or hardcoded seed. A
missing/disabled row means no configured defaults.

Deployment order: schema, reviewed configuration data, matching public and Host
code, then verify `/api/evolve/skill-assets/:assetId/task-defaults`. Existing tasks
keep their frozen implementations. No restart is needed for later config-value
changes. Rollback keeps this table/data; an older reader requires its previous
YAML configuration restored by the operator. `version` is a revision counter,
not a compare-and-swap or history table.

The MySQL/ZDAS script must still be verified on the target database before use;
local SQLite and dialect-rendering tests do not constitute managed DB acceptance.
