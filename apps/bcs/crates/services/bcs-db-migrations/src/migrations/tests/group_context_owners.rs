//! Regression coverage for adding per-version Group Context owners.
use super::*;

#[tokio::test]
async fn sqlite_group_context_owners_upgrade_adds_nullable_column_without_rewriting_rows()
-> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 34)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(&db).await?;

    let insert_sql = r#"
INSERT INTO bcs_group_contexts
    (context_id, name, scope_level, scope, content, creator, visible_to_json, collect_from_json,
     supersedes, change_reason, valid_from, valid_to, is_superseded, gmt_superseded, tx_time,
     gmt_create, gmt_modified)
VALUES ('ctx-owners-upgrade', 'owner_upgrade', 'session', 'sess-upgrade', 'v1', 'writer-a',
        '{"userIds":["writer-a"]}', '{"userIds":["writer-a"]}', NULL, 'v1',
        '2026-10-19 00:00:00.000', NULL, 1, '2026-10-19 01:00:00.000',
        '2026-10-19 01:00:00.000', '2026-10-19 00:00:00.000', '2026-10-19 01:00:00.000'),
       ('ctx-owners-upgrade', 'owner_upgrade', 'session', 'sess-upgrade', 'v2', 'writer-b',
        '{"userIds":["writer-b"]}', '{"userIds":["writer-b"]}', 1, 'v2',
        '2026-10-19 01:00:00.000', NULL, 0, NULL, '2026-10-19 01:00:00.000',
        '2026-10-19 01:00:00.000', '2026-10-19 01:00:00.000')
"#;
    db.execute(DbStatement::new(insert_sql)).await?;

    run_sqlite_migrations(&db).await?;

    let columns = column_names(&db, "bcs_group_contexts").await?;
    assert!(columns.iter().any(|column| column == "owners_json"));
    let schema_rows = db
        .query(DbStatement::new("PRAGMA table_info(bcs_group_contexts)"))
        .await?;
    let owners_column = schema_rows
        .iter()
        .find(|row| {
            db_get_column::<String>(row, "name")
                .map(|name| name == "owners_json")
                .unwrap_or(false)
        })
        .expect("owners_json column metadata");
    assert_eq!(db_get_column::<i64>(owners_column, "notnull")?, 0);
    assert_eq!(
        bcs_db_api::db_get_column_opt::<String>(owners_column, "dflt_value")?,
        None
    );

    let rows = db
        .query(DbStatement::new(
            "SELECT creator, owners_json FROM bcs_group_contexts ORDER BY id",
        ))
        .await?;
    assert_eq!(rows.len(), 2);
    assert_eq!(db_get_column::<String>(&rows[0], "creator")?, "writer-a");
    assert_eq!(
        bcs_db_api::db_get_column_opt::<String>(&rows[0], "owners_json")?,
        None
    );
    assert_eq!(db_get_column::<String>(&rows[1], "creator")?, "writer-b");
    assert_eq!(
        bcs_db_api::db_get_column_opt::<String>(&rows[1], "owners_json")?,
        None
    );

    let expected_checksum = sqlite_migration_checksum(
        SQLITE_VERSIONED_MIGRATIONS
            .iter()
            .find(|migration| migration.version == 35)
            .expect("group_context_owners migration listed"),
    );
    let record = applied_sqlite_migration(&db, 35)
        .await?
        .expect("owners migration recorded");
    assert_eq!(record.name, "group_context_owners");
    assert_eq!(record.dialect, "sqlite");
    assert_eq!(record.checksum, expected_checksum);

    let history = db
        .query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version",
        ))
        .await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(
        db.query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version"
        ))
        .await?,
        history
    );
    Ok(())
}

#[test]
fn mysql_group_context_owners_migration_adds_nullable_column_without_backfill() {
    let migration = include_str!("../../../../../../migrations/mysql/034_group_context_owners.sql");
    assert_eq!(
        migration.trim(),
        "ALTER TABLE bcs_group_contexts\n    ADD COLUMN owners_json VARCHAR(4096) NULL;"
    );
}

#[test]
fn oceanbase_group_context_owners_migration_adds_nullable_column_without_backfill() {
    let migration =
        include_str!("../../../../../../migrations/oceanbase/032_group_context_owners.sql");
    assert_eq!(
        migration.trim(),
        "ALTER TABLE bcs_group_contexts\n    ADD COLUMN owners_json JSON NULL;"
    );
}
