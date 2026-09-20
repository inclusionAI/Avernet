use super::*;
use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement, db_get_column};
use mysql_async::Opts;

fn write_migration(
    dir: &Path,
    name: &str,
    sql: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    fs::write(dir.join(name), sql)?;
    Ok(())
}

fn migrate_args(dir: &Path) -> MigrateArgs {
    MigrateArgs {
        dialect: None,
        migrations_dir: Some(dir.to_path_buf()),
        sqlite_path: None,
        emit_sql: true,
        check_files: false,
        check_db: false,
        apply: false,
        yes: false,
        only: Vec::new(),
        from: None,
        to: None,
    }
}

#[test]
fn emit_sql_selects_only_requested_migration() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_migration(temp_dir.path(), "001_first.sql", "SELECT 1;")?;
    write_migration(temp_dir.path(), "016_templates.sql", "SELECT 16;")?;

    let mut args = migrate_args(temp_dir.path());
    args.only = vec![16];
    let sql = emit_migration_sql(&args)?;

    assert!(sql.contains("Migration 016"));
    assert!(sql.contains("SELECT 16;"));
    assert!(!sql.contains("SELECT 1;"));
    Ok(())
}

#[test]
fn rejects_duplicate_numbers_in_selection() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_migration(temp_dir.path(), "012_a.sql", "SELECT 1;")?;
    write_migration(temp_dir.path(), "012_b.sql", "SELECT 2;")?;

    let mut args = migrate_args(temp_dir.path());
    args.only = vec![12];
    let error = load_selected_migrations(&args)
    .err()
    .ok_or_else(|| io::Error::other("expected duplicate error"))?;

    assert!(error.to_string().contains("duplicate migration numbers"));
    Ok(())
}

#[test]
fn emit_sql_allows_duplicate_numbers_for_inspection() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_migration(temp_dir.path(), "012_a.sql", "SELECT 1;")?;
    write_migration(temp_dir.path(), "012_b.sql", "SELECT 2;")?;

    let args = migrate_args(temp_dir.path());
    let sql = emit_migration_sql(&args)?;

    assert!(sql.contains("012: a"));
    assert!(sql.contains("012: b"));
    assert!(sql.contains("SELECT 1;"));
    assert!(sql.contains("SELECT 2;"));
    Ok(())
}

#[test]
fn migration_mode_requires_exactly_one_mode() {
    let mut args = migrate_args(Path::new("."));
    args.emit_sql = false;
    let error = MigrationMode::from_args(&args).expect_err("missing mode should fail");
    assert!(error.to_string().contains("choose exactly one"));

    args.emit_sql = true;
    args.check_files = true;
    let error = MigrationMode::from_args(&args).expect_err("multiple modes should fail");
    assert!(error.to_string().contains("choose exactly one"));
}

#[test]
fn mysql_check_files_validates_baseline_record() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_migration(
        temp_dir.path(),
        "001_init_schema.sql",
        "CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
            `version` int(11) NOT NULL,
            PRIMARY KEY (`version`)
        );
        INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
        VALUES (1, 'init_schema', 'mysql', 'abc');",
    )?;

    let mut args = migrate_args(temp_dir.path());
    args.emit_sql = false;
    args.check_files = true;

    let summary = check_mysql_migration_files(&args)?;

    assert!(summary.contains("MySQL/OceanBase migration files check ok"));
    assert!(summary.contains("versions=001"));
    Ok(())
}

#[test]
fn mysql_check_files_rejects_overlong_utf8mb4_index() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_migration(
        temp_dir.path(),
        "001_init_schema.sql",
        "CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
            `version` int(11) NOT NULL,
            PRIMARY KEY (`version`)
        ) DEFAULT CHARSET = utf8mb4;
        INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
        VALUES (1, 'init_schema', 'mysql', 'abc');
        CREATE TABLE IF NOT EXISTS `too_wide` (
            `name` varchar(1024) NOT NULL,
            KEY `idx_name` (`name`)
        ) DEFAULT CHARSET = utf8mb4;",
    )?;

    let mut args = migrate_args(temp_dir.path());
    args.emit_sql = false;
    args.check_files = true;
    let error = check_mysql_migration_files(&args)
        .expect_err("overlong utf8mb4 index should fail static validation");

    assert!(error.to_string().contains("too_wide.idx_name"));
    Ok(())
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn eventing_mysql_migration_applies_to_real_mysql() -> Result<()> {
    let mysql_url = std::env::var("BCS_TEST_MYSQL_URL")
        .context("BCS_TEST_MYSQL_URL must be set for the ignored migration test")?;
    let opts = Opts::from_url(&mysql_url)
        .map_err(|error| anyhow!("BCS_TEST_MYSQL_URL is invalid: {error}"))?;
    let database = opts
        .db_name()
        .ok_or_else(|| anyhow!("BCS_TEST_MYSQL_URL must include a database name"))?;
    let mut config = MysqlDbConfig::new()
        .with_database(database)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string),
            extra: BTreeMap::new(),
        })
        .with_statement_protocol(StatementProtocol::Text);
    config.pool_size = 2;
    config.min_pool_size = 1;

    let manager = MysqlDbManager::new(config)
        .await
        .map_err(|error| anyhow!("open MySQL migration test datasource: {error}"))?;
    let plugin = MysqlDbPlugin::new(manager.clone(), "bcs");
    let result = async {
        plugin
            .execute(DbStatement::new(
                "CREATE TABLE IF NOT EXISTS bcs_schema_migrations (
                    version int NOT NULL PRIMARY KEY,
                    name varchar(255) NOT NULL,
                    dialect varchar(32) NOT NULL,
                    checksum varchar(64) NOT NULL,
                    applied_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )",
            ))
            .await?;

        let mut args = migrate_args(&bcs_root().join("migrations").join("mysql"));
        args.only = vec![9];
        let migrations = load_selected_migrations(&args)?;
        let migration = migrations
            .first()
            .ok_or_else(|| anyhow!("migration 009 was not loaded"))?;
        let plan = mysql_migration_plan(migration);
        apply_mysql_migration(&plugin, migration, &plan).await?;
        apply_mysql_migration(&plugin, migration, &plan).await?;

        for table in [
            "bcs_event_subscriptions",
            "bcs_event_subscription_revisions",
            "bcs_event_scope_epochs",
            "bcs_event_streams",
            "bcs_events",
            "bcs_event_fanout_targets",
            "bcs_event_deliveries",
            "bcs_event_delivery_attempts",
            "bcs_event_subscription_audits",
        ] {
            let rows = plugin
                .query(DbStatement::with_params(
                    "SELECT COUNT(*) AS table_count FROM information_schema.tables \
                     WHERE table_schema = DATABASE() AND table_name = ?",
                    vec![DbValue::from(table)],
                ))
                .await?;
            let count: i64 = db_get_column(
                rows.first()
                    .ok_or_else(|| anyhow!("missing table count row for {table}"))?,
                "table_count",
            )?;
            if count != 1 {
                bail!("MySQL Eventing migration did not create {table}");
            }
        }

        let primary_rows = plugin
            .query(DbStatement::new(
                "SELECT column_name AS column_name FROM information_schema.statistics \
                 WHERE table_schema = DATABASE() \
                   AND table_name = 'bcs_event_scope_epochs' \
                   AND index_name = 'PRIMARY' ORDER BY seq_in_index",
            ))
            .await?;
        let primary_columns = primary_rows
            .iter()
            .map(|row| db_get_column::<String>(row, "column_name"))
            .collect::<std::result::Result<Vec<_>, _>>()?;
        if primary_columns != ["env", "scope_type", "scope_id"] {
            bail!("unexpected scope epoch primary key: {primary_columns:?}");
        }
        Ok(())
    }
    .await;
    manager.close().await;
    result
}

#[test]
fn mysql_migration_plan_uses_declared_record_checksum() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    write_migration(
        temp_dir.path(),
        "001_init_schema.sql",
        "CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
            `version` int(11) NOT NULL,
            PRIMARY KEY (`version`)
        );
        INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
        VALUES (1, 'init_schema', 'mysql', 'declared-checksum');",
    )?;

    let args = migrate_args(temp_dir.path());
    let migrations = load_selected_migrations(&args)?;
    let plan = mysql_migration_plan(&migrations[0]);

    assert_eq!(plan.checksum, "declared-checksum");
    Ok(())
}

#[test]
fn mysql_check_report_lists_pending_when_no_rows() -> Result<(), Box<dyn std::error::Error>> {
    let plans = vec![MysqlMigrationPlan {
        version: 1,
        name: "init_schema".to_string(),
        checksum: "abc".to_string(),
    }];

    let report = build_mysql_migration_report("bcs".to_string(), plans, Vec::new(), true)?;
    let summary = format_mysql_check_report(&report);

    assert!(summary.contains("current_version=<none>"));
    assert!(summary.contains("target_version=1"));
    assert!(summary.contains("pending_versions=1"));
    assert!(summary.contains("- 001 init_schema checksum=abc"));
    Ok(())
}

#[test]
fn mysql_check_report_rejects_checksum_mismatch() {
    let plans = vec![MysqlMigrationPlan {
        version: 1,
        name: "init_schema".to_string(),
        checksum: "abc".to_string(),
    }];
    let applied = vec![AppliedMysqlMigration {
        version: 1,
        name: "init_schema".to_string(),
        dialect: "mysql".to_string(),
        checksum: "bad".to_string(),
    }];

    let error = build_mysql_migration_report("bcs".to_string(), plans, applied, true)
        .expect_err("checksum mismatch should fail");

    assert!(error.to_string().contains("checksum mismatch"));
}

#[test]
fn mysql_apply_yes_confirmation_accepts_only_explicit_yes() {
    assert!(is_yes_confirmation("y"));
    assert!(is_yes_confirmation("Y\n"));
    assert!(is_yes_confirmation("yes"));
    assert!(is_yes_confirmation("YES"));
    assert!(!is_yes_confirmation(""));
    assert!(!is_yes_confirmation("n"));
    assert!(!is_yes_confirmation("sure"));
}

#[test]
fn mysql_sql_splitter_ignores_semicolons_inside_literals_and_comments() {
    let statements = split_sql_statements(
        "-- comment ;\nCREATE TABLE `a;b` (`c` varchar(10) DEFAULT ';');\n\
         INSERT INTO t VALUES ('x; y'); /* block ; */\n\
         # comment ;\n",
    );

    assert_eq!(statements.len(), 2);
    assert!(statements[0].contains("CREATE TABLE"));
    assert!(statements[0].contains("DEFAULT ';'"));
    assert!(statements[1].contains("INSERT INTO t"));
    assert!(statements[1].contains("'x; y'"));
}

#[test]
fn mysql_apply_report_lists_applied_versions() -> Result<(), Box<dyn std::error::Error>> {
    let plan = MysqlMigrationPlan {
        version: 1,
        name: "init_schema".to_string(),
        checksum: "abc".to_string(),
    };
    let report = build_mysql_migration_report(
        "bcs".to_string(),
        vec![plan.clone()],
        vec![AppliedMysqlMigration {
            version: 1,
            name: "init_schema".to_string(),
            dialect: "mysql".to_string(),
            checksum: "abc".to_string(),
        }],
        true,
    )?;

    let summary = format_mysql_apply_report(&report, &[plan]);

    assert!(summary.contains("MySQL/OceanBase migrations applied"));
    assert!(summary.contains("current_version=1"));
    assert!(summary.contains("applied_versions=1"));
    assert!(summary.contains("pending_versions=0"));
    assert!(summary.contains("- 001 init_schema checksum=abc"));
    Ok(())
}

#[tokio::test]
async fn sqlite_check_missing_file_does_not_create_file() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    let sqlite_path = temp_dir.path().join("missing.db");

    let summary = check_sqlite_migration_state(&sqlite_path).await?;

    assert!(summary.contains(&format!(
        "pending_versions={}",
        bcs::migrations::sqlite_migration_count()
    )));
    assert!(!sqlite_path.exists());
    Ok(())
}

#[test]
fn sqlite_check_files_reports_code_defined_migrations() {
    let summary = check_sqlite_migration_definitions();

    assert!(summary.contains("SQLite migration definitions check ok"));
    assert!(summary.contains(&format!(
        "target_version={}",
        bcs::migrations::sqlite_target_version()
    )));
}

#[tokio::test]
async fn sqlite_apply_records_code_defined_migrations() -> Result<(), Box<dyn std::error::Error>> {
    let temp_dir = tempfile::tempdir()?;
    let sqlite_path = temp_dir.path().join("bcs.db");
    let mut args = migrate_args(Path::new("."));
    args.dialect = Some(MigrationDialect::Sqlite);
    args.sqlite_path = Some(sqlite_path.clone());
    args.emit_sql = false;
    args.apply = true;
    let global = MigrateGlobalArgs {
        config_dir: None,
        config_file: None,
    };

    run_migrate(&args, &global).await?;

    let db = LocalSqliteDbPlugin::new_file(&sqlite_path)?;
    let rows = db
        .query(DbStatement::new(
            "SELECT version, name, dialect FROM bcs_schema_migrations ORDER BY version",
        ))
        .await?;
    assert_eq!(rows.len(), bcs::migrations::sqlite_migration_count());
    assert_eq!(db_get_column::<i64>(&rows[0], "version")?, 1);
    assert_eq!(db_get_column::<String>(&rows[0], "name")?, "init_schema");
    assert_eq!(db_get_column::<String>(&rows[0], "dialect")?, "sqlite");
    assert_eq!(db_get_column::<i64>(&rows[1], "version")?, 2);
    assert_eq!(
        db_get_column::<String>(&rows[1], "name")?,
        "channel_binding_audit_timestamps"
    );
    assert_eq!(db_get_column::<String>(&rows[1], "dialect")?, "sqlite");
    Ok(())
}
