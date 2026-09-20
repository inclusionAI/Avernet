use super::*;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_config_api::mysql::MysqlConnectionConfig;
use mysql_async::Opts;

const LEGACY_001: &str = include_str!("../../../../migrations/legacy/mysql/001_init_schema.sql");
const LEGACY_008: &str = include_str!("../../../../migrations/legacy/mysql/008_human_input_im_requests.sql");
const OLD_BASELINE_CHECKSUM: &str = "b3de64c97b982a735230f6c55e966e4404eb509d70a0b7fd8f11dfa43e3452a7";

fn index_migration_args(only: Vec<u16>) -> MigrateArgs {
    MigrateArgs {
        dialect: Some(MigrationDialect::Mysql), migrations_dir: Some(bcs_root().join("migrations/mysql")),
        sqlite_path: None, emit_sql: false, check_files: false, check_db: false, apply: true,
        yes: true, only, from: None, to: None,
    }
}

#[test]
fn human_input_index_revisions_preserve_archives_and_pass_static_gate() -> Result<()> {
    assert_eq!(sha256_hex(LEGACY_001.as_bytes()), "f2248c186b6d4260978d817a9654adfbee2f08dd12fa8cc3c1aa5a70d1bb9d3d");
    assert_eq!(sha256_hex(LEGACY_008.as_bytes()), "0e10c711afc436cf59d2393e3d5c88b9c24e73be72f4e984044840e6f798ebd5");
    let migrations = load_selected_migrations(&index_migration_args(vec![1, 8]))?;
    let baseline = &migrations[0];
    let plan = mysql_migration_plan(baseline);
    let body = baseline.sql.split_once("-- Record the open-source v1 baseline").context("baseline checksum boundary")?.0;
    assert_eq!(plan.checksum, sha256_hex(body.as_bytes()));
    let old_key = "KEY `idx_human_input_scope_status` (`reply_scope_key`, `status`, `deadline_ms`, `created_at`)";
    let new_key = "KEY `idx_human_input_scope_status` (`reply_scope_key`(700), `status`, `deadline_ms`, `created_at`)";
    let incremental_columns = [
        "tags_json", "message_view_scope", "message_visibility_version",
        "visibility_domain", "audience_kind", "audience_actor_ids_json",
    ];
    let starting_schema = LEGACY_001.split_inclusive('\n').filter(|line| {
        !incremental_columns.iter().any(|column| line.trim_start().starts_with(&format!("`{column}` ")))
    }).collect::<String>();
    assert_eq!(baseline.sql, starting_schema.replace(old_key, new_key).replace(OLD_BASELINE_CHECKSUM, &plan.checksum));
    assert_eq!(migrations[1].sql, LEGACY_008.replace(old_key, new_key));
    assert!(check_mysql_migration_files(&index_migration_args(vec![]))?.contains("migration files check ok"));
    let active = load_selected_migrations(&index_migration_args(vec![]))?;
    assert_eq!(active.iter().map(|migration| migration.number).collect::<Vec<_>>(), (1..=28).collect::<Vec<_>>());
    assert!(!active.iter().any(|migration| migration.sql.contains("DROP INDEX `idx_human_input_scope_status`")));

    Ok(())
}

#[test]
fn human_input_index_compatibility_keeps_old_records_without_index_rebuild() -> Result<()> {
    let migrations = load_selected_migrations(&index_migration_args(vec![1, 8]))?;
    let plans = migrations.iter().map(mysql_migration_plan).collect::<Vec<_>>();
    let old_checksums = [OLD_BASELINE_CHECKSUM.to_string(), sha256_hex(LEGACY_008.as_bytes())];
    let records = plans[..2].iter().zip(old_checksums).map(|(plan, checksum)| AppliedMysqlMigration {
        version: i64::from(plan.version), name: plan.name.clone(), dialect: "mysql".into(), checksum,
    }).collect::<Vec<_>>();
    let report = build_mysql_migration_report("bcs".into(), plans.clone(), records.clone(), true)?;
    assert_eq!(report.applied_versions, records);
    assert!(report.pending_versions.is_empty());
    for (plan, record) in plans.iter().zip(&records) {
        let mut bad_record = record.clone();
        bad_record.checksum = "0".repeat(64);
        assert!(validate_mysql_migration_record(plan, &bad_record).is_err());
        bad_record = record.clone();
        bad_record.name = "unrelated".into();
        assert!(validate_mysql_migration_record(plan, &bad_record).is_err());
        bad_record = record.clone();
        bad_record.dialect = "sqlite".into();
        assert!(validate_mysql_migration_record(plan, &bad_record).is_err());
        let mut changed_plan = plan.clone();
        changed_plan.checksum = "f".repeat(64);
        assert!(validate_mysql_migration_record(&changed_plan, record).is_err());
    }
    Ok(())
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable MySQL database"]
async fn human_input_index_migrations_apply_to_real_mysql() -> Result<()> {
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
    let result = check_human_input_index_migrations(&db).await;
    manager.close().await;
    result
}

async fn check_human_input_index_migrations(db: &dyn DbPlugin) -> Result<()> {
    let migrations = load_selected_migrations(&index_migration_args(vec![1, 8]))?;
    let plans = migrations.iter().map(mysql_migration_plan).collect::<Vec<_>>();
    let tables = migrations[0].sql.lines().filter_map(parse_mysql_create_table_name).collect::<Vec<_>>();
    let schema_existed = mysql_schema_migrations_exists(db).await?;
    for table in &tables {
        if table == "bcs_schema_migrations" { continue; }
        let rows = db.query(DbStatement::with_params("SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?", vec![DbValue::from(table.clone())])).await?;
        anyhow::ensure!(db_get_column::<i64>(&rows[0], "count")? == 0, "test requires absent baseline table {table}; use a disposable database");
    }
    let original_records = load_applied_mysql_migrations(db).await?;
    anyhow::ensure!(!original_records.iter().any(|row| [1, 8].contains(&row.version)), "test migration versions already present");
    let result = async {
        // Execute the complete corrected 001, not just its HumanInput CREATE.
        apply_mysql_migration(db, &migrations[0], &plans[0]).await?;
        assert_human_input_scope_index(db, 700).await?;
        // Exercise 008 on a missing table too; CREATE IF NOT EXISTS after 001
        // alone would hide a broken standalone 008 definition.
        db.execute(DbStatement::new("DROP TABLE bcs_human_input_requests")).await?;
        apply_mysql_migration(db, &migrations[1], &plans[1]).await?;
        assert_human_input_scope_index(db, 700).await?;
        for suffix in ["a", "b"] {
            insert_scope_request(db, suffix, suffix).await?;
        }
        assert_scope_rows_and_uniqueness(db).await?;
        // Historical records are accepted without scheduling an index rebuild.
        let legacy_checksums = [OLD_BASELINE_CHECKSUM.to_string(), sha256_hex(LEGACY_008.as_bytes())];
        for (plan, checksum) in plans[..2].iter().zip(legacy_checksums) {
            db.execute(DbStatement::with_params("UPDATE bcs_schema_migrations SET checksum = ?, applied_at = '2026-01-01 00:00:00' WHERE version = ? AND checksum = ?", vec![DbValue::from(checksum), DbValue::from(i64::from(plan.version)), DbValue::from(plan.checksum.clone())])).await?;
        }
        let before = load_applied_mysql_migrations(db).await?;
        let column_before = human_input_column_metadata(db).await?;
        let report = build_mysql_migration_report("bcs".into(), plans.clone(), before.clone(), false)?;
        assert!(report.pending_versions.is_empty());
        let report = build_mysql_migration_report("bcs".into(), plans.clone(), before.clone(), true)?;
        assert!(report.pending_versions.is_empty());
        assert_human_input_scope_index(db, 700).await?;
        assert_eq!(human_input_column_metadata(db).await?, column_before);
        assert_scope_rows_and_uniqueness(db).await?;
        let after = load_applied_mysql_migrations(db).await?;
        assert_eq!(after, before);
        let timestamps = db.query(DbStatement::new("SELECT DATE_FORMAT(applied_at, '%Y-%m-%d %H:%i:%s') AS applied_at FROM bcs_schema_migrations WHERE version IN (1, 8)")).await?;
        for row in timestamps { assert_eq!(db_get_column::<String>(&row, "applied_at")?, "2026-01-01 00:00:00"); }
        assert!(build_mysql_migration_report("bcs".into(), plans.clone(), after, false)?.pending_versions.is_empty());
        Ok(())
    }.await;
    // Every table below was checked absent before any DDL. Keep shared CI
    // schema history and records belonging to other migration tests intact.
    for table in tables.iter().rev() {
        if table == "bcs_schema_migrations" { continue; }
        db.execute(DbStatement::new(format!("DROP TABLE IF EXISTS `{table}`"))).await?;
    }
    if mysql_schema_migrations_exists(db).await? {
        db.execute(DbStatement::new("DELETE FROM bcs_schema_migrations WHERE version IN (1, 8)")).await?;
        assert_eq!(load_applied_mysql_migrations(db).await?, original_records);
        if !schema_existed { db.execute(DbStatement::new("DROP TABLE bcs_schema_migrations")).await?; }
    }
    result
}

async fn assert_human_input_scope_index(db: &dyn DbPlugin, prefix: i64) -> Result<()> {
    let rows = db.query(DbStatement::new("SELECT column_name AS column_name, sub_part AS sub_part, non_unique AS non_unique FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'bcs_human_input_requests' AND index_name = 'idx_human_input_scope_status' ORDER BY seq_in_index")).await?;
    assert_eq!(rows.len(), 4);
    for (index, column) in ["reply_scope_key", "status", "deadline_ms", "created_at"].iter().enumerate() {
        assert_eq!(db_get_column::<String>(&rows[index], "column_name")?, *column);
        assert_eq!(bcs_db_api::db_get_column_opt::<i64>(&rows[index], "sub_part")?, if index == 0 { Some(prefix) } else { None });
        assert_eq!(db_get_column::<i64>(&rows[index], "non_unique")?, 1);
    }
    let unique = db.query(DbStatement::new("SELECT sub_part AS sub_part, non_unique AS non_unique FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'bcs_human_input_requests' AND index_name = 'uk_human_input_active_slot'")).await?;
    assert_eq!(unique.len(), 1);
    assert_eq!(bcs_db_api::db_get_column_opt::<i64>(&unique[0], "sub_part")?, None);
    assert_eq!(db_get_column::<i64>(&unique[0], "non_unique")?, 0);
    Ok(())
}

async fn human_input_column_metadata(db: &dyn DbPlugin) -> Result<Vec<String>> {
    let rows = db.query(DbStatement::new("SELECT CONCAT(column_name, ':', column_type, ':', is_nullable, ':', IFNULL(collation_name, '')) AS shape FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'bcs_human_input_requests' ORDER BY ordinal_position")).await?;
    rows.iter().map(|row| db_get_column::<String>(row, "shape").map_err(Into::into)).collect()
}

async fn insert_scope_request(db: &dyn DbPlugin, id: &str, suffix: &str) -> Result<()> {
    let scope = format!("{}{suffix}", "界".repeat(700));
    db.execute(DbStatement::with_params("INSERT INTO bcs_human_input_requests (request_id, session_id, run_id, node_id, binding_id, channel_type, account_ref, notification_mode, reply_scope_key, active_slot_key, assignee_actor_id, im_conversation_id, im_conversation_type, node_display_name, notification_text, deadline_ms, status) VALUES (?, 'session', 'run', 'node', 'binding', 'im', 'account', 'direct', ?, ?, 'actor', 'conversation', 'direct', 'human', 'prompt', 999, 'active')", vec![DbValue::from(id), DbValue::from(scope.clone()), DbValue::from(scope)])).await?;
    Ok(())
}

async fn assert_scope_rows_and_uniqueness(db: &dyn DbPlugin) -> Result<()> {
    for suffix in ["a", "b"] {
        let scope = format!("{}{suffix}", "界".repeat(700));
        let rows = db.query(DbStatement::with_params("SELECT request_id FROM bcs_human_input_requests FORCE INDEX (idx_human_input_scope_status) WHERE reply_scope_key = ? AND status = 'active'", vec![DbValue::from(scope)])).await?;
        assert_eq!(rows.len(), 1);
        assert_eq!(db_get_column::<String>(&rows[0], "request_id")?, suffix);
    }
    let error = insert_scope_request(db, "duplicate", "a").await.unwrap_err().to_string();
    assert!(error.contains("Duplicate entry"), "{error}");
    Ok(())
}
