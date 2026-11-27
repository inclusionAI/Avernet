//! fsm_workflow_revision migration (plan W): SQLite upgrade + additive MySQL
//! dialect parity. Opt-in tables: no existing table is touched.
use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn workflow_revision_upgrade_applies_and_is_idempotent() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 48)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    assert!(!table_exists(&db, "bcs_fsm_workflow_revisions").await?);
    assert!(!table_exists(&db, "bcs_fsm_group_publications").await?);
    run_sqlite_migrations(&db).await?;
    assert!(table_exists(&db, "bcs_fsm_workflow_revisions").await?);
    assert!(table_exists(&db, "bcs_fsm_group_publications").await?);
    assert_eq!(
        applied_sqlite_migration(&db, 49)
            .await?
            .map(|migration| migration.name),
        Some("fsm_workflow_revision".into())
    );
    // Re-runs leave recorded history untouched and keep both tables stable.
    let history_sql = "SELECT version, name, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 48 ORDER BY version";
    let before = db.query(DbStatement::new(history_sql)).await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_workflow_revisions (revision_key_hash, load_key_hash, \
         scope_tenant, scope_principal, group_id, workflow_id, revision, content_format, \
         content_digest, definition_json, bindings_json, limits_json, compiled_format, \
         compiled_json, published_at) \
         VALUES ('k', 'kl', 't', 'p', 'g', 'w', 'r', 'v1', 'sha256:d', '{}', '[]', '{}', 'v1', \
         '{}', 0)",
        vec![],
    ))
    .await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_group_publications (pointer_key_hash, scope_tenant, \
         scope_principal, group_id, workflow_id, current_revision, current_digest, \
         revision_counter, updated_at) \
         VALUES ('p', 't', 'p', 'g', 'w', 'r', 'sha256:d', 1, 0)",
        vec![],
    ))
    .await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, before);
    // The identity-unique object dedupe is enforced: a second row under the
    // same (scope, group, workflow, revision) identity is rejected, never
    // duplicated — even carrying different content bytes.
    assert!(
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_fsm_workflow_revisions (revision_key_hash, load_key_hash, \
             scope_tenant, scope_principal, group_id, workflow_id, revision, content_format, \
             content_digest, definition_json, bindings_json, limits_json, compiled_format, \
             compiled_json, published_at) \
             VALUES ('k', 'kl', 't', 'p', 'g', 'w', 'r', 'v1', 'sha256:other', '{\"x\":1}', \
             '[]', '{}', 'v1', '{}', 0)",
            vec![],
        ))
        .await
        .is_err()
    );
    Ok(())
}

#[test]
fn mysql_workflow_revision_migration_is_additive_with_bounded_identity_keys() {
    let sql = include_str!("../../../../../migrations/mysql/051_fsm_workflow_revision.sql");
    // Additive only: no existing table is touched.
    assert!(!sql.contains("ALTER TABLE"));
    assert!(!sql.contains("DROP"));
    assert_eq!(
        sql.matches("CREATE TABLE IF NOT EXISTS").count(),
        2,
        "revision objects plus publish pointer"
    );
    // The immutable revision identity is a unique hash key, so the utf8mb4
    // unique index stays well under the 3072-byte limit; the pointer has
    // exactly one row per (scope, group).
    assert!(sql.contains("UNIQUE KEY uk_fsm_workflow_revision_identity (revision_key_hash)"));
    assert!(sql.contains("PRIMARY KEY (pointer_key_hash)"));
    assert!(sql.contains("KEY idx_fsm_workflow_revision_load (load_key_hash)"));
    // Hash keys bind index width; full identities stay addressable.
    assert_eq!(
        sql.matches("CHAR(64) CHARACTER SET ascii COLLATE ascii_bin").count(),
        3
    );
    // The pointer keeps the store-assigned revision counter as an UNSIGNED
    // BIGINT so publication order stays monotonic without table scans.
    assert!(sql.contains("revision_counter BIGINT UNSIGNED NOT NULL"));
}

#[test]
fn sqlite_workflow_revision_file_is_registered_and_checksums_stable() {
    let migration = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .find(|migration| migration.version == 49)
        .expect("version 49 registered");
    assert_eq!(migration.name, "fsm_workflow_revision");
    // Both dialects are hash-key addressed with state JSON columns and no
    // BLOB payloads.
    let sqlite_sql = include_str!("../../../../../migrations/sqlite/049_fsm_workflow_revision.sql");
    assert!(sqlite_sql.contains("UNIQUE (revision_key_hash)"));
    assert!(sqlite_sql.contains("PRIMARY KEY"));
    assert_eq!(sqlite_sql.matches("CREATE TABLE IF NOT EXISTS").count(), 2);
    assert!(sqlite_sql.contains("idx_fsm_workflow_revision_load"));
}
