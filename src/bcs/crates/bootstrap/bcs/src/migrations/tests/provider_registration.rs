use super::*;

#[test]
fn provider_registration_uses_unique_versions_after_fixed_loop() {
    let sqlite = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .map(|migration| migration.version)
        .collect::<Vec<_>>();
    assert_eq!(sqlite, (1..=30).collect::<Vec<_>>());
    assert_eq!(SQLITE_VERSIONED_MIGRATIONS[28].name, "fixed_loop_runtime");
    assert_eq!(
        SQLITE_VERSIONED_MIGRATIONS[29].name,
        "provider_registrations"
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
    assert_eq!(mysql[28], "029_provider_registrations.sql");
}

#[tokio::test]
async fn provider_registration_upgrades_29_without_rewriting_history() -> DbResult<()> {
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
    let registration = applied_sqlite_migration(&db, 30).await?.unwrap();
    assert_eq!(registration.name, "provider_registrations");
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
        column_names(&db, "bcs_provider_registrations")
            .await?
            .iter()
            .any(|column| column == "record_json")
    );
    db.execute(DbStatement::new("INSERT INTO bcs_provider_registrations (env, provider_id, provider_bot_ref, bot_uuid, record_json, completed) VALUES ('test', 'provider-test', 'stable-ref', 'bot-test', '{}', 1)")).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, original);
    let rows = db
        .query(DbStatement::new(
            "SELECT completed FROM bcs_provider_registrations WHERE bot_uuid = 'bot-test'",
        ))
        .await?;
    assert_eq!(rows.len(), 1);
    assert!(db_get_column::<bool>(&rows[0], "completed")?);
    Ok(())
}
