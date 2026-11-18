use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn archive_upgrade_preserves_history_and_immutable_unique_rows() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 45)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    let history_sql = "SELECT version, name, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 45 ORDER BY version";
    let before = db.query(DbStatement::new(history_sql)).await?;
    assert!(!table_exists(&db, "bcs_dist_immutable_objects").await?);
    run_sqlite_migrations(&db).await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_dist_immutable_objects (namespace_hash, object_hash, namespace_key, object_key, content_digest, source_json, content_bytes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        vec!["namespace".into(), "object".into(), "full-namespace".into(), "full-object".into(), "digest".into(), "source".into(), DbValue::Bytes(vec![0, 1, 255])],
    )).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, before);
    let rows = db
        .query(DbStatement::new(
            "SELECT content_bytes FROM bcs_dist_immutable_objects",
        ))
        .await?;
    assert_eq!(rows.len(), 1);
    assert_eq!(
        rows[0].get("content_bytes"),
        Some(&DbValue::Bytes(vec![0, 1, 255]))
    );
    assert!(
        db.execute(DbStatement::new(
            "INSERT INTO bcs_dist_immutable_objects SELECT * FROM bcs_dist_immutable_objects"
        ))
        .await
        .is_err()
    );
    assert_eq!(
        applied_sqlite_migration(&db, 46)
            .await?
            .map(|migration| migration.name),
        Some("dist_immutable_objects".into())
    );
    Ok(())
}

#[test]
fn mysql_archive_migration_is_additive_and_uses_bounded_binary_identity_keys() {
    let sql = include_str!("../../../../../migrations/mysql/049_dist_immutable_objects.sql");
    assert!(sql.contains("PRIMARY KEY (namespace_hash, object_hash)"));
    assert!(sql.contains("content_bytes LONGBLOB NOT NULL"));
    assert_eq!(
        sql.matches("CHAR(64) CHARACTER SET ascii COLLATE ascii_bin")
            .count(),
        2
    );
    assert!(!sql.contains("ALTER TABLE"));
    assert!(!sql.contains("DROP"));
}
