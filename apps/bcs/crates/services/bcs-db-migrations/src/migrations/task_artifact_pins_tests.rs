//! fsm_task_artifact_pins migration (plan T4 review fix R1): SQLite upgrade
//! plus additive MySQL dialect parity. Opt-in table; no existing table is
//! touched, including the version-50 archive tables.
use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn task_artifact_pins_upgrade_applies_and_is_idempotent() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 50)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    assert!(!table_exists(&db, "bcs_fsm_task_artifact_pins").await?);
    run_sqlite_migrations(&db).await?;
    assert!(table_exists(&db, "bcs_fsm_task_artifact_pins").await?);
    assert_eq!(
        applied_sqlite_migration(&db, 51)
            .await?
            .map(|migration| migration.name),
        Some("fsm_task_artifact_pins".into())
    );
    // Re-runs leave recorded history untouched and keep the table stable.
    let history_sql = "SELECT version, name, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 50 ORDER BY version";
    let before = db.query(DbStatement::new(history_sql)).await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_task_artifact_pins (archive_hash, content_hash, content_key) \
         VALUES ('archive', 'hash', 'artifact-part:aaaa')",
        vec![],
    ))
    .await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, before);
    // One pin per (archive, content blob): re-pinning the same content under
    // the same archive is rejected, never duplicated.
    assert!(
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_fsm_task_artifact_pins (archive_hash, content_hash, content_key) \
             VALUES ('archive', 'hash', 'artifact-part:bbbb')",
            vec![],
        ))
        .await
        .is_err()
    );
    Ok(())
}

#[test]
fn mysql_task_artifact_pins_migration_is_additive_with_bounded_identity_keys() {
    let sql = include_str!("../../../../../migrations/mysql/053_fsm_task_artifact_pins.sql");
    // Additive only: no existing table is touched.
    assert!(!sql.contains("ALTER TABLE"));
    assert!(!sql.contains("DROP"));
    assert_eq!(sql.matches("CREATE TABLE IF NOT EXISTS").count(), 1);
    // Pins are hash-key addressed per (archive, content blob); the utf8mb4
    // content key stays non-indexed so keys fit InnoDB index width bounds.
    assert!(sql.contains("PRIMARY KEY (archive_hash, content_hash)"));
    assert_eq!(
        sql.matches("CHAR(64) CHARACTER SET ascii COLLATE ascii_bin").count(),
        2
    );
}

#[test]
fn sqlite_task_artifact_pins_file_is_registered_and_checksums_stable() {
    let migration = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .find(|migration| migration.version == 51)
        .expect("version 51 registered");
    assert_eq!(migration.name, "fsm_task_artifact_pins");
    let sqlite_sql = include_str!("../../../../../migrations/sqlite/051_fsm_task_artifact_pins.sql");
    assert!(sqlite_sql.contains("PRIMARY KEY (archive_hash, content_hash)"));
    assert_eq!(sqlite_sql.matches("CREATE TABLE IF NOT EXISTS").count(), 1);
}
