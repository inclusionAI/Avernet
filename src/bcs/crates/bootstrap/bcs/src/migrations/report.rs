//! SQLite migration runner + helpers.
//!
//! Drives the versioned migration execution loop against an SQLite-backed
//! DB plugin, applies baseline tables and indexes, and reports the current
//! vs. target version. The migration registry and checksum live in
//! [`super::registry`]; the baseline DDL groups live in
//! [`super::baseline_identity`], [`super::baseline_collaboration`], and
//! [`super::baseline_delivery`] (orchestrated by the facade's
//! `SQLITE_DDL_STATEMENTS`).

use bcs_db_api::{
    DbError, DbPlugin, DbResult, DbStatement, DbValue, db_get_column,
};

use super::registry::{
    SqliteMigration, SQLITE_VERSIONED_MIGRATIONS, sqlite_migration_checksum,
    sqlite_target_version,
};
#[allow(unused_imports)]
use super::repairs::{
    add_sqlite_edge_permission_schema, add_sqlite_fixed_loop_runtime_schema,
    add_sqlite_group_participant_tags_schema,
    add_sqlite_human_input_output_metadata_schema, add_sqlite_human_participant_message_visibility_schema,
    add_sqlite_invite_code_id_schema, add_sqlite_one_shot_opening_message_override_schema,
    add_sqlite_session_callback_lease_schema, add_sqlite_state_machine_rerun_lineage_schema,
    ensure_bcs_session_files, ensure_sqlite_bot_internal_attributes,
    ensure_sqlite_bot_task_modes, ensure_sqlite_group_opening_message_column,
    ensure_sqlite_message_owner_bot_id, ensure_sqlite_session_collected_column,
    migrate_sqlite_eventing_plaintext_endpoint, rebuild_sqlite_edge_table_with_gmt_columns,
    repair_sqlite_channel_bindings_audit_schema,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqliteMigrationReport {
    pub current_version: Option<i64>,
    pub target_version: i64,
    pub pending_versions: Vec<SqliteMigrationPlan>,
    pub applied_versions: Vec<SqliteMigrationPlan>,
    pub repaired_columns: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SqliteMigrationPlan {
    pub version: i64,
    pub name: String,
    pub checksum: String,
    pub statements: Vec<String>,
    pub repairs: Vec<String>,
}

/// Execute all SQLite schema work against the given DB plugin.
pub async fn run_sqlite_migrations(db: &dyn DbPlugin) -> DbResult<()> {
    run_sqlite_migrations_with_report(db).await?;
    Ok(())
}

/// Execute all SQLite schema work and return a summary report.
pub async fn run_sqlite_migrations_with_report(
    db: &dyn DbPlugin,
) -> DbResult<SqliteMigrationReport> {
    let before = check_sqlite_migrations(db).await?;
    run_sqlite_bootstrap_tables(db).await?;
    run_sqlite_versioned_migrations(db).await?;
    run_sqlite_bootstrap_indexes(db).await?;
    let mut after = check_sqlite_migrations(db).await?;
    after.applied_versions = before.pending_versions;
    after.repaired_columns = after
        .applied_versions
        .iter()
        .flat_map(|migration| migration.repairs.iter().cloned())
        .collect();
    Ok(after)
}

/// Inspect the current SQLite migration state without mutating the database.
pub async fn check_sqlite_migrations(db: &dyn DbPlugin) -> DbResult<SqliteMigrationReport> {
    let schema_table_exists = table_exists(db, "bcs_schema_migrations").await?;
    let current_version = current_sqlite_version(db, schema_table_exists).await?;
    let mut pending_versions = Vec::new();

    for migration in SQLITE_VERSIONED_MIGRATIONS {
        let checksum = sqlite_migration_checksum(migration);
        if schema_table_exists
            && let Some(applied) = applied_sqlite_migration(db, migration.version).await?
        {
            if applied.checksum != checksum {
                return Err(DbError::InvalidInput(format!(
                    "sqlite migration checksum mismatch for version {} ({}): applied={}, current={}",
                    migration.version, applied.name, applied.checksum, checksum
                )));
            }
            continue;
        }

        pending_versions.push(sqlite_migration_plan(migration, checksum));
    }

    Ok(SqliteMigrationReport {
        current_version,
        target_version: sqlite_target_version(),
        pending_versions,
        applied_versions: Vec::new(),
        repaired_columns: Vec::new(),
    })
}

/// Create missing SQLite tables for fresh local databases.
///
/// This intentionally skips indexes so versioned migrations can run before the
/// current indexes are created.
pub async fn run_sqlite_bootstrap_tables(db: &dyn DbPlugin) -> DbResult<()> {
    for ddl in super::SQLITE_DDL_STATEMENTS.iter().copied().flatten() {
        if is_create_table(ddl) {
            db.execute(DbStatement::new(*ddl)).await?;
        }
    }
    ensure_sqlite_message_owner_bot_id(db).await?;
    ensure_sqlite_session_collected_column(db).await?;
    ensure_bcs_session_files(db).await?;
    ensure_sqlite_bot_task_modes(db).await?;
    ensure_sqlite_bot_internal_attributes(db).await?;
    Ok(())
}

pub async fn run_sqlite_bootstrap_indexes(db: &dyn DbPlugin) -> DbResult<()> {
    for ddl in super::SQLITE_DDL_STATEMENTS.iter().copied().flatten() {
        if is_create_index(ddl) {
            db.execute(DbStatement::new(*ddl)).await?;
        }
    }
    Ok(())
}

/// Apply versioned SQLite migrations and record successful versions.
pub async fn run_sqlite_versioned_migrations(db: &dyn DbPlugin) -> DbResult<()> {
    for migration in SQLITE_VERSIONED_MIGRATIONS {
        apply_sqlite_migration(db, migration).await?;
    }
    Ok(())
}

async fn apply_sqlite_migration(db: &dyn DbPlugin, migration: &SqliteMigration) -> DbResult<()> {
    let checksum = sqlite_migration_checksum(migration);
    if let Some(applied) = applied_sqlite_migration(db, migration.version).await? {
        if applied.checksum != checksum {
            return Err(DbError::InvalidInput(format!(
                "sqlite migration checksum mismatch for version {} ({}): applied={}, current={}",
                migration.version, applied.name, applied.checksum, checksum
            )));
        }
        return Ok(());
    }

    apply_sqlite_migration_body(db, migration).await?;

    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (?, ?, ?, ?)",
        vec![
            DbValue::from(migration.version),
            DbValue::from(migration.name),
            DbValue::from("sqlite"),
            DbValue::from(checksum.as_str()),
        ],
    ))
    .await?;
    Ok(())
}

async fn apply_sqlite_migration_body(
    db: &dyn DbPlugin,
    migration: &SqliteMigration,
) -> DbResult<()> {
    match migration.version {
        2 => repair_sqlite_channel_bindings_audit_schema(db).await,
        // Startup creates any missing organization tables before recording version 3.
        3 => Ok(()),
        // collected column is added by ensure_sqlite_session_collected_column in
        // run_sqlite_bootstrap_tables; version 4 only records progress.
        4 => Ok(()),
        // collected_at column is added by ensure_sqlite_session_collected_column
        // in run_sqlite_bootstrap_tables; version 5 only records progress.
        5 => Ok(()),
        // session_files table is created by run_sqlite_bootstrap_tables via
        // SQLITE_DDL_STATEMENTS; version 6 only records progress.
        6 => Ok(()),
        7 => add_sqlite_human_input_output_metadata_schema(db).await,
        // Startup DDL creates the HumanInput request table and indexes before
        // versioned migrations are recorded.
        8 => Ok(()),
        // Startup DDL creates the additive Eventing tables and indexes before
        // versioned migrations are recorded.
        9 => Ok(()),
        // Eventing was still under development when per-Subscription HMAC and
        // encrypted endpoint storage were removed. Repair empty local schemas
        // without discarding any persisted Subscription configuration.
        10 => migrate_sqlite_eventing_plaintext_endpoint(db).await,
        11 => ensure_sqlite_group_opening_message_column(db).await,
        // task_claim_mode / task_dream_mode columns are added by
        // ensure_sqlite_bot_task_modes in run_sqlite_bootstrap_tables;
        // version 12 only records progress.
        12 => Ok(()),
        // Edge-permission tables (friend unification) + bcs_bots config columns.
        13 => add_sqlite_edge_permission_schema(db).await,
        // Internal Bot attribute columns are added by
        // ensure_sqlite_bot_internal_attributes in run_sqlite_bootstrap_tables;
        // version 14 only records progress.
        14 => Ok(()),
        15 => add_sqlite_group_participant_tags_schema(db).await,
        // SQLite stores session identifiers as unbounded TEXT, so version 16
        // records dialect parity with the MySQL/OceanBase VARCHAR expansion.
        16 => Ok(()),
        17 => add_sqlite_session_callback_lease_schema(db).await,
        18 => add_sqlite_state_machine_rerun_lineage_schema(db).await,
        19 => add_sqlite_one_shot_opening_message_override_schema(db).await,
        20 => add_sqlite_invite_code_id_schema(db).await,
        21 => add_sqlite_human_participant_message_visibility_schema(db).await,
        22 => {
            // This migration contains only simple DDL statements, no routines
            // or string literals containing semicolons.
            for sql in
                include_str!("../../../../../migrations/sqlite/022_message_deliveries.sql").split(';')
            {
                if !sql.trim().is_empty() {
                    db.execute(DbStatement::new(sql.trim())).await?;
                }
            }
            Ok(())
        }
        23 => {
            db.execute(DbStatement::new(include_str!("../../../../../migrations/sqlite/023_message_delivery_policy.sql"))).await?;
            Ok(())
        }
        24 => {
            for sql in include_str!("../../../../../migrations/sqlite/024_delivery_worker_queries.sql").split(';').map(str::trim).filter(|s| !s.is_empty()) {
                db.execute(DbStatement::new(sql)).await?;
            }
            Ok(())
        }
        25 => {
            for sql in include_str!("../../../../../migrations/sqlite/025_delivery_context_selection.sql").split(';').map(str::trim).filter(|s| !s.is_empty()) {
                db.execute(DbStatement::new(sql)).await?;
            }
            Ok(())
        }
        26 => {
            db.execute(DbStatement::new(include_str!("../../../../../migrations/sqlite/026_delivery_pending_abort.sql"))).await?;
            Ok(())
        }
        27 => {
            db.execute(DbStatement::new(include_str!("../../../../../migrations/sqlite/027_run_reply_segments.sql"))).await?;
            Ok(())
        }
        28 => {
            db.execute(DbStatement::new(include_str!("../../../../../migrations/sqlite/028_provider_bot_webhook.sql"))).await?;
            Ok(())
        }
        29 => add_sqlite_fixed_loop_runtime_schema(db).await,
        30 => {
            let columns = db.query(DbStatement::new("PRAGMA table_info(bcs_bots)")).await?
                .iter().map(|row| db_get_column::<String>(row, "name")).collect::<DbResult<Vec<_>>>()?;
            let indexes = db.query(DbStatement::new("PRAGMA index_list(bcs_bots)")).await?;
            let mut index_present = false;
            for row in indexes {
                if db_get_column::<String>(&row, "name")? == "uk_bcs_bots_provider_ref_env" {
                    if !db_get_column::<bool>(&row, "unique")? {
                        return Err(DbError::Conversion("Bot Provider/ref index must be unique".into()));
                    }
                    index_present = true;
                }
            }
            let added = ["provider_id", "provider_bot_ref", "connection_mode", "webhook_url"];
            for (index, sql) in include_str!("../../../../../migrations/sqlite/030_bot_provider_storage.sql")
                .split(';').map(str::trim).filter(|sql| !sql.is_empty()).enumerate()
            {
                if index < added.len() && columns.iter().any(|column| column == added[index]) { continue; }
                if index == added.len() && index_present { continue; }
                db.execute(DbStatement::new(sql)).await?;
            }
            Ok(())
        }
        31 => {
            // Single additive DDL statement; kept verbatim from
            // migrations/sqlite/031_group_human_mention_notify_mode.sql.
            db.execute(DbStatement::new(include_str!(
                "../../../../../migrations/sqlite/031_group_human_mention_notify_mode.sql"
            )))
            .await?;
            Ok(())
        }
        32 => {
            // Auth-session versioned columns. Three ALTER TABLE ADD COLUMN
            // statements (additive, CONSTANT default; SQLite 3.26 has no
            // DROP COLUMN) plus one UPDATE that invalidates legacy
            // `token`/`token_expire_at` rows on upgrade. Comments in the SQL
            // file may contain `;` (prose), so the file is stripped of
            // `--` comment lines first and then split on `;` so each ALTER /
            // UPDATE is its own DB statement (rusqlite only executes one per
            // call). The legacy-invalidate UPDATE is guarded by
            // `session_id IS NULL` so sessions installed via the new port
            // (which always sets `session_id`) survive re-runs — additionally
            // the migration is recorded in `bcs_schema_migrations` and skipped
            // on the second runner pass.
            let body = include_str!("../../../../../migrations/sqlite/032_auth_session_version.sql");
            let stripped: String = body
                .lines()
                .filter(|line| !line.trim_start().starts_with("--"))
                .collect::<Vec<_>>()
                .join("\n");
            for sql in stripped
                .split(';')
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                db.execute(DbStatement::new(sql)).await?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

#[derive(Debug)]
struct AppliedMigration {
    name: String,
    checksum: String,
}

async fn applied_sqlite_migration(
    db: &dyn DbPlugin,
    version: i64,
) -> DbResult<Option<AppliedMigration>> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name, checksum FROM bcs_schema_migrations WHERE version = ?",
            vec![DbValue::from(version)],
        ))
        .await?;
    rows.into_iter()
        .next()
        .map(|row| {
            Ok(AppliedMigration {
                name: db_get_column(&row, "name")?,
                checksum: db_get_column(&row, "checksum")?,
            })
        })
        .transpose()
}

async fn current_sqlite_version(
    db: &dyn DbPlugin,
    schema_table_exists: bool,
) -> DbResult<Option<i64>> {
    if !schema_table_exists {
        return Ok(None);
    }
    let rows = db
        .query(DbStatement::new(
            "SELECT version FROM bcs_schema_migrations ORDER BY version DESC LIMIT 1",
        ))
        .await?;
    rows.into_iter()
        .next()
        .map(|row| db_get_column(&row, "version"))
        .transpose()
}

fn sqlite_migration_plan(migration: &SqliteMigration, checksum: String) -> SqliteMigrationPlan {
    SqliteMigrationPlan {
        version: migration.version,
        name: migration.name.to_string(),
        checksum,
        statements: Vec::new(),
        repairs: Vec::new(),
    }
}

pub(super) async fn table_exists(db: &dyn DbPlugin, table: &str) -> DbResult<bool> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            vec![DbValue::from(table)],
        ))
        .await?;
    Ok(!rows.is_empty())
}

pub(super) async fn sqlite_table_columns(db: &dyn DbPlugin, table: &str) -> DbResult<Vec<String>> {
    let rows = db
        .query(DbStatement::new(format!("PRAGMA table_info({table})")))
        .await?;
    rows.into_iter()
        .map(|row| db_get_column(&row, "name"))
        .collect()
}


fn is_create_table(sql: &str) -> bool {
    sql.trim_start()
        .to_ascii_uppercase()
        .starts_with("CREATE TABLE")
}

fn is_create_index(sql: &str) -> bool {
    let sql = sql.trim_start().to_ascii_uppercase();
    sql.starts_with("CREATE INDEX") || sql.starts_with("CREATE UNIQUE INDEX")
}
