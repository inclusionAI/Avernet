use super::*;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_config_api::mysql::MysqlConnectionConfig;
use mysql_async::Opts;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable MySQL database"]
async fn fixed_loop_migration_applies_to_real_mysql() -> Result<()> {
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
    let manager = MysqlDbManager::new(config).await?;
    let db = MysqlDbPlugin::new(manager.clone(), "bcs");
    let result = check_fixed_loop_migration(&db).await;
    manager.close().await;
    result
}

async fn check_fixed_loop_migration(db: &dyn DbPlugin) -> Result<()> {
    let args = MigrateArgs {
        dialect: Some(MigrationDialect::Mysql), migrations_dir: Some(bcs_root().join("migrations/mysql")),
        sqlite_path: None, emit_sql: false, check_files: false, check_db: false, apply: true,
        yes: true, only: vec![28], from: None, to: None,
    };
    let migration = load_selected_migrations(&args)?.remove(0);
    let plan = mysql_migration_plan(&migration);
    db.execute(DbStatement::new("CREATE TABLE IF NOT EXISTS bcs_schema_migrations (version INT NOT NULL PRIMARY KEY, name VARCHAR(255) NOT NULL, dialect VARCHAR(32) NOT NULL, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")).await?;
    let tables = ["bcs_state_machine_definition_snapshots", "bcs_state_machine_node_runs", "bcs_state_machine_runs", "bcs_group_sessions"];
    for table in tables.into_iter().chain(["bcs_collaboration_delivery_checkpoints"]) {
        let rows = db.query(DbStatement::with_params("SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?", vec![DbValue::from(table)])).await?;
        anyhow::ensure!(db_get_column::<i64>(&rows[0], "count")? == 0, "requires absent Loop table {table}");
    }
    anyhow::ensure!(!load_applied_mysql_migrations(db).await?.iter().any(|row| row.version == 28), "migration 028 already exists; use a disposable test database");
    // A failed DDL must never be recorded as a successfully applied migration.
    assert!(apply_mysql_migration(db, &migration, &plan).await.is_err());
    assert!(!load_applied_mysql_migrations(db).await?.iter().any(|row| row.version == 28));
    // The consolidated 028 alters four baseline tables and creates a checkpoint table. Use their original baseline
    // CREATE statements through the production splitter for the focused test.
    let baseline = split_sql_statements(include_str!("../../../../migrations/mysql/001_init_schema.sql"));
    let mut creates = Vec::new();
    for table in tables {
        let sql = baseline.iter().find(|sql| sql.contains(&format!("CREATE TABLE IF NOT EXISTS `{table}`")))
            .with_context(|| format!("baseline DDL for {table}"))?;
        creates.push((table, sql.clone()));
    }
    for legacy in [false, true] {
        for (_, sql) in &creates { db.execute(DbStatement::new(sql.clone())).await?; }
        if legacy {
            db.execute(DbStatement::new("INSERT INTO bcs_state_machine_definition_snapshots (env, run_id, group_id, session_id, group_version, definition_id, definition_version, definition_content_hash, snapshot_json) VALUES ('test', 'legacy', 'group', 'session', 1, 'legacy', 1, REPEAT('a', 64), JSON_OBJECT('version', 1))")).await?;
        }
        let before = build_mysql_migration_report("bcs".into(), vec![plan.clone()], load_applied_mysql_migrations(db).await?, false)?;
        assert_eq!(before.pending_versions.len(), 1);
        apply_mysql_migration(db, &migration, &plan).await?;
        // Repeated operator apply uses the stored plan/record comparison and
        // has no pending DDL; it does not blindly replay ALTER ADD COLUMN.
        for _ in 0..2 {
            let report = build_mysql_migration_report("bcs".into(), vec![plan.clone()], load_applied_mysql_migrations(db).await?, false)?;
            assert!(report.pending_versions.is_empty());
        }
        let columns = db.query(DbStatement::new("SELECT column_name AS column_name, data_type AS data_type, is_nullable AS is_nullable, character_maximum_length AS max_length FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'bcs_state_machine_definition_snapshots' AND column_name LIKE 'execution_plan_%' ORDER BY column_name")).await?;
        assert_eq!(columns.len(), 3);
        for row in &columns {
            assert_eq!(db_get_column::<String>(row, "is_nullable")?, "YES");
            match db_get_column::<String>(row, "column_name")?.as_str() {
                "execution_plan_json" => assert_eq!(db_get_column::<String>(row, "data_type")?, "json"),
                "execution_plan_content_hash" => {
                    assert_eq!(db_get_column::<String>(row, "data_type")?, "char");
                    assert_eq!(db_get_column::<i64>(row, "max_length")?, 64);
                }
                "execution_plan_compiler_version" => {
                    assert_eq!(db_get_column::<String>(row, "data_type")?, "varchar");
                    assert_eq!(db_get_column::<i64>(row, "max_length")?, 128);
                }
                _ => unreachable!(),
            }
        }
        if legacy {
            let row = db.query(DbStatement::new("SELECT snapshot_json, execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version FROM bcs_state_machine_definition_snapshots WHERE run_id = 'legacy'")).await?.remove(0);
            for field in ["execution_plan_json", "execution_plan_content_hash", "execution_plan_compiler_version"] {
                assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, field)?, None);
            }
            assert_eq!(serde_json::from_str::<serde_json::Value>(&db_get_column::<String>(&row, "snapshot_json")?)?, serde_json::json!({"version": 1}));
        }
        let action = db.query(DbStatement::new("SELECT data_type AS data_type, is_nullable AS is_nullable FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'bcs_state_machine_node_runs' AND column_name = 'failure_action'")).await?;
        assert_eq!(action.len(), 1);
        assert_eq!(db_get_column::<String>(&action[0], "data_type")?, "varchar");
        assert_eq!(db_get_column::<String>(&action[0], "is_nullable")?, "YES");
        for (name, expected_type, nullable) in [
            ("runtime_phase", "varchar", "YES"), ("recovery_lease_owner", "varchar", "YES"),
            ("recovery_lease_token", "bigint", "NO"), ("recovery_lease_until_ms", "bigint", "YES"),
        ] {
            let columns = db.query(DbStatement::with_params("SELECT data_type AS data_type, is_nullable AS is_nullable FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'bcs_state_machine_node_runs' AND column_name = ?", vec![DbValue::from(name)])).await?;
            assert_eq!(columns.len(), 1);
            assert_eq!(db_get_column::<String>(&columns[0], "data_type")?, expected_type);
            assert_eq!(db_get_column::<String>(&columns[0], "is_nullable")?, nullable);
        }
        for (table, index, expected) in [
            ("bcs_state_machine_runs", "idx_sm_runs_progression", vec!["env", "status", "record_status", "run_id"]),
            ("bcs_group_sessions", "idx_session_running_recovery", vec!["env", "session_kind", "status", "session_id"]),
            ("bcs_collaboration_delivery_checkpoints", "idx_collaboration_checkpoints_run", vec!["env", "aggregate_id", "operation_kind", "status"]),
            ("bcs_collaboration_delivery_checkpoints", "idx_collaboration_terminal_im_scan", vec!["env", "operation_kind", "status", "aggregate_id"]),
        ] {
            let rows = db.query(DbStatement::with_params("SELECT column_name AS column_name FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ? ORDER BY seq_in_index", vec![DbValue::from(table), DbValue::from(index)])).await?;
            let actual = rows.iter().map(|row| db_get_column::<String>(row, "column_name")).collect::<bcs_db_api::DbResult<Vec<_>>>()?;
            assert_eq!(actual, expected);
        }
        let mut changed_plan = plan.clone();
        let checkpoint_columns = db.query(DbStatement::new("SELECT column_name AS column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'bcs_collaboration_delivery_checkpoints' ORDER BY ordinal_position")).await?;
        assert_eq!(checkpoint_columns.iter().map(|row| db_get_column::<String>(row, "column_name")).collect::<bcs_db_api::DbResult<Vec<_>>>()?,
            ["env", "operation_key", "aggregate_kind", "aggregate_id", "operation_kind", "payload_json", "progress_json", "status", "created_at_ms", "delivered_at_ms", "node_id", "aggregate_attempt", "deadline_ms", "lease_owner", "lease_token", "lease_until_ms", "last_error"]);
        let checkpoint_pk = db.query(DbStatement::new("SELECT column_name AS column_name FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'bcs_collaboration_delivery_checkpoints' AND index_name = 'PRIMARY' ORDER BY seq_in_index")).await?;
        assert_eq!(checkpoint_pk.iter().map(|row| db_get_column::<String>(row, "column_name")).collect::<bcs_db_api::DbResult<Vec<_>>>()?, ["env", "operation_key"]);
        changed_plan.checksum = "0".repeat(64);
        assert!(build_mysql_migration_report("bcs".into(), vec![changed_plan], load_applied_mysql_migrations(db).await?, false).is_err());
        db.execute(DbStatement::with_params("DELETE FROM bcs_schema_migrations WHERE version = 28 AND checksum = ?", vec![DbValue::from(plan.checksum.clone())])).await?;
        for table in tables.into_iter().chain(["bcs_collaboration_delivery_checkpoints"]) { db.execute(DbStatement::new(format!("DROP TABLE `{table}`"))).await?; }
    }
    // A late failure must not record the whole bundle as complete, even when
    // the earlier column/index DDL has committed. Only this test's tables exist.
    for (table, sql) in &creates {
        if *table != "bcs_group_sessions" { db.execute(DbStatement::new(sql.clone())).await?; }
    }
    let error = apply_mysql_migration(db, &migration, &plan).await.expect_err("missing Session prerequisite must fail");
    assert!(error.to_string().contains("statement 4"), "{error}");
    assert!(!load_applied_mysql_migrations(db).await?.iter().any(|row| row.version == 28));
    let columns = db.query(DbStatement::new("SELECT failure_action FROM bcs_state_machine_node_runs")).await?;
    assert!(columns.is_empty());
    for table in tables {
        if table != "bcs_group_sessions" { db.execute(DbStatement::new(format!("DROP TABLE `{table}`"))).await?; }
    }
    // Failure at the newly added final CREATE must leave the bundle unrecorded.
    for (_, sql) in &creates { db.execute(DbStatement::new(sql.clone())).await?; }
    db.execute(DbStatement::new("CREATE TABLE bcs_collaboration_delivery_checkpoints (placeholder INT)")).await?;
    let error = apply_mysql_migration(db, &migration, &plan).await.expect_err("conflicting checkpoint table must fail");
    assert!(error.to_string().contains("statement 5"), "{error}");
    assert!(!load_applied_mysql_migrations(db).await?.iter().any(|row| row.version == 28));
    for table in tables.into_iter().chain(["bcs_collaboration_delivery_checkpoints"]) {
        db.execute(DbStatement::new(format!("DROP TABLE `{table}`"))).await?;
    }
    Ok(())
}
