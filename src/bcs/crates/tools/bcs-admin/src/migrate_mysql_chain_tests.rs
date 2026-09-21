use super::*;
use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use mysql_async::Opts;

fn chain_args() -> MigrateArgs {
    MigrateArgs {
        dialect: Some(MigrationDialect::Mysql), migrations_dir: Some(bcs_root().join("migrations/mysql")),
        sqlite_path: None, emit_sql: false, check_files: false, check_db: false, apply: true,
        yes: true, only: vec![], from: None, to: None,
    }
}

#[test]
fn mysql_syntax_revisions_preserve_exact_archives_and_schema() -> Result<()> {
    let migrations = load_selected_migrations(&chain_args())?;
    for &(version, name, current, archived) in MYSQL_SYNTAX_REVISIONS {
        let original = fs::read_to_string(bcs_root().join("migrations/legacy/mysql")
            .join(format!("{version:03}_{name}.sql")))?;
        let migration = migrations.iter().find(|migration| migration.number == version).unwrap();
        assert_eq!(sha256_hex(original.as_bytes()), archived);
        assert_eq!(mysql_migration_plan(migration).checksum, current);
        assert!(original.contains("ADD COLUMN IF NOT EXISTS"));
        assert_eq!(migration.sql, original.replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN"));
    }
    for migration in &migrations {
        assert!(!migration.sql.to_ascii_uppercase().contains("ADD COLUMN IF NOT EXISTS"), "{}", migration.name);
    }
    Ok(())
}

#[test]
fn mysql_syntax_compatibility_rejects_unknown_or_changed_revisions() -> Result<()> {
    let migrations = load_selected_migrations(&chain_args())?;
    for &(version, name, _, archived) in MYSQL_SYNTAX_REVISIONS {
        let plan = mysql_migration_plan(migrations.iter().find(|migration| migration.number == version).unwrap());
        let record = AppliedMysqlMigration {
            version: i64::from(version), name: name.into(), dialect: "mysql".into(), checksum: archived.into(),
        };
        let report = build_mysql_migration_report("test".into(), vec![plan.clone()], vec![record.clone()], true)?;
        assert!(report.pending_versions.is_empty());
        assert_eq!(report.applied_versions, vec![record.clone()]);
        for field in ["checksum", "name", "dialect"] {
            let mut changed = record.clone();
            match field {
                "checksum" => changed.checksum = "0".repeat(64),
                "name" => changed.name = "unrelated".into(),
                _ => changed.dialect = "sqlite".into(),
            }
            assert!(validate_mysql_migration_record(&plan, &changed).is_err(), "{version}: {field}");
        }
        let mut changed_plan = plan.clone();
        changed_plan.checksum = "f".repeat(64);
        assert!(validate_mysql_migration_record(&changed_plan, &record).is_err());
        let mut changed_record = record.clone();
        changed_record.version += 100;
        assert!(!is_legacy_mysql_syntax_record(&plan, &changed_record));
    }
    Ok(())
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to an empty disposable MySQL database"]
async fn full_mysql_migration_chain_applies_and_preserves_history() -> Result<()> {
    let url = std::env::var("BCS_TEST_MYSQL_URL").context("BCS_TEST_MYSQL_URL is required")?;
    let opts = Opts::from_url(&url).context("valid MySQL test URL")?;
    let mut config = MysqlDbConfig::new().with_database(opts.db_name().context("test database")?)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".into(), host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()), user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string), extra: BTreeMap::new(),
        }).with_statement_protocol(StatementProtocol::Text);
    config.pool_size = 2;
    config.min_pool_size = 1;
    let temp = tempfile::tempdir()?;
    let config_file = temp.path().join("bcs-config.json");
    let mut bcs_config = BcsConfig::default();
    bcs_config.database.database_type = DatabaseType::Mysql;
    bcs_config.database.mysql = config.clone();
    fs::write(&config_file, serde_json::to_vec(&bcs_config)?)?;
    let global = MigrateGlobalArgs { config_dir: None, config_file: Some(config_file) };
    let manager = MysqlDbManager::new(config).await?;
    let db = MysqlDbPlugin::new(manager.clone(), "bcs");
    let result = check_full_mysql_chain(&db, &global).await;
    manager.close().await;
    result
}

async fn check_full_mysql_chain(db: &dyn DbPlugin, global: &MigrateGlobalArgs) -> Result<()> {
    // The test owns an empty database exclusively. Refuse existing tables before
    // entering the cleanup scope; CI runs this before all other MySQL contracts.
    anyhow::ensure!(chain_table_names(db).await?.is_empty(), "full-chain test requires an empty disposable database");
    let args = chain_args();
    let result: Result<()> = async {
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains("applied_versions=30\npending_versions=0"), "{report}");
        let versions = load_applied_mysql_migrations(db).await?.into_iter().map(|record| record.version).collect::<Vec<_>>();
        assert_eq!(versions, (1..=30).collect::<Vec<_>>());
        assert_chain_columns(db).await?;
        let records = chain_history(db).await?;
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains("applied_versions=0\npending_versions=0"), "{report}");
        assert_eq!(chain_history(db).await?, records);

        // A completed historical prefix upgrades normally without rewriting its
        // records. The old IF NOT EXISTS spelling has the same DDL result here;
        // old checksum/timestamp rows model a prior successful deployment.
        drop_chain_tables(db).await?;
        let mut prefix = chain_args();
        prefix.to = Some(20);
        apply_mysql_migrations(&prefix, global).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_group_participants (group_id, bot_uuid, role, env, tags_json) VALUES ('chain-group', 'chain-bot', 'member', 'test', '[\"keep\"]')")).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_state_machine_definition_snapshots (env, run_id, group_id, session_id, group_version, definition_id, definition_version, definition_content_hash, snapshot_json) VALUES ('test', 'legacy-run', 'chain-group', 'chain-session', 1, 'legacy', 1, REPEAT('a', 64), '{\"version\":1}')")).await?;
        for &(version, _, current, archived) in MYSQL_SYNTAX_REVISIONS {
            let updated = db.execute(DbStatement::with_params(
                "UPDATE bcs_schema_migrations SET checksum = ?, applied_at = '2026-01-01 00:00:00' WHERE version = ? AND checksum = ?",
                vec![DbValue::from(archived), DbValue::from(i64::from(version)), DbValue::from(current)],
            )).await?;
            assert_eq!(updated.affected_rows, 1);
        }
        let records = chain_history(db).await?;
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains("applied_versions=10\npending_versions=0"), "{report}");
        assert_eq!(chain_history(db).await?.into_iter().filter(|(version, _)| *version <= 20).collect::<Vec<_>>(), records);
        assert_chain_columns(db).await?;
        let row = db.query(DbStatement::new("SELECT tags_json, message_view_scope FROM bcs_group_participants WHERE bot_uuid = 'chain-bot'")).await?.remove(0);
        assert_eq!(db_get_column::<String>(&row, "tags_json")?, "[\"keep\"]");
        assert_eq!(db_get_column::<String>(&row, "message_view_scope")?, "full");
        let row = db.query(DbStatement::new("SELECT snapshot_json, execution_plan_json FROM bcs_state_machine_definition_snapshots WHERE run_id = 'legacy-run'")).await?.remove(0);
        assert_eq!(serde_json::from_str::<serde_json::Value>(&db_get_column::<String>(&row, "snapshot_json")?)?, serde_json::json!({"version": 1}));
        assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, "execution_plan_json")?, None);
        let records = chain_history(db).await?;
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains("applied_versions=0\npending_versions=0"), "{report}");
        assert_eq!(chain_history(db).await?, records);

        db.execute(DbStatement::new("UPDATE bcs_schema_migrations SET checksum = REPEAT('0', 64) WHERE version = 2")).await?;
        let before = chain_history(db).await?;
        let error = apply_mysql_migrations(&args, global).await.expect_err("unknown historical checksum must fail");
        assert!(error.to_string().contains("checksum mismatch"), "{error}");
        assert_eq!(chain_history(db).await?, before);
        Ok(())
    }.await;
    let cleanup = drop_chain_tables(db).await;
    result?;
    cleanup
}

async fn chain_table_names(db: &dyn DbPlugin) -> Result<Vec<String>> {
    db.query(DbStatement::new("SELECT table_name AS table_name FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name"))
        .await?.iter().map(|row| Ok(db_get_column::<String>(row, "table_name")?)).collect()
}

async fn drop_chain_tables(db: &dyn DbPlugin) -> Result<()> {
    for table in chain_table_names(db).await? {
        db.execute(DbStatement::new(format!("DROP TABLE `{}`", table.replace('`', "``")))).await?;
    }
    Ok(())
}

async fn chain_history(db: &dyn DbPlugin) -> Result<Vec<(i64, String)>> {
    db.query(DbStatement::new("SELECT version, CONCAT(name, '|', dialect, '|', checksum, '|', DATE_FORMAT(applied_at, '%Y-%m-%d %H:%i:%s')) AS record FROM bcs_schema_migrations ORDER BY version"))
        .await?.iter().map(|row| Ok((db_get_column::<i64>(row, "version")?, db_get_column::<String>(row, "record")?))).collect()
}

async fn assert_chain_columns(db: &dyn DbPlugin) -> Result<()> {
    for (table, columns) in [
        ("bcs_messages", vec!["owner_bot_id", "visibility_domain", "audience_kind", "audience_actor_ids_json"]),
        ("bcs_state_machine_node_runs", vec!["outcome", "responded_by", "failure_action"]),
        ("bcs_group_participants", vec!["tags_json", "message_view_scope"]),
        ("bcs_bots", vec!["task_claim_mode", "task_dream_mode", "user_visibility", "friend_ext", "friend_check_in_strategy", "provider_id", "provider_bot_ref", "connection_mode", "webhook_url", "provider_registered_at", "provider_updated_at"]),
        ("bcs_state_machine_runs", vec!["root_run_id", "rerun_of", "session_activation_count", "opening_message_override_json"]),
        ("bcs_group_sessions", vec!["message_visibility_version", "callback_lease_owner", "callback_lease_token", "callback_lease_until_ms"]),
        ("bcs_state_machine_definition_snapshots", vec!["execution_plan_json", "execution_plan_content_hash", "execution_plan_compiler_version"]),
        ("bcs_provider_registrations", vec!["provider_id", "provider_bot_ref", "bot_uuid", "record_json", "completed"]),
    ] {
        let rows = db.query(DbStatement::with_params("SELECT column_name AS column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ?", vec![DbValue::from(table)])).await?;
        let actual = rows.iter().map(|row| db_get_column::<String>(row, "column_name")).collect::<bcs_db_api::DbResult<Vec<_>>>()?;
        for column in columns { assert!(actual.iter().any(|actual| actual == column), "missing {table}.{column}"); }
    }
    Ok(())
}
