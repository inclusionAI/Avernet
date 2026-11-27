//! fsm_node_history migration (plan T3): SQLite upgrade + additive MySQL
//! dialect parity. Opt-in tables: no existing table is touched.
use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn node_history_upgrade_applies_and_is_idempotent() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 47)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    assert!(!table_exists(&db, "bcs_fsm_node_history").await?);
    assert!(!table_exists(&db, "bcs_fsm_node_input_pins").await?);
    run_sqlite_migrations(&db).await?;
    assert!(table_exists(&db, "bcs_fsm_node_history").await?);
    assert!(table_exists(&db, "bcs_fsm_node_input_pins").await?);
    assert_eq!(
        applied_sqlite_migration(&db, 48)
            .await?
            .map(|migration| migration.name),
        Some("fsm_node_history".into())
    );
    // Re-runs leave recorded history untouched and keep both tables stable.
    let history_sql = "SELECT version, name, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 47 ORDER BY version";
    let before = db.query(DbStatement::new(history_sql)).await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_node_history (node_key_hash, scope_tenant, scope_principal, \
         group_id, sid, workflow_id, workflow_revision, workflow_digest, node_id, \
         interaction_id, entry_json, appended_at) \
         VALUES ('k', 't', 'p', 'g', 's', 'w', 'r', 'd', 'n', 'i-1', '{}', 0)",
        vec![],
    ))
    .await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_fsm_node_input_pins (pin_key_hash, scope_tenant, scope_principal, \
         group_id, sid, tid, round_generation, node_id, cut_json, entries_json, history_digest, \
         truncated, budget_rows, state, pinned_at) \
         VALUES ('pin', 't', 'p', 'g', 's', 'tid', 1, 'n', '{}', '[]', 'sha256:d', 0, 100, \
         'pinned', 0)",
        vec![],
    ))
    .await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, before);
    // The identity-unique dedupe is enforced: a second row with the same
    // stable interaction identity is rejected, never duplicated.
    assert!(
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_fsm_node_history (node_key_hash, scope_tenant, scope_principal, \
             group_id, sid, workflow_id, workflow_revision, workflow_digest, node_id, \
             interaction_id, entry_json, appended_at) \
             VALUES ('k', 't', 'p', 'g', 's', 'w', 'r', 'd', 'n', 'i-1', '{}', 0)",
            vec![],
        ))
        .await
        .is_err()
    );
    Ok(())
}

#[test]
fn mysql_node_history_migration_is_additive_with_bounded_identity_keys() {
    let sql = include_str!("../../../../../migrations/mysql/050_fsm_node_history.sql");
    // Additive only: no existing table is touched.
    assert!(!sql.contains("ALTER TABLE"));
    assert!(!sql.contains("DROP"));
    assert_eq!(
        sql.matches("CREATE TABLE IF NOT EXISTS").count(),
        2,
        "history rows plus input pins"
    );
    // Identity-unique dedupe of one node's interactions is a hashed pair, so
    // the utf8mb4 unique index stays well under the 3072-byte limit.
    assert!(
        sql.contains("UNIQUE (node_key_hash, interaction_id)")
            || sql.contains("UNIQUE KEY uk_fsm_node_history_identity (node_key_hash, interaction_id)")
    );
    assert!(
        sql.contains("PRIMARY KEY (pin_key_hash)"),
        "one pin per (round, node) identity"
    );
    // Hash keys bind index width; full identities stay addressable.
    assert_eq!(sql.matches("CHAR(64) CHARACTER SET ascii COLLATE ascii_bin").count(), 2);
}

#[test]
fn sqlite_node_history_file_is_registered_and_checksums_stable() {
    let migration = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .find(|migration| migration.version == 48)
        .expect("version 48 registered");
    assert_eq!(migration.name, "fsm_node_history");
    // Both dialects are hash-key addressed with BLOB-free state JSON columns.
    let sqlite_sql = include_str!("../../../../../migrations/sqlite/048_fsm_node_history.sql");
    assert!(sqlite_sql.contains("UNIQUE (node_key_hash, interaction_id)"));
    assert!(sqlite_sql.contains("PRIMARY KEY"));
    assert_eq!(sqlite_sql.matches("CREATE TABLE IF NOT EXISTS").count(), 2);
}
