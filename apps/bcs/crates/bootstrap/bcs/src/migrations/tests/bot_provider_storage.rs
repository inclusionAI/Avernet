use super::*;

#[test]
fn bot_provider_storage_uses_unique_versions_after_fixed_loop() {
    let sqlite = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .map(|migration| migration.version)
        .collect::<Vec<_>>();
    assert_eq!(sqlite, (1..=38).collect::<Vec<_>>());
    assert_eq!(SQLITE_VERSIONED_MIGRATIONS[28].name, "fixed_loop_runtime");
    assert_eq!(SQLITE_VERSIONED_MIGRATIONS[29].name, "bot_provider_storage");
    // The new Group migration owns SQLite version 31 (Provider storage keeps
    // version 30).
    assert_eq!(
        SQLITE_VERSIONED_MIGRATIONS[30].name,
        "group_human_mention_notify_mode"
    );

    // Suites that include this file from crates at other
    // depths (e.g. `crates/application/v1/*`) share the
    // repository layout: the SQL directory sits above
    // `crates/`, so try the known manifest-relative roots.
    let directory = [
        "../../../migrations/mysql",
        "../../../../migrations/mysql",
    ]
    .iter()
    .map(|base| std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(base))
    .find(|candidate| candidate.is_dir())
    .expect("migrations/mysql directory resolves");
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
    // MySQL Provider storage remains version 029; the Group
    // human-mention migration is MySQL version 030, Group context
    // owners is 034, collaboration drafts is 035, bot authority is 036,
    // session speaking leases is 037 and task-handle lookup is 038.
    assert_eq!(versions, (1..=38).collect::<Vec<_>>());
    assert_eq!(mysql[27], "028_fixed_loop_runtime.sql");
    assert_eq!(mysql[28], "029_bot_provider_storage.sql");
    assert_eq!(mysql[29], "030_group_human_mention_notify_mode.sql");
    assert_eq!(mysql[30], "031_session_registry.sql");
    assert_eq!(mysql[31], "032_provider_slug.sql");
    assert_eq!(mysql[32], "033_group_context.sql");
    assert_eq!(mysql[33], "034_group_context_owners.sql");
    assert_eq!(mysql[34], "035_collaboration_drafts.sql");
    assert_eq!(mysql[35], "036_bot_authority.sql");
    assert_eq!(mysql[36], "037_session_speaking_leases.sql");
    assert_eq!(mysql[37], "038_task_handle_lookup.sql");
}

#[tokio::test]
async fn bot_provider_storage_upgrades_29_without_new_tables_or_rewriting_history() -> DbResult<()>
{
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
    // The upgrade path stays focused on Provider storage: pending versions
    // begin with the Provider storage step (the version-31 Group migration and later tails
    // follow, asserted by the final runner assertions below).
    assert_eq!(
        pending
            .iter()
            .map(|migration| migration.version)
            .collect::<Vec<_>>(),
        vec![30, 31, 32, 33, 34, 35, 36, 37, 38]
    );

    run_sqlite_migrations(&db).await?;
    // The full runner includes the Session registry migration.
    assert_eq!(
        current_sqlite_version(&db, true).await?,
        Some(sqlite_target_version())
    );
    let storage = applied_sqlite_migration(&db, 30).await?.unwrap();
    assert_eq!(storage.name, "bot_provider_storage");
    assert_eq!(
        applied_sqlite_migration(&db, 29).await?.unwrap().name,
        "fixed_loop_runtime"
    );
    assert!(column_names(&db, "bcs_state_machine_node_runs")
        .await?
        .iter()
        .any(|column| column == "failure_action"));
    assert!(column_names(&db, "bcs_bots")
        .await?
        .iter()
        .any(|column| column == "connection_mode"));
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, original);
    assert!(column_names(&db, "bcs_collaboration_drafts").await?.iter().any(|c| c == "head_revision"));
    assert!(column_names(&db, "bcs_collaboration_draft_revisions").await?.iter().any(|c| c == "payload_json"));
    // The version-30+ tail legitimately adds: the Session-registry
    // table (32), Group context (34), the collaboration-draft
    // tables (36) and the bot-authority tables (37); the Provider
    // storage upgrade itself adds no tables.
    let tables = db.query(DbStatement::new("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN ('bcs_session_registry', 'bcs_group_contexts', 'bcs_collaboration_drafts', 'bcs_collaboration_draft_revisions', 'bot_ownership_transfers', 'bot_authority_changes') ORDER BY name")).await?;
    assert_eq!(tables, original_tables);
    Ok(())
}

#[tokio::test]
async fn bot_provider_expansion_resumes_every_partial_step() -> DbResult<()> {
    let statements =
        include_str!("../../../../../../migrations/sqlite/030_bot_provider_storage.sql")
            .split(';')
            .map(str::trim)
            .filter(|sql| !sql.is_empty())
            .collect::<Vec<_>>();
    for completed in 0..=statements.len() {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_bootstrap_tables(&db).await?;
        for migration in SQLITE_VERSIONED_MIGRATIONS
            .iter()
            .filter(|migration| migration.version <= 29)
        {
            apply_sqlite_migration(&db, migration).await?;
        }
        for statement in &statements[..completed] {
            db.execute(DbStatement::new(*statement)).await?;
        }
        run_sqlite_migrations(&db).await?;
        run_sqlite_migrations(&db).await?;
        assert_eq!(
            current_sqlite_version(&db, true).await?,
            Some(sqlite_target_version())
        );
        assert!(index_exists(&db, "uk_bcs_bots_provider_ref_env").await?);
    }
    Ok(())
}

#[test]
fn mysql_speaking_lease_migration_is_additive_and_unique_scoped() {
    let baseline = include_str!("../../../../../../migrations/mysql/001_init_schema.sql");
    let migration = include_str!("../../../../../../migrations/mysql/037_session_speaking_leases.sql");

    assert!(
        !baseline.contains("bcs_session_speaking_leases"),
        "Caller speaking leases must remain a versioned MySQL migration, not a baseline edit"
    );
    assert!(migration.contains("CREATE TABLE IF NOT EXISTS `bcs_session_speaking_leases`"));
    for column in [
        "`env`",
        "`session_id`",
        "`holder_actor_id`",
        "`lease_id`",
        "`epoch`",
        "`expires_at_ms`",
        "`released`",
    ] {
        assert!(migration.contains(column), "missing lease column {column}");
    }
    assert!(migration.contains("UNIQUE KEY `uk_speaking_leases_env_session` (`env`, `session_id`)"));
    assert!(migration.contains("KEY `idx_speaking_leases_session` (`env`, `session_id`)"));
}
