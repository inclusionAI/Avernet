#[path = "migrate_bot_authority_contract.rs"]
mod bot_authority_contract;

#[cfg(test)]
#[path = "migrate_task_handle_contract.rs"]
mod task_handle_contract;

#[cfg(test)]
#[path = "migrate_group_notify_contract.rs"]
mod group_notify_contract;

#[cfg(test)]
use bcs_db_migrations as migration_chain_migrations;

#[path = "../../../services/bcs-bot-store/tests/support/a2a_mysql_cases.rs"]
mod a2a_store_contract;

// The shared existing-only bot profile/avatar store contract
// (bot-avatar plan Task 3): the ignored full-chain MySQL gate and the
// bot-store SQLite conformance run the same verification body.
#[path = "../../../services/bcs-bot-store/tests/support/bot_profile_contract.rs"]
mod avatar_store_contract;

// The shared candidate viewer-visibility + friend-set store contract: the
// ignored full-chain MySQL gate and the bot-store Memory/SQLite conformance
// run the same verification body.
#[path = "../../../services/bcs-bot-store/tests/support/candidate_friend_set_contract.rs"]
mod candidate_friend_set_contract;

use super::*;
use std::sync::Arc;
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
fn mysql_v20_prefix_upgrade_reports_the_complete_pending_tail() -> Result<()> {
    // Exercise the real planner and apply-report formatter without a MySQL
    // server, so migration-tail additions cannot hide behind an ignored test.
    let plans = load_selected_migrations(&chain_args())?
        .iter().map(mysql_migration_plan).collect::<Vec<_>>();
    let records = plans.iter().map(|plan| AppliedMysqlMigration {
        version: i64::from(plan.version), name: plan.name.clone(),
        dialect: "mysql".into(), checksum: plan.checksum.clone(),
    }).collect::<Vec<_>>();
    let prefix = records.iter().filter(|record| record.version <= 20).cloned().collect();
    let before = build_mysql_migration_report("bcs".into(), plans.clone(), prefix, true)?;
    assert_eq!(before.current_version, Some(20));
    assert_eq!(before.target_version, Some(45));
    assert_eq!(before.pending_versions.iter().map(|plan| plan.version).collect::<Vec<_>>(),
        (21..=45).collect::<Vec<_>>());

    let after = build_mysql_migration_report("bcs".into(), plans, records, true)?;
    let report = format_mysql_apply_report(&after, &before.pending_versions);
    assert!(report.contains("applied_versions=25\npending_versions=0"), "{report}");
    assert!(report.contains("- 044 actor_identity_no_pad checksum="), "{report}");
    assert!(report.contains("- 045 delivery_session_lookup checksum="), "{report}");
    let repeated = format_mysql_apply_report(&after, &[]);
    assert!(repeated.contains("applied_versions=0\npending_versions=0"), "{repeated}");
    Ok(())
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
    let db: Arc<dyn DbPlugin> = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
    let result = check_full_mysql_chain(db, &global).await;
    manager.close().await;
    result
}

/// The group-notify contract verification is dialect-independent Store
/// behavior over the migrated `bcs_groups` chain. Running it non-ignored on a
/// SQLite-backed migrated chain keeps these lines exercised in every CI run;
/// the ignored MySQL chain test adds the MySQL-compatible evidence on top with
/// the exact same verification body.
#[tokio::test]
async fn sqlite_migration_chain_backs_the_group_notify_contract() -> Result<()> {
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_group_store::MySqlGroupStore as ChainMySqlGroupStore;

    let db: Arc<dyn DbPlugin> =
        Arc::new(LocalSqliteDbPlugin::new().context("open sqlite db")?);
    migration_chain_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .context("apply the sqlite migration chain")?;
    task_handle_contract::verify(db.clone(), true).await?;
    group_notify_contract::verify(Arc::clone(&db), |db, env| {
        ChainMySqlGroupStore::sqlite(Arc::clone(db), env.to_string())
    })
    .await?;
    bcs_test_support::contract::provider_slug::provider_repo_port_contract_tests(
        &bcs_bot_store::provider::DbProviderStore::sqlite(db),
    ).await;
    Ok(())
}

async fn check_full_mysql_chain(db: Arc<dyn DbPlugin>, global: &MigrateGlobalArgs) -> Result<()> {
    let migrated_db = Arc::clone(&db);
    let db: &dyn DbPlugin = db.as_ref();
    // The test owns an empty database exclusively. Refuse existing tables before
    // entering the cleanup scope; CI runs this before all other MySQL contracts.
    anyhow::ensure!(chain_table_names(db).await?.is_empty(), "full-chain test requires an empty disposable database");
    let args = chain_args();
    let result: Result<()> = async {
        println!("[phase 1/3] applying the fresh MySQL migration chain");
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains("applied_versions=45\npending_versions=0"), "{report}");
        let versions = load_applied_mysql_migrations(db).await?.into_iter().map(|record| record.version).collect::<Vec<_>>();
        assert_eq!(versions, (1..=45).collect::<Vec<_>>());
        assert_chain_columns(db).await?;
        assert_direct_queue_indexes(db).await?;
        assert_history_lookup_plans(db).await?;
        task_handle_contract::verify(migrated_db.clone(), false).await?;
        history_window_tests::verify_history_windows(global).await?;
        let records = chain_history(db).await?;
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains("applied_versions=0\npending_versions=0"), "{report}");
        assert_eq!(chain_history(db).await?, records);

        println!("[phase 2/3] Group, Provider and A2A repository contracts on the migrated chain");
        group_notify_contract::verify(migrated_db.clone(), |db, env| {
            bcs_group_store::MySqlGroupStore::new(Arc::clone(db), env.to_string())
        })
        .await?;
        bcs_test_support::contract::provider_slug::provider_repo_port_contract_tests(
            &bcs_bot_store::provider::DbProviderStore::mysql(migrated_db.clone()),
        ).await;

        a2a_store_contract::verify(migrated_db.clone()).await;

        // The SAME existing-only bot profile/avatar contract the Memory and
        // SQLite conformance runs use, on the fully migrated MySQL schema
        // (this exercises the real MySQL affected_rows=0 unchanged-write
        // classification, SQLite's BEGIN IMMEDIATE locking and the
        // `SELECT ... FOR UPDATE` confirm differ on this dialect).
        avatar_store_contract::avatar_contract(
            &bcs_bot_store::PersistentBotRepo::with_sql_flavor(
                migrated_db.clone(),
                bcs_db_api::DbSqlFlavor::Mysql,
            ),
        )
        .await;
        println!("Bot profile: existing-only avatar/profile MySQL contract passed (unchanged-value writes classify by the confirmed live row)");

        // The SAME candidate viewer-visibility and JSON friend-set contract
        // the Memory/SQLite conformance runs use, on the fully migrated MySQL
        // schema (real JSON_TABLE expansion and MySQL collation).
        let candidate_repo = bcs_bot_store::PersistentBotRepo::with_sql_flavor(
            migrated_db.clone(),
            bcs_db_api::DbSqlFlavor::Mysql,
        );
        candidate_friend_set_contract::verify(
            &candidate_repo,
            &candidate_repo,
            &bcs_config::resolve_env_str(),
            "mysql-candidate-set",
            // `bcs_bots.bot_uuid` is VARCHAR(256).
            256,
        )
        .await;
        println!("Bot candidates: viewer visibility and JSON friend-set MySQL contract passed");

        // A completed historical prefix upgrades normally without rewriting its
        // records. The old IF NOT EXISTS spelling has the same DDL result here;
        // old checksum/timestamp rows model a prior successful deployment.
        println!("[phase 3/3] v20 prefix upgrade with a pre-existing Group row");
        drop_chain_tables(db).await?;
        let mut prefix = chain_args();
        prefix.to = Some(20);
        apply_mysql_migrations(&prefix, global).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_group_participants (group_id, bot_uuid, role, env, tags_json) VALUES ('chain-group', 'chain-bot', 'member', 'test', '[\"keep\"]')")).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_groups (group_id, status, driver_bot, env) VALUES ('chain-group', 'active', 'chain-driver', 'test')")).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_state_machine_definition_snapshots (env, run_id, group_id, session_id, group_version, definition_id, definition_version, definition_content_hash, snapshot_json) VALUES ('test', 'legacy-run', 'chain-group', 'chain-session', 1, 'legacy', 1, REPEAT('a', 64), '{\"version\":1}')")).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, session_id, group_id, participants, current_msg_seq) VALUES ('test', 'chain-session', 'chain-group', '[]', 7)")).await?;
        db.execute(DbStatement::new("UPDATE bcs_groups SET service_spec = '{\"max_concurrency\":1}' WHERE group_id = 'chain-group' AND env = 'test'")).await?;
        db.execute(DbStatement::new("UPDATE bcs_group_sessions SET callback_status = 'pending', callback_lease_owner = 'old-worker', callback_lease_token = 42, callback_lease_until_ms = 1234 WHERE session_id = 'chain-session' AND env = 'test'")).await?;
        let retired_group_sql = "SELECT service_spec FROM bcs_groups WHERE group_id = 'chain-group' AND env = 'test'";
        let retired_session_sql = "SELECT callback_status, callback_lease_owner, callback_lease_token, callback_lease_until_ms FROM bcs_group_sessions WHERE session_id = 'chain-session' AND env = 'test'";
        let retired_group = db.query(DbStatement::new(retired_group_sql)).await?;
        let retired_session = db.query(DbStatement::new(retired_session_sql)).await?;
        for &(version, _, current, archived) in MYSQL_SYNTAX_REVISIONS {
            let updated = db.execute(DbStatement::with_params(
                "UPDATE bcs_schema_migrations SET checksum = ?, applied_at = '2026-01-01 00:00:00' WHERE version = ? AND checksum = ?",
                vec![DbValue::from(archived), DbValue::from(i64::from(version)), DbValue::from(current)],
            )).await?;
            assert_eq!(updated.affected_rows, 1);
        }
        let records = chain_history(db).await?;
        let expected_applied = load_selected_migrations(&args)?.len() - records.len();
        let report = apply_mysql_migrations(&args, global).await?;
        assert!(report.contains(&format!("applied_versions={expected_applied}\npending_versions=0")), "{report}");
        assert_eq!(chain_history(db).await?.into_iter().filter(|(version, _)| *version <= 20).collect::<Vec<_>>(), records);
        assert_eq!(db.query(DbStatement::new(retired_group_sql)).await?, retired_group);
        assert_eq!(db.query(DbStatement::new(retired_session_sql)).await?, retired_session);
        assert_chain_columns(db).await?;
        assert_direct_queue_indexes(db).await?;
        let row = db.query(DbStatement::new("SELECT session_type, current_msg_seq FROM bcs_session_registry WHERE env = 'test' AND session_id = 'chain-session'")).await?.remove(0);
        assert_eq!(db_get_column::<String>(&row, "session_type")?, "group");
        assert_eq!(bcs_db_api::db_get_column_opt::<i64>(&row, "current_msg_seq")?, None);
        let row = db.query(DbStatement::new("SELECT current_msg_seq FROM bcs_group_sessions WHERE env = 'test' AND session_id = 'chain-session'")).await?.remove(0);
        assert_eq!(db_get_column::<i64>(&row, "current_msg_seq")?, 7);
        let row = db.query(DbStatement::new("SELECT human_mention_notify_mode FROM bcs_groups WHERE group_id = 'chain-group' AND env = 'test'")).await?.remove(0);
        assert_eq!(db_get_column::<String>(&row, "human_mention_notify_mode")?, "all");
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
        verify_original_043_history_upgrade(db, global).await?;
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
        ("bcs_providers", vec!["env", "provider_id", "slug"]),
        ("bcs_group_agent_cards", vec!["id", "gmt_create", "gmt_modified", "env", "group_id", "card_template"]),
        ("bcs_collaboration_drafts", vec!["draft_id", "owner_bot_id", "head_revision", "created_at", "deleted"]),
        ("bcs_collaboration_draft_revisions", vec!["draft_id", "revision", "payload_json", "revoked"]),
        ("bcs_session_registry", vec!["env", "session_id", "session_type", "current_msg_seq"]),
        ("bcs_chat_runs", vec!["delivery_id", "source_message_id"]),
        ("bcs_messages", vec!["owner_bot_id", "visibility_domain", "audience_kind", "audience_actor_ids_json"]),
        ("bcs_state_machine_node_runs", vec!["outcome", "responded_by", "failure_action"]),
        ("bcs_group_participants", vec!["tags_json", "message_view_scope"]),
        ("bcs_groups", vec!["human_mention_notify_mode", "service_spec"]),
        ("bcs_bots", vec!["task_claim_mode", "task_dream_mode", "user_visibility", "friend_ext", "friend_check_in_strategy", "provider_id", "provider_bot_ref", "connection_mode", "webhook_url", "ownership_version", "avatar_url"]),
        ("bcs_state_machine_runs", vec!["root_run_id", "rerun_of", "session_activation_count", "opening_message_override_json"]),
        ("bcs_group_sessions", vec!["message_visibility_version", "activation_count", "completed_at", "callback_status", "callback_lease_owner", "callback_lease_token", "callback_lease_until_ms"]),
        ("bcs_state_machine_definition_snapshots", vec!["execution_plan_json", "execution_plan_content_hash", "execution_plan_compiler_version"]),
    ] {
        let rows = db.query(DbStatement::with_params("SELECT column_name AS column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ? ORDER BY ordinal_position", vec![DbValue::from(table)])).await?;
        let actual = rows.iter().map(|row| db_get_column::<String>(row, "column_name")).collect::<bcs_db_api::DbResult<Vec<_>>>()?;
        if table == "bcs_group_agent_cards" {
            assert_eq!(actual, columns, "{table} column order");
        }
        for column in columns { assert!(actual.iter().any(|actual| actual == column), "missing {table}.{column}"); }
        if table.starts_with("bcs_collaboration_draft") {
            assert!(!actual.iter().any(|column| column == "deployment_id"), "obsolete {table}.deployment_id");
        }
    }
    Ok(())
}

async fn assert_history_lookup_plans(db: &dyn DbPlugin) -> Result<()> {
    // Populate a growing mixed Session so EXPLAIN cannot succeed vacuously on
    // an empty table. The enclosing test owns and drops this isolated schema.
    for start in (0..5_000).step_by(200) {
        let values = (start..start + 200).map(|i| format!(
            "('history-plan-{i}', 'history-plan-group', 'history-plan-session', {i}, 'test', 'bot', 'bot', \
            '{}', '{{\"text\":\"output\"}}', 'key-{i}', {i}, 'workflow')",
            if i % 10 == 0 { "state_machine_output" } else { "chat" })).collect::<Vec<_>>().join(",");
        db.execute(DbStatement::new(format!("INSERT INTO bcs_messages \
            (message_id, group_id, session_id, session_seq, env, sender_id, sender_type, message_type, content, client_msg_id, created_at, run_id) \
            VALUES {values}"))).await?;
    }
    db.query(DbStatement::new("ANALYZE TABLE bcs_messages")).await?;
    for count in [1, 200] {
        for (column, prefix) in [("message_id", "history-plan-"), ("client_msg_id", "key-")] {
            let mut params = vec![DbValue::from("test"), DbValue::from("history-plan-session")];
            params.extend((0..count).map(|i| DbValue::from(format!("{prefix}{i}"))));
            let rows = db.query(DbStatement::with_params(format!("EXPLAIN SELECT * FROM bcs_messages \
                WHERE env = ? AND session_id = ? AND {column} IN ({}) \
                AND message_type IN ('state_machine_panel', 'state_machine_human_input_prompt', \
                'state_machine_human_input_response', 'state_machine_output') LIMIT 401", vec!["?"; count].join(",")), params)).await?;
            let index = db_get_column::<String>(&rows[0], "key")?;
            assert!(!index.is_empty());
            println!("history identity lookup: column={column} keys={count} index={index} rows={}", db_get_column::<i64>(&rows[0], "rows")?);
        }
    }
    Ok(())
}

async fn assert_direct_queue_indexes(db: &dyn DbPlugin) -> Result<()> {
    for (table, index, non_unique, columns) in [
        ("bcs_providers", "uk_bcs_providers_env_slug", 0, vec!["env", "slug"]),
        ("bcs_group_agent_cards", "uk_group_agent_card", 0, vec!["env", "group_id"]),
        ("bcs_session_registry", "uk_session_registry", 0, vec!["env", "session_id"]),
        ("bcs_messages", "uk_session_seq", 0, vec!["env", "session_id", "session_seq"]),
        ("bcs_chat_runs", "idx_env_delivery", 1, vec!["env", "delivery_id"]),
        ("bcs_group_sessions", "idx_callback_status", 1, vec!["env", "callback_status"]),
        ("bcs_group_sessions", "idx_session_callback_recovery", 1, vec!["env", "session_kind", "status", "callback_status", "callback_lease_token", "callback_lease_until_ms", "session_id"]),
        ("bcs_message_deliveries", "idx_delivery_session_message", 1, vec!["env", "session_id", "source_message_id", "kind", "status"]),
    ] {
        let rows = db.query(DbStatement::with_params(
            "SELECT COLUMN_NAME AS col, NON_UNIQUE AS non_unique FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ? ORDER BY seq_in_index",
            vec![DbValue::from(table), DbValue::from(index)],
        )).await?;
        let actual = rows.iter().map(|row| db_get_column::<String>(row, "col")).collect::<bcs_db_api::DbResult<Vec<_>>>()?;
        assert_eq!(actual, columns, "{table}.{index}");
        for row in rows { assert_eq!(db_get_column::<i64>(&row, "non_unique")?, non_unique, "{table}.{index}"); }
    }
    Ok(())
}

#[test]
fn actor_identity_correction_accepts_only_exact_archived_history() -> Result<()> {
    let plans = load_selected_migrations(&chain_args())?;
    let plan = mysql_migration_plan(plans.iter().find(|p| p.number == 43).unwrap());
    let old = include_str!("../../../../migrations/legacy/mysql/043_actor_identity_collation.sql");
    let record = AppliedMysqlMigration {version: 43, name: plan.name.clone(),
        dialect: "mysql".into(), checksum: sha256_hex(old.as_bytes())};
    validate_mysql_migration_record(&plan, &record)?;
    for field in ["name", "checksum", "dialect", "version"] {
        let mut wrong = record.clone();
        match field {
            "name" => wrong.name.push_str("-wrong"),
            "checksum" => wrong.checksum = "0".repeat(64),
            "dialect" => wrong.dialect = "sqlite".into(),
            _ => wrong.version = 42,
        }
        assert!(validate_mysql_migration_record(&plan, &wrong).is_err());
    }
    let mut changed = plan.clone(); changed.checksum = "f".repeat(64);
    assert!(validate_mysql_migration_record(&changed, &record).is_err());
    Ok(())
}

async fn verify_original_043_history_upgrade(db: &dyn DbPlugin, global: &MigrateGlobalArgs) -> Result<()> {
    drop_chain_tables(db).await?;
    let mut prefix = chain_args(); prefix.to = Some(42);
    apply_mysql_migrations(&prefix, global).await?;
    // Model an actually applied original 043, not an edited history record.
    let original = include_str!("../../../../migrations/legacy/mysql/043_actor_identity_collation.sql");
    for sql in split_sql_statements(original) { db.execute(DbStatement::new(sql)).await?; }
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version,name,dialect,checksum) VALUES (43,'actor_identity_collation','mysql',?)",
        vec![DbValue::from(sha256_hex(original.as_bytes()))])).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind) VALUES ('human_existing','Existing','dev','human')")).await?;
    let before = chain_history(db).await?;
    // Every migration after the original 043 applies on top of it.
    let expected = load_selected_migrations(&chain_args())?.len() - before.len();
    let report = apply_mysql_migrations(&chain_args(), global).await?;
    assert!(report.contains(&format!("applied_versions={expected}\npending_versions=0")), "{report}");
    assert_eq!(chain_history(db).await?.into_iter().filter(|(v,_)| *v <= 43).collect::<Vec<_>>(), before);
    // Existing exact ID remains; a trailing-space-different ID is now independent.
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind) VALUES ('human_existing ','Distinct','dev','human')")).await?;
    let rows = db.query(DbStatement::new("SELECT name FROM bcs_bots WHERE bot_uuid='human_existing' AND env='dev'")).await?;
    assert_eq!(rows.len(), 1);
    assert_eq!(db_get_column::<String>(&rows[0], "name")?, "Existing");
    Ok(())
}
