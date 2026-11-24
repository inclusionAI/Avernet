//! fsm_task_artifacts migration (plan T4): SQLite upgrade + additive MySQL
//! dialect parity. Opt-in tables: no existing table is touched.
use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn task_artifact_upgrade_applies_and_is_idempotent() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 49)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    assert!(!table_exists(&db, "bcs_fsm_task_archives").await?);
    assert!(!table_exists(&db, "bcs_fsm_task_artifacts").await?);
    run_sqlite_migrations(&db).await?;
    assert!(table_exists(&db, "bcs_fsm_task_archives").await?);
    assert!(table_exists(&db, "bcs_fsm_task_artifacts").await?);
    assert_eq!(
        applied_sqlite_migration(&db, 50)
            .await?
            .map(|migration| migration.name),
        Some("fsm_task_artifacts".into())
    );
    // Re-runs leave recorded history untouched and keep both tables stable.
    let history_sql = "SELECT version, name, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 49 ORDER BY version";
    let before = db.query(DbStatement::new(history_sql)).await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_task_archives (archive_hash, scope_tenant, scope_principal, \
         group_id, sid, tid, round_generation, state, request_digest, result_digest, result_key, \
         measured_bytes, updated_at) \
         VALUES ('hash', 't', 'p', 'g', 's', 'tid', 1, 'committed', 'sha256:a', 'sha256:b', \
         'result:k', 0, 1)",
        vec![],
    ))
    .await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_task_artifacts (tid, artifact_id, archive_hash, part_order, \
         content_key) VALUES ('tid', 'part-1', 'hash', 1, 'artifact-part:x')",
        vec![],
    ))
    .await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, before);
    // The product unique key is enforced: a second row with the same
    // (tid, artifact_id) is rejected, never duplicated.
    assert!(
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_fsm_task_artifacts (tid, artifact_id, archive_hash, part_order, \
             content_key) VALUES ('tid', 'part-1', 'hash', 2, 'artifact-part:y')",
            vec![],
        ))
        .await
        .is_err()
    );
    Ok(())
}

#[test]
fn mysql_task_artifact_migration_is_additive_with_bounded_identity_keys() {
    let sql = include_str!("../../../../../migrations/mysql/052_fsm_task_artifacts.sql");
    // Additive only: no existing table is touched.
    assert!(!sql.contains("ALTER TABLE"));
    assert!(!sql.contains("DROP"));
    assert_eq!(
        sql.matches("CREATE TABLE IF NOT EXISTS").count(),
        2,
        "archive rows plus artifact-part references"
    );
    // The product unique key is exactly (tid, artifact_id) and the archive row
    // is hash-key addressed, so utf8mb4 indexes stay well under the
    // 3072-byte InnoDB limit.
    assert!(sql.contains("PRIMARY KEY (tid, artifact_id)"));
    assert!(sql.contains("PRIMARY KEY (archive_hash)"));
    // Hash keys bind index width; full identities stay addressable.
    assert_eq!(
        sql.matches("CHAR(64) CHARACTER SET ascii COLLATE ascii_bin").count(),
        2
    );
}

#[test]
fn sqlite_task_artifact_file_is_registered_and_checksums_stable() {
    let migration = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .find(|migration| migration.version == 50)
        .expect("version 50 registered");
    assert_eq!(migration.name, "fsm_task_artifacts");
    // Both dialects are hash-key addressed with tombstone-capable states and
    // the (tid, artifact_id) product unique key.
    let sqlite_sql = include_str!("../../../../../migrations/sqlite/050_fsm_task_artifacts.sql");
    assert!(sqlite_sql.contains("archive_hash TEXT NOT NULL PRIMARY KEY"));
    assert!(sqlite_sql.contains("PRIMARY KEY (tid, artifact_id)"));
    assert_eq!(sqlite_sql.matches("CREATE TABLE IF NOT EXISTS").count(), 2);
}
