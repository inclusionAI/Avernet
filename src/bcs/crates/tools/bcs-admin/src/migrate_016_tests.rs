use super::*;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_config_api::mysql::MysqlConnectionConfig;
use mysql_async::Opts;

const LEGACY_016: [(&str, &str, &str); 2] = [
    ("session_callback_lease", include_str!("../../../../migrations/legacy/mysql/016_session_callback_lease.sql"), "1f06ddc25a84418ec12fb2b6936f35750489996e0a9adbf35df1ef8151712abe"),
    ("chat_runs", include_str!("../../../../migrations/legacy/mysql/016_chat_runs.sql"), "f1f448f49d5cc00aaebe22e3810cd15366484c88c141e0122ee0b297fa13cfd5"),
];

fn merged_016() -> Result<Migration> {
    let args = MigrateArgs {
        dialect: Some(MigrationDialect::Mysql), migrations_dir: Some(bcs_root().join("migrations/mysql")),
        sqlite_path: None, emit_sql: false, check_files: false, check_db: false, apply: true,
        yes: true, only: vec![16], from: None, to: None,
    };
    let mut migrations = load_selected_migrations(&args)?;
    assert_eq!(migrations.len(), 1);
    Ok(migrations.remove(0))
}

#[test]
fn merged_016_preserves_both_legacy_archives_and_ddl() -> Result<()> {
    let migration = merged_016()?;
    assert_eq!(migration.name, "session_callback_lease_and_chat_runs");
    for (_, sql, checksum) in LEGACY_016 {
        assert_eq!(sha256_hex(sql.as_bytes()), checksum);
    }
    let statements = split_sql_statements(&migration.sql);
    assert_eq!(statements.len(), 2);
    assert!(statements[0].ends_with(&split_sql_statements(&LEGACY_016[0].1.replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN"))[0]));
    assert_eq!(statements[1], split_sql_statements(LEGACY_016[1].1)[0]);
    Ok(())
}

#[test]
fn merged_016_requires_reconciliation_for_either_legacy_record() -> Result<()> {
    let plan = mysql_migration_plan(&merged_016()?);
    for (name, _, checksum) in LEGACY_016 {
        let applied = AppliedMysqlMigration { version: 16, name: name.into(), dialect: "mysql".into(), checksum: checksum.into() };
        let error = build_mysql_migration_report("bcs".into(), vec![plan.clone()], vec![applied.clone()], false).unwrap_err().to_string();
        assert!(error.contains("requires explicit reconciliation"), "{error}");
        assert!(error.contains("016-session-callback-and-chat-runs.md"));
        let mut corrupted = applied;
        corrupted.checksum = "0".repeat(64);
        assert!(validate_mysql_migration_record(&plan, &corrupted).unwrap_err().to_string().contains("checksum mismatch"));
    }
    let applied = AppliedMysqlMigration { version: 16, name: plan.name.clone(), dialect: "mysql".into(), checksum: plan.checksum.clone() };
    assert!(build_mysql_migration_report("bcs".into(), vec![plan.clone()], vec![applied.clone()], false)?.pending_versions.is_empty());
    let mut corrupted = applied;
    corrupted.checksum = "0".repeat(64);
    assert!(validate_mysql_migration_record(&plan, &corrupted).unwrap_err().to_string().contains("checksum mismatch"));
    Ok(())
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable MySQL database"]
async fn merged_016_migration_applies_to_real_mysql() -> Result<()> {
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
    let result = check_merged_016(&db).await;
    manager.close().await;
    result
}

async fn check_merged_016(db: &dyn DbPlugin) -> Result<()> {
    let migration = merged_016()?;
    let plan = mysql_migration_plan(&migration);
    let baseline = split_sql_statements(include_str!("../../../../migrations/mysql/001_init_schema.sql"));
    let ddl = |table: &str| -> Result<String> {
        baseline.iter().find(|sql| sql.contains(&format!("CREATE TABLE IF NOT EXISTS `{table}`")))
            .cloned().with_context(|| format!("baseline DDL for {table}"))
    };
    db.execute(DbStatement::new(ddl("bcs_schema_migrations")?)).await?;
    let rows = db.query(DbStatement::new("SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN ('bcs_group_sessions', 'bcs_chat_runs')")).await?;
    anyhow::ensure!(db_get_column::<i64>(&rows[0], "count")? == 0, "requires absent Session/Chat tables in a disposable database");
    anyhow::ensure!(!load_applied_mysql_migrations(db).await?.iter().any(|row| row.version == 16), "migration 016 already exists; use a disposable database");
    db.execute(DbStatement::new(ddl("bcs_group_sessions")?)).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, participants) VALUES ('legacy', 'group', '[]')")).await?;
    let report = build_mysql_migration_report("bcs".into(), vec![plan.clone()], load_applied_mysql_migrations(db).await?, false)?;
    assert_eq!(report.pending_versions, vec![plan.clone()]);
    apply_mysql_migration(db, &migration, &plan).await?;
    assert!(build_mysql_migration_report("bcs".into(), vec![plan.clone()], load_applied_mysql_migrations(db).await?, false)?.pending_versions.is_empty());
    let row = db.query(DbStatement::new("SELECT callback_lease_owner, callback_lease_token, callback_lease_until_ms FROM bcs_group_sessions WHERE session_id = 'legacy'")).await?.remove(0);
    for column in ["callback_lease_owner", "callback_lease_token", "callback_lease_until_ms"] {
        assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, column)?, None);
    }
    let index = db.query(DbStatement::new("SELECT column_name AS column_name FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'bcs_group_sessions' AND index_name = 'idx_session_callback_recovery' ORDER BY seq_in_index")).await?;
    let columns = index.iter().map(|row| db_get_column::<String>(row, "column_name")).collect::<std::result::Result<Vec<_>, _>>()?;
    assert_eq!(columns, ["env", "session_kind", "status", "callback_status", "callback_lease_token", "callback_lease_until_ms", "session_id"]);
    db.execute(DbStatement::new("INSERT INTO bcs_chat_runs (env, run_id, bot_uuid, from_bot_id, session_key, state, expires_at_ms, version, response_mode, completion_policy) VALUES ('test', 'run', 'bot', 'caller', 'session', 'pending', 1, 1, 'async', 'explicit')")).await?;
    let chat = db.query(DbStatement::new("SELECT run_id FROM bcs_chat_runs WHERE env = 'test'")).await?;
    assert_eq!(db_get_column::<String>(&chat[0], "run_id")?, "run");
    db.execute(DbStatement::with_params("DELETE FROM bcs_schema_migrations WHERE version = 16 AND checksum = ?", vec![DbValue::from(plan.checksum)])).await?;
    // Neither historical name/checksum is silently relabelled, even when both
    // schema portions happen to exist. Operator reconciliation stays explicit.
    for (name, _, checksum) in LEGACY_016 {
        db.execute(DbStatement::with_params("INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (16, ?, 'mysql', ?)", vec![DbValue::from(name), DbValue::from(checksum)])).await?;
        let before = load_applied_mysql_migrations(db).await?;
        let error = build_mysql_migration_report("bcs".into(), vec![mysql_migration_plan(&migration)], before.clone(), false).unwrap_err().to_string();
        assert!(error.contains("requires explicit reconciliation"), "{error}");
        assert_eq!(load_applied_mysql_migrations(db).await?, before);
        db.execute(DbStatement::with_params("DELETE FROM bcs_schema_migrations WHERE version = 16 AND name = ? AND checksum = ?", vec![DbValue::from(name), DbValue::from(checksum)])).await?;
    }
    db.execute(DbStatement::new("DROP TABLE bcs_chat_runs")).await?;
    db.execute(DbStatement::new("DROP TABLE bcs_group_sessions")).await?;
    Ok(())
}
