use super::*;
use bcs_config_api::{MysqlDbConfig, mysql::MysqlConnectionConfig};

fn sqlite_args(path: &Path) -> MigrateArgs {
    MigrateArgs { dialect: None, migrations_dir: None, sqlite_path: Some(path.into()), emit_sql: true,
        check_files: false, check_db: false, apply: false, yes: true, only: vec![], from: None, to: None }
}

#[tokio::test]
async fn coverage_sqlite_diagnostics_are_read_only_and_apply_clears_pending_versions() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join("migration.db");
    let global = MigrateGlobalArgs { config_dir: None, config_file: None };
    let mut args = sqlite_args(&path);
    let missing = emit_sqlite_migration_plan(&path).await?;
    assert!(missing.contains("file does not exist"));
    assert!(check_sqlite_migration_state(&path).await?.contains("current_version=<none>"));
    run_migrate(&args, &global).await?;
    assert!(!path.exists(), "diagnostics must not create a database");
    // An existing empty database needs a diagnostic plan for every migration.
    let db = open_sqlite_db(&path)?;
    db.execute(DbStatement::new("CREATE TABLE coverage_marker (id INTEGER)")).await?;
    let pending = emit_sqlite_migration_plan(&path).await?;
    assert!(pending.contains("Migration 001:"));
    assert!(pending.contains("checksum="));
    assert!(check_sqlite_migration_state(&path).await?.contains("current_version=<none>"));
    args.emit_sql = false; args.check_files = true;
    run_migrate(&args, &global).await?;
    args.check_files = false; args.apply = true;
    run_migrate(&args, &global).await?;
    assert!(emit_sqlite_migration_plan(&path).await?.contains("No pending SQLite migrations"));
    let checked = check_sqlite_migration_state(&path).await?;
    assert!(checked.contains("pending_versions=0"), "{checked}");
    args.apply = false; args.check_db = true;
    run_migrate(&args, &global).await?;
    let marker = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM coverage_marker")).await?;
    assert_eq!(db_get_column::<i64>(&marker[0], "n")?, 0);
    Ok(())
}

#[test]
fn coverage_migration_dialect_uses_config_and_rejects_unsupported_backends() -> Result<()> {
    let dir = tempfile::tempdir()?;
    let config_file = dir.path().join("config.json");
    let global = MigrateGlobalArgs { config_dir: None, config_file: Some(config_file.clone()) };
    let mut args = sqlite_args(Path::new("override.db")); args.sqlite_path = None;
    for (kind, expected) in [(DatabaseType::Sqlite, MigrationDialect::Sqlite), (DatabaseType::Mysql, MigrationDialect::Mysql)] {
        let mut config = BcsConfig::default(); config.database.database_type = kind;
        config.database.sqlite.path = "configured.db".into();
        fs::write(&config_file, serde_json::to_vec(&config)?)?;
        assert_eq!(resolve_dialect(&args, &global, MigrationMode::CheckDb)?, expected);
        assert_eq!(resolve_sqlite_path(&args, &global)?, Path::new("configured.db"));
    }
    let mut config = BcsConfig::default(); config.database.database_type = DatabaseType::Other("unsupported".into());
    fs::write(&config_file, serde_json::to_vec(&config)?)?;
    assert!(resolve_dialect(&args, &global, MigrationMode::Apply).unwrap_err().to_string().contains("not supported"));
    fs::write(&config_file, "not a config")?;
    assert!(load_bcs_config(&global).unwrap_err().to_string().contains("load config file"));
    Ok(())
}

#[test]
fn coverage_sql_splitter_preserves_delimiters_inside_quotes_and_comments() {
    let first = r#"SELECT 'it''s; \'quoted', "a"";\\b", `a``;`"#;
    let second = "-- ; ignored\n# ; ignored\n/* ; ignored */ SELECT 2";
    assert_eq!(split_sql_statements(&format!("{first}; {second}; /* comment only */; # last comment")), [first, second]);
    assert!(split_sql_statements("-- only\n# comments\n/* no SQL */").is_empty());
}

#[test]
fn coverage_mysql_confirmation_identifies_pending_changes_and_defaults_to_refusal() {
    let report = MysqlMigrationCheckReport { datasource: "fixture".into(), current_version: None, target_version: None,
        pending_versions: vec![MysqlMigrationPlan { version: 2, name: "add_body".into(), checksum: "abc".into() }],
        applied_versions: vec![], ignored_extra_versions: vec![] };
    let prompt = format_mysql_apply_confirmation(&report);
    assert!(prompt.contains("current_version=<none>\ntarget_version=<none>\npending_versions=1"));
    assert!(prompt.contains("002 add_body checksum=abc"));
    assert!(format_mysql_apply_cancelled(&report).contains("cancelled\ndatasource=fixture\npending_versions=1"));
    assert!(confirm_mysql_apply(true, &report).unwrap());
    for yes in ["y", " YES \n"] { assert!(is_yes_confirmation(yes)); }
    for no in ["", "\n", "no", "true"] { assert!(!is_yes_confirmation(no)); }
}

#[tokio::test]
async fn coverage_mysql_admin_apply_is_idempotent_and_refuses_changed_history() -> Result<()> {
    let url = match std::env::var("BCS_TEST_MYSQL_URL") {
        Ok(url) => url,
        Err(_) if std::env::var_os("CI").is_none() => {
            eprintln!("MySQL migration regression not run: set BCS_TEST_MYSQL_URL"); return Ok(());
        }
        Err(error) => return Err(error.into()),
    };
    let opts = mysql_async::Opts::from_url(&url)?;
    let config = MysqlDbConfig::new().with_database(opts.db_name().context("test database")?)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".into(), host: Some(opts.ip_or_hostname().into()), port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string), password: opts.pass().map(str::to_string), extra: BTreeMap::new(),
        });
    let manager = MysqlDbManager::new(config.clone()).await?;
    // CI grants its test user access to this isolated contract database, not
    // permission to create additional schemas. Refuse an occupied migration
    // table before making any changes; never overwrite existing history.
    let occupied = manager.query("bcs", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN ('bcs_schema_migrations', 'coverage_admin_items')").await?;
    anyhow::ensure!(occupied[0].get::<u64, _>(0) == Some(0), "BCS_TEST_MYSQL_URL must name an isolated database without migration history");
    let result = async {
        let dir = tempfile::tempdir()?;
        let migrations = dir.path().join("migrations"); fs::create_dir(&migrations)?;
        fs::write(migrations.join("001_initial.sql"), "CREATE TABLE bcs_schema_migrations (version BIGINT PRIMARY KEY, name VARCHAR(128), dialect VARCHAR(16), checksum VARCHAR(64)); CREATE TABLE coverage_admin_items (id BIGINT PRIMARY KEY);")?;
        fs::write(migrations.join("002_body.sql"), "ALTER TABLE coverage_admin_items ADD COLUMN body TEXT;")?;
        let mut bcs = BcsConfig::default(); bcs.database.database_type = DatabaseType::Mysql;
        bcs.database.mysql = config;
        let config_file = dir.path().join("bcs-config.json"); fs::write(&config_file, serde_json::to_vec(&bcs)?)?;
        let global = MigrateGlobalArgs { config_dir: None, config_file: Some(config_file) };
        let mut args = MigrateArgs { dialect: Some(MigrationDialect::Mysql), migrations_dir: Some(migrations.clone()),
            sqlite_path: None, emit_sql: false, check_files: false, check_db: false, apply: true,
            yes: true, only: Vec::new(), from: None, to: None };
        let before = check_mysql_migration_state(&args, &global).await?;
        assert!(before.contains("pending_versions=2"), "{before}");
        let applied = apply_mysql_migrations(&args, &global).await?;
        assert!(applied.contains("applied_versions=2\npending_versions=0"), "{applied}");
        let repeated = apply_mysql_migrations(&args, &global).await?;
        assert!(repeated.contains("applied_versions=0\npending_versions=0"), "{repeated}");
        args.apply = false; args.check_db = true;
        run_migrate(&args, &global).await?;
        // These are temporary test migration files, never repository history.
        fs::write(migrations.join("002_body.sql"), "ALTER TABLE coverage_admin_items ADD COLUMN changed TEXT;")?;
        let error = apply_mysql_migrations(&args, &global).await.unwrap_err();
        assert!(error.to_string().contains("checksum mismatch"), "{error}");
        let opened = open_configured_mysql_db(&global).await?;
        let columns = opened.plugin.query(DbStatement::new("SHOW COLUMNS FROM coverage_admin_items")).await?;
        assert_eq!(columns.len(), 2, "changed history must not execute new SQL");
        assert!(columns.iter().any(|row| row.get_string("Field").unwrap().as_deref() == Some("body")));
        let empty = Migration { number: 3, name: "empty".into(), path: migrations.join("003_empty.sql"), sql: "-- comment only".into() };
        assert!(apply_mysql_migration(&opened.plugin, &empty, &mysql_migration_plan(&empty)).await.unwrap_err().to_string().contains("no executable SQL"));
        opened.manager.close().await;
        Ok::<(), anyhow::Error>(())
    }.await;
    manager.execute("bcs", "DROP TABLE IF EXISTS coverage_admin_items, bcs_schema_migrations").await?;
    manager.close().await;
    result
}
