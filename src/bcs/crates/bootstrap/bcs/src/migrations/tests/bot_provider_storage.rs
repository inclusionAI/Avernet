use super::*;

#[test]
fn bot_provider_storage_uses_unique_versions_after_fixed_loop() {
    let sqlite = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .map(|migration| migration.version)
        .collect::<Vec<_>>();
    assert_eq!(sqlite, (1..=30).collect::<Vec<_>>());
    assert_eq!(SQLITE_VERSIONED_MIGRATIONS[28].name, "fixed_loop_runtime");
    assert_eq!(
        SQLITE_VERSIONED_MIGRATIONS[29].name,
        "bot_provider_storage"
    );

    let directory =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../migrations/mysql");
    let mut mysql = std::fs::read_dir(directory)
        .unwrap()
        .map(|entry| {
            let path = entry.unwrap().path();
            path.file_name().unwrap().to_str().unwrap().to_owned()
        })
        .filter(|name| name.ends_with(".sql"))
        .collect::<Vec<_>>();
    mysql.sort();
    let versions = mysql
        .iter()
        .map(|name| name.split('_').next().unwrap().parse::<i64>().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(versions, (1..=29).collect::<Vec<_>>());
    assert_eq!(mysql[27], "028_fixed_loop_runtime.sql");
    assert_eq!(mysql[28], "029_bot_provider_storage.sql");
}

#[tokio::test]
async fn bot_provider_storage_upgrades_29_without_new_tables_or_rewriting_history() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 29)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(&db).await?;
    let history_sql = "SELECT version, name, dialect, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 29 ORDER BY version";
    let original = db.query(DbStatement::new(history_sql)).await?;
    assert_eq!(original.len(), 29);
    let tables_sql = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name";
    let original_tables = db.query(DbStatement::new(tables_sql)).await?;
    let pending = check_sqlite_migrations(&db).await?.pending_versions;
    assert_eq!(
        pending
            .iter()
            .map(|migration| migration.version)
            .collect::<Vec<_>>(),
        vec![30]
    );

    run_sqlite_migrations(&db).await?;
    assert_eq!(current_sqlite_version(&db, true).await?, Some(30));
    let storage = applied_sqlite_migration(&db, 30).await?.unwrap();
    assert_eq!(storage.name, "bot_provider_storage");
    assert_eq!(
        applied_sqlite_migration(&db, 29).await?.unwrap().name,
        "fixed_loop_runtime"
    );
    assert!(
        column_names(&db, "bcs_state_machine_node_runs")
            .await?
            .iter()
            .any(|column| column == "failure_action")
    );
    assert!(
        column_names(&db, "bcs_bots")
            .await?
            .iter()
            .any(|column| column == "connection_mode")
    );
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, original);
    assert_eq!(db.query(DbStatement::new(tables_sql)).await?, original_tables);
    Ok(())
}

#[tokio::test]
async fn bot_provider_expansion_resumes_every_partial_step() -> DbResult<()> {
    let statements = include_str!("../../../../../../migrations/sqlite/030_bot_provider_storage.sql")
        .split(';').map(str::trim).filter(|sql| !sql.is_empty()).collect::<Vec<_>>();
    for completed in 0..=statements.len() {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_bootstrap_tables(&db).await?;
        for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|migration| migration.version <= 29) {
            apply_sqlite_migration(&db, migration).await?;
        }
        for statement in &statements[..completed] { db.execute(DbStatement::new(*statement)).await?; }
        run_sqlite_migrations(&db).await?;
        run_sqlite_migrations(&db).await?;
        assert_eq!(current_sqlite_version(&db, true).await?, Some(30));
        assert!(index_exists(&db, "uk_bcs_bots_provider_ref_env").await?);
    }
    Ok(())
}
