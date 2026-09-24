use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[path = "tests/session_registry.rs"]
mod session_registry;

#[path = "tests/fixed_loop.rs"]
mod fixed_loop;

#[path = "tests/bot_provider_storage.rs"]
mod bot_provider_storage;

#[path = "tests/group_human_mention_notify_mode.rs"]
mod group_human_mention_notify_mode;

async fn column_names(db: &dyn DbPlugin, table: &str) -> DbResult<Vec<String>> {
    let rows = db
        .query(DbStatement::new(format!("PRAGMA table_info({table})")))
        .await?;
    rows.into_iter()
        .map(|row| db_get_column(&row, "name"))
        .collect()
}

async fn index_exists(db: &dyn DbPlugin, index: &str) -> DbResult<bool> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            vec![DbValue::from(index)],
        ))
        .await?;
    Ok(!rows.is_empty())
}

#[test]
fn mysql_queue_tables_preserve_business_keys_with_audit_columns() {
    let deliveries = include_str!("../../../../../migrations/mysql/021_message_deliveries.sql");
    let policy = include_str!("../../../../../migrations/mysql/022_message_delivery_policy.sql");
    for sql in [deliveries, policy] {
        assert!(sql.contains("id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT"));
        assert!(sql.contains("gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"));
        assert!(sql.contains("gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"));
        assert!(sql.contains("PRIMARY KEY (id)"));
        assert!(sql.contains("updated_at_ms BIGINT NOT NULL"));
    }
    assert!(deliveries.contains("UNIQUE KEY uk_delivery_env_id (env, delivery_id)"));
    assert!(deliveries.contains("created_at_ms BIGINT NOT NULL"));
    for key in ["env, source_message_id, target_bot_id", "env, run_id", "env, idempotency_key", "env, request_id"] {
        assert!(deliveries.contains(&format!("UNIQUE ({key})")));
    }
    assert!(policy.contains("UNIQUE KEY uk_delivery_policy_env (env)"));
}

async fn migration_rows(db: &dyn DbPlugin) -> DbResult<Vec<(i64, String, String)>> {
    let rows = db
        .query(DbStatement::new(
            "SELECT version, name, dialect FROM bcs_schema_migrations ORDER BY version",
        ))
        .await?;
    rows.into_iter()
        .map(|row| {
            Ok((
                db_get_column(&row, "version")?,
                db_get_column(&row, "name")?,
                db_get_column(&row, "dialect")?,
            ))
        })
        .collect()
}

#[tokio::test]
async fn fresh_sqlite_migrations_create_human_output_metadata() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;

    run_sqlite_migrations(&db).await?;

    let columns = column_names(&db, "bcs_bots").await?;
    assert!(columns.iter().any(|column| column == "agent_code"));
    assert!(columns.iter().any(|column| column == "task_claim_mode"));
    assert!(columns.iter().any(|column| column == "task_dream_mode"));
    assert!(columns.iter().any(|column| column == "user_visibility"));
    assert!(columns.iter().any(|column| column == "friend_ext"));
    assert!(
        columns
            .iter()
            .any(|column| column == "friend_check_in_strategy")
    );
    let node_columns = column_names(&db, "bcs_state_machine_node_runs").await?;
    assert!(node_columns.iter().any(|column| column == "outcome"));
    assert!(node_columns.iter().any(|column| column == "responded_by"));
    let request_columns = column_names(&db, "bcs_human_input_requests").await?;
    assert!(
        request_columns
            .iter()
            .any(|column| column == "active_slot_key")
    );
    assert!(
        request_columns
            .iter()
            .any(|column| column == "provider_message_ref")
    );
    assert!(request_columns.iter().any(|column| column == "created_at"));
    let session_columns = column_names(&db, "bcs_group_sessions").await?;
    assert!(
        session_columns
            .iter()
            .any(|column| column == "callback_lease_owner")
    );
    assert!(
        session_columns
            .iter()
            .any(|column| column == "callback_lease_token")
    );
    assert!(
        session_columns
            .iter()
            .any(|column| column == "callback_lease_until_ms")
    );
    assert!(index_exists(&db, "idx_session_callback_recovery").await?);
    assert_eq!(
        migration_rows(&db).await?,
        vec![
            (1, "init_schema".to_string(), "sqlite".to_string()),
            (
                2,
                "channel_binding_audit_timestamps".to_string(),
                "sqlite".to_string()
            ),
            (3, "add_organizations".to_string(), "sqlite".to_string()),
            (
                4,
                "add_session_collection".to_string(),
                "sqlite".to_string()
            ),
            (
                5,
                "add_session_collection_timestamp".to_string(),
                "sqlite".to_string()
            ),
            (6, "session_files".to_string(), "sqlite".to_string()),
            (
                7,
                "human_input_output_metadata".to_string(),
                "sqlite".to_string()
            ),
            (
                8,
                "human_input_im_requests".to_string(),
                "sqlite".to_string()
            ),
            (9, "eventing".to_string(), "sqlite".to_string()),
            (
                10,
                "eventing_plaintext_endpoint".to_string(),
                "sqlite".to_string()
            ),
            (
                11,
                "group_opening_message".to_string(),
                "sqlite".to_string()
            ),
            (12, "add_bot_task_modes".to_string(), "sqlite".to_string()),
            (13, "edge_permission".to_string(), "sqlite".to_string()),
            (
                14,
                "add_bot_internal_attributes".to_string(),
                "sqlite".to_string()
            ),
            (
                15,
                "group_participant_tags".to_string(),
                "sqlite".to_string()
            ),
            (16, "expand_session_ids".to_string(), "sqlite".to_string()),
            (
                17,
                "session_callback_lease".to_string(),
                "sqlite".to_string()
            ),
            (
                18,
                "state_machine_rerun_lineage".to_string(),
                "sqlite".to_string()
            ),
            (
                19,
                "one_shot_opening_message_override".to_string(),
                "sqlite".to_string()
            ),
            (20, "invite_code_id".to_string(), "sqlite".to_string()),
            (
                21,
                "human_participant_message_visibility".to_string(),
                "sqlite".to_string()
            ),
            (22, "message_deliveries".to_string(), "sqlite".to_string()),
            (23, "message_delivery_policy".to_string(), "sqlite".to_string()),
            (24, "delivery_worker_queries".to_string(), "sqlite".to_string()),
            (25, "delivery_context_selection".to_string(), "sqlite".to_string()),
            (26, "delivery_pending_abort".to_string(), "sqlite".to_string()),
            (27, "run_reply_segments".to_string(), "sqlite".to_string()),
            (28, "provider_bot_webhook".to_string(), "sqlite".to_string()),
            (29, "fixed_loop_runtime".to_string(), "sqlite".to_string()),
            (30, "bot_provider_storage".to_string(), "sqlite".to_string()),
            (
                31,
                "group_human_mention_notify_mode".to_string(),
                "sqlite".to_string()
            ),
            (32, "session_registry".to_string(), "sqlite".to_string())
        ]
    );
    Ok(())
}

#[tokio::test]
async fn sqlite_migration_plan_reports_all_versions() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;

    let report = check_sqlite_migrations(&db).await?;

    assert_eq!(report.pending_versions.len(), 32);
    assert_eq!(report.pending_versions[0].version, 1);
    assert_eq!(report.pending_versions[0].name, "init_schema");
    assert!(report.pending_versions[0].statements.is_empty());
    assert!(report.pending_versions[0].repairs.is_empty());
    assert_eq!(report.pending_versions[1].version, 2);
    assert_eq!(
        report.pending_versions[1].name,
        "channel_binding_audit_timestamps"
    );
    assert_eq!(report.pending_versions[2].version, 3);
    assert_eq!(report.pending_versions[2].name, "add_organizations");
    assert_eq!(report.pending_versions[3].version, 4);
    assert_eq!(report.pending_versions[3].name, "add_session_collection");
    assert_eq!(report.pending_versions[4].version, 5);
    assert_eq!(
        report.pending_versions[4].name,
        "add_session_collection_timestamp"
    );
    assert_eq!(report.pending_versions[5].version, 6);
    assert_eq!(report.pending_versions[5].name, "session_files");
    assert_eq!(report.pending_versions[6].version, 7);
    assert_eq!(
        report.pending_versions[6].name,
        "human_input_output_metadata"
    );
    assert_eq!(report.pending_versions[7].version, 8);
    assert_eq!(report.pending_versions[7].name, "human_input_im_requests");
    assert_eq!(report.pending_versions[8].version, 9);
    assert_eq!(report.pending_versions[8].name, "eventing");
    assert_eq!(report.pending_versions[9].version, 10);
    assert_eq!(
        report.pending_versions[9].name,
        "eventing_plaintext_endpoint"
    );
    assert_eq!(report.pending_versions[10].version, 11);
    assert_eq!(report.pending_versions[10].name, "group_opening_message");
    assert_eq!(report.pending_versions[11].version, 12);
    assert_eq!(report.pending_versions[11].name, "add_bot_task_modes");
    assert_eq!(report.pending_versions[12].version, 13);
    assert_eq!(report.pending_versions[12].name, "edge_permission");
    assert_eq!(report.pending_versions[13].version, 14);
    assert_eq!(
        report.pending_versions[13].name,
        "add_bot_internal_attributes"
    );
    assert_eq!(report.pending_versions[14].version, 15);
    assert_eq!(report.pending_versions[14].name, "group_participant_tags");
    assert_eq!(report.pending_versions[15].version, 16);
    assert_eq!(report.pending_versions[15].name, "expand_session_ids");
    assert_eq!(report.pending_versions[16].version, 17);
    assert_eq!(report.pending_versions[16].name, "session_callback_lease");
    assert_eq!(report.pending_versions[17].version, 18);
    assert_eq!(
        report.pending_versions[17].name,
        "state_machine_rerun_lineage"
    );
    assert_eq!(report.pending_versions[18].version, 19);
    assert_eq!(
        report.pending_versions[18].name,
        "one_shot_opening_message_override"
    );
    assert_eq!(report.pending_versions[19].version, 20);
    assert_eq!(report.pending_versions[19].name, "invite_code_id");
    assert_eq!(report.pending_versions[20].version, 21);
    assert_eq!(
        report.pending_versions[20].name,
        "human_participant_message_visibility"
    );
    Ok(())
}

#[tokio::test]
async fn sqlite_callback_lease_migration_repairs_legacy_session_table() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_group_sessions (
            session_id TEXT NOT NULL,
            env TEXT NOT NULL DEFAULT 'prod',
            session_kind TEXT NOT NULL DEFAULT 'chat',
            status TEXT NOT NULL DEFAULT 'running',
            callback_status TEXT DEFAULT NULL
        )",
    ))
    .await?;

    add_sqlite_session_callback_lease_schema(&db).await?;
    add_sqlite_session_callback_lease_schema(&db).await?;

    let columns = column_names(&db, "bcs_group_sessions").await?;
    for expected in [
        "callback_lease_owner",
        "callback_lease_token",
        "callback_lease_until_ms",
    ] {
        assert!(columns.iter().any(|column| column == expected));
    }
    assert!(index_exists(&db, "idx_session_callback_recovery").await?);
    Ok(())
}

#[test]
fn mysql_callback_lease_migration_adds_recovery_index() {
    let migration = include_str!("../../../../../migrations/mysql/016_session_callback_lease_and_chat_runs.sql");
    assert!(migration.contains("ADD INDEX `idx_session_callback_recovery`"));
    for column in [
        "`env`",
        "`session_kind`",
        "`status`",
        "`callback_status`",
        "`callback_lease_token`",
        "`callback_lease_until_ms`",
        "`session_id`",
    ] {
        assert!(migration.contains(column), "missing index column {column}");
    }
}

#[tokio::test]
async fn sqlite_rerun_lineage_migration_preserves_legacy_null_root() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_state_machine_runs (
            env TEXT NOT NULL,
            run_id TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL
        )",
    ))
    .await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_state_machine_runs (env, run_id, created_at_ms) \
         VALUES ('test', 'legacy-run', 1)",
    ))
    .await?;

    add_sqlite_state_machine_rerun_lineage_schema(&db).await?;
    add_sqlite_state_machine_rerun_lineage_schema(&db).await?;

    let columns = column_names(&db, "bcs_state_machine_runs").await?;
    for expected in ["root_run_id", "rerun_of", "session_activation_count"] {
        assert!(columns.iter().any(|column| column == expected));
    }
    assert!(index_exists(&db, "uk_sm_run_rerun_of").await?);
    assert!(index_exists(&db, "idx_sm_runs_root").await?);
    let rows = db
        .query(DbStatement::new(
            "SELECT root_run_id FROM bcs_state_machine_runs WHERE run_id = 'legacy-run'",
        ))
        .await?;
    let legacy_root: Option<String> = bcs_db_api::db_get_column_opt(&rows[0], "root_run_id")?;
    assert_eq!(legacy_root, None);
    Ok(())
}

#[test]
fn mysql_rerun_lineage_migration_adds_unique_direct_child_constraint() {
    let migration =
        include_str!("../../../../../migrations/mysql/017_state_machine_rerun_lineage.sql");
    for column in ["`root_run_id`", "`rerun_of`", "`session_activation_count`"] {
        assert!(migration.contains(column), "missing rerun column {column}");
    }
    assert!(migration.contains("ADD UNIQUE INDEX `uk_sm_run_rerun_of` (`env`, `rerun_of`)"));
    assert!(
        migration
            .contains("ADD INDEX `idx_sm_runs_root` (`env`, `root_run_id`, `created_at_ms`)")
    );
    assert!(!migration.contains("SET `root_run_id` = `run_id`"));
}

#[tokio::test]
async fn sqlite_one_shot_opening_message_override_migration_repairs_legacy_run_table()
-> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_state_machine_runs (
            env TEXT NOT NULL,
            run_id TEXT NOT NULL
        )",
    ))
    .await?;

    add_sqlite_one_shot_opening_message_override_schema(&db).await?;
    add_sqlite_one_shot_opening_message_override_schema(&db).await?;

    let columns = column_names(&db, "bcs_state_machine_runs").await?;
    assert!(
        columns
            .iter()
            .any(|column| column == "opening_message_override_json")
    );
    Ok(())
}

#[test]
fn mysql_one_shot_opening_message_override_migration_adds_nullable_column() {
    let migration =
        include_str!("../../../../../migrations/mysql/018_one_shot_opening_message_override.sql");
    assert!(migration.contains(
        "ADD COLUMN `opening_message_override_json` text DEFAULT NULL"
    ));
}

#[test]
fn mysql_human_participant_message_visibility_migration_is_additive() {
    let baseline = include_str!("../../../../../migrations/mysql/001_init_schema.sql");
    let migration = include_str!(
        "../../../../../migrations/mysql/020_human_participant_message_visibility.sql"
    );
    for column in [
        "message_view_scope", "message_visibility_version", "visibility_domain",
        "audience_kind", "audience_actor_ids_json",
    ] {
        assert!(!baseline.contains(&format!("`{column}`")), "{column} belongs to migration 020");
    }
    assert!(!baseline.contains("idx_messages_session_audience_created"));
    assert!(migration.contains("ADD COLUMN `message_view_scope`"));
    assert!(migration.contains("ADD COLUMN `message_visibility_version`"));
    assert!(migration.contains("ADD COLUMN `visibility_domain`"));
    assert!(migration.contains("ADD COLUMN `audience_kind`"));
    assert!(migration.contains("ADD COLUMN `audience_actor_ids_json`"));
    assert!(migration.contains("idx_messages_session_audience_created"));
}

#[test]
fn mysql_participant_tags_are_added_only_by_migration_011() {
    let baseline = include_str!("../../../../../migrations/mysql/001_init_schema.sql");
    let migration = include_str!("../../../../../migrations/mysql/011_group_participant_tags.sql");
    assert!(!baseline.contains("`tags_json`"));
    assert!(migration.contains("ADD COLUMN `tags_json` text DEFAULT NULL"));
}

#[tokio::test]
async fn sqlite_human_participant_visibility_migration_repairs_legacy_tables()
-> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_group_participants (
            id INTEGER PRIMARY KEY,
            bot_uuid TEXT NOT NULL
        )",
    ))
    .await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_group_participants (id, bot_uuid) VALUES (1, 'human_staff-1')",
    ))
    .await?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_group_sessions (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL
        )",
    ))
    .await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_group_sessions (id, session_id) VALUES (1, 'session-1')",
    ))
    .await?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_messages (
            message_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            session_seq INTEGER NOT NULL
        )",
    ))
    .await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_messages (message_id, session_id, created_at, session_seq) \
         VALUES ('message-1', 'session-1', 1, 1)",
    ))
    .await?;

    add_sqlite_human_participant_message_visibility_schema(&db).await?;
    add_sqlite_human_participant_message_visibility_schema(&db).await?;

    assert!(
        column_names(&db, "bcs_group_participants")
            .await?
            .iter()
            .any(|column| column == "message_view_scope")
    );
    assert!(
        column_names(&db, "bcs_group_sessions")
            .await?
            .iter()
            .any(|column| column == "message_visibility_version")
    );
    let message_columns = column_names(&db, "bcs_messages").await?;
    for expected in [
        "visibility_domain",
        "audience_kind",
        "audience_actor_ids_json",
    ] {
        assert!(message_columns.iter().any(|column| column == expected));
    }
    assert!(index_exists(&db, "idx_messages_session_audience_created").await?);

    let participant = db
        .query(DbStatement::new(
            "SELECT message_view_scope FROM bcs_group_participants WHERE id = 1",
        ))
        .await?;
    assert_eq!(
        db_get_column::<String>(&participant[0], "message_view_scope")?,
        "full"
    );
    let session = db
        .query(DbStatement::new(
            "SELECT message_visibility_version FROM bcs_group_sessions WHERE id = 1",
        ))
        .await?;
    assert_eq!(
        db_get_column::<i64>(&session[0], "message_visibility_version")?,
        0
    );
    let messages = db
        .query(DbStatement::new(
            "SELECT visibility_domain, audience_kind, audience_actor_ids_json \
             FROM bcs_messages WHERE message_id = 'message-1'",
        ))
        .await?;
    assert_eq!(
        bcs_db_api::db_get_column_opt::<String>(&messages[0], "visibility_domain")?,
        None
    );
    assert_eq!(
        bcs_db_api::db_get_column_opt::<String>(&messages[0], "audience_kind")?,
        None
    );
    assert_eq!(
        bcs_db_api::db_get_column_opt::<String>(
            &messages[0],
            "audience_actor_ids_json"
        )?,
        None
    );
    Ok(())
}

#[tokio::test]
async fn sqlite_migrations_are_idempotent() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;

    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;

    assert_eq!(
        migration_rows(&db).await?,
        vec![
            (1, "init_schema".to_string(), "sqlite".to_string()),
            (
                2,
                "channel_binding_audit_timestamps".to_string(),
                "sqlite".to_string()
            ),
            (3, "add_organizations".to_string(), "sqlite".to_string()),
            (
                4,
                "add_session_collection".to_string(),
                "sqlite".to_string()
            ),
            (
                5,
                "add_session_collection_timestamp".to_string(),
                "sqlite".to_string()
            ),
            (6, "session_files".to_string(), "sqlite".to_string()),
            (
                7,
                "human_input_output_metadata".to_string(),
                "sqlite".to_string()
            ),
            (
                8,
                "human_input_im_requests".to_string(),
                "sqlite".to_string()
            ),
            (9, "eventing".to_string(), "sqlite".to_string()),
            (
                10,
                "eventing_plaintext_endpoint".to_string(),
                "sqlite".to_string()
            ),
            (
                11,
                "group_opening_message".to_string(),
                "sqlite".to_string()
            ),
            (12, "add_bot_task_modes".to_string(), "sqlite".to_string()),
            (13, "edge_permission".to_string(), "sqlite".to_string()),
            (
                14,
                "add_bot_internal_attributes".to_string(),
                "sqlite".to_string()
            ),
            (
                15,
                "group_participant_tags".to_string(),
                "sqlite".to_string()
            ),
            (16, "expand_session_ids".to_string(), "sqlite".to_string()),
            (
                17,
                "session_callback_lease".to_string(),
                "sqlite".to_string()
            ),
            (
                18,
                "state_machine_rerun_lineage".to_string(),
                "sqlite".to_string()
            ),
            (
                19,
                "one_shot_opening_message_override".to_string(),
                "sqlite".to_string()
            ),
            (20, "invite_code_id".to_string(), "sqlite".to_string()),
            (
                21,
                "human_participant_message_visibility".to_string(),
                "sqlite".to_string()
            ),
            (22, "message_deliveries".to_string(), "sqlite".to_string()),
            (23, "message_delivery_policy".to_string(), "sqlite".to_string()),
            (24, "delivery_worker_queries".to_string(), "sqlite".to_string()),
            (25, "delivery_context_selection".to_string(), "sqlite".to_string()),
            (26, "delivery_pending_abort".to_string(), "sqlite".to_string()),
            (27, "run_reply_segments".to_string(), "sqlite".to_string()),
            (28, "provider_bot_webhook".to_string(), "sqlite".to_string()),
            (29, "fixed_loop_runtime".to_string(), "sqlite".to_string()),
            (30, "bot_provider_storage".to_string(), "sqlite".to_string()),
            (
                31,
                "group_human_mention_notify_mode".to_string(),
                "sqlite".to_string()
            ),
            (32, "session_registry".to_string(), "sqlite".to_string())
        ]
    );
    Ok(())
}

#[tokio::test]
async fn sqlite_eventing_database_upgrades_to_group_opening_message() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    // Simulate a DB that lost its v11 migration row (e.g. restored from a
    // partial backup). SQLite < 3.35 has no DROP COLUMN, so drop only the
    // schema row; the column add itself is repaired idempotently by
    // ensure_sqlite_group_opening_message_column (covered by
    // sqlite_group_opening_message_column_repair_adds_missing_column).
    db.execute(DbStatement::new(
        "DELETE FROM bcs_schema_migrations WHERE version = 11",
    ))
    .await?;

    let before = check_sqlite_migrations(&db).await?;
    // Deleting only the v11 (group_opening_message) record leaves later
    // migrations applied, so the max applied version stays at the latest
    // schema version even though v11 is the sole pending re-apply.
    assert_eq!(before.current_version, Some(sqlite_target_version()));
    assert_eq!(
        before
            .pending_versions
            .iter()
            .map(|migration| (migration.version, migration.name.as_str()))
            .collect::<Vec<_>>(),
        vec![(11, "group_opening_message")]
    );

    run_sqlite_migrations(&db).await?;

    assert!(
        column_names(&db, "bcs_groups")
            .await?
            .iter()
            .any(|column| column == "opening_message_json")
    );
    // group_opening_message is no longer the tail migration (task_modes at v12
    // follows it), so assert it was re-applied as the version-11 row rather than
    // as the last row; re-applying while the column already exists must not
    // fail on a duplicate column.
    assert!(
        migration_rows(&db)
            .await?
            .iter()
            .any(|(version, name, _)| *version == 11 && name == "group_opening_message")
    );
    Ok(())
}

// Coverage for the column-add branch of the v11 repair: a legacy
// bcs_groups without opening_message_json gets the column back.
#[tokio::test]
async fn sqlite_group_opening_message_column_repair_adds_missing_column() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_groups (group_id TEXT PRIMARY KEY, name TEXT NOT NULL)",
    ))
    .await?;
    ensure_sqlite_group_opening_message_column(&db).await?;
    let columns = sqlite_table_columns(&db, "bcs_groups").await?;
    assert!(
        columns.iter().any(|column| column == "opening_message_json"),
        "legacy bcs_groups must gain opening_message_json; got {columns:?}"
    );
    Ok(())
}

#[tokio::test]
async fn sqlite_migrations_repair_legacy_channel_binding_created_at() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            dialect TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )",
    ))
    .await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (?, ?, ?, ?)",
        vec![
            DbValue::from(1_i64),
            DbValue::from("init_schema"),
            DbValue::from("sqlite"),
            DbValue::from(sqlite_migration_checksum(&SQLITE_VERSIONED_MIGRATIONS[0])),
        ],
    ))
    .await?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_channel_bindings (
            id TEXT PRIMARY KEY,
            channel_type TEXT NOT NULL,
            account_ref TEXT NOT NULL,
            target_json TEXT NOT NULL,
            group_chat_scope TEXT DEFAULT NULL,
            visibility TEXT NOT NULL,
            env TEXT NOT NULL,
            status TEXT NOT NULL,
            created_by TEXT DEFAULT NULL,
            created_at INTEGER NOT NULL,
            config_json TEXT NOT NULL
        )",
    ))
    .await?;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_channel_bindings \
         (id, channel_type, account_ref, target_json, group_chat_scope, visibility, env, status, created_by, created_at, config_json) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from("legacy_binding"),
            DbValue::from("dingtalk"),
            DbValue::from("robot_1"),
            DbValue::from(r#"{"type":"group","group_id":"group_1"}"#),
            DbValue::from("per_sender"),
            DbValue::from("full_transcript"),
            DbValue::from("dev"),
            DbValue::from("active"),
            DbValue::from("creator"),
            DbValue::from(100_i64),
            DbValue::from(r#"{"send_mode":{"mode":"normal"}}"#),
        ],
    ))
    .await?;

    run_sqlite_migrations(&db).await?;

    let columns = column_names(&db, "bcs_channel_bindings").await?;
    assert!(columns.iter().any(|column| column == "gmt_create"));
    assert!(columns.iter().any(|column| column == "gmt_modified"));
    assert!(!columns.iter().any(|column| column == "created_at"));
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_channel_bindings \
         (id, channel_type, account_ref, target_json, group_chat_scope, visibility, env, status, created_by, config_json) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from("new_binding"),
            DbValue::from("dingtalk"),
            DbValue::from("robot_2"),
            DbValue::from(r#"{"type":"group","group_id":"group_2"}"#),
            DbValue::from("per_sender"),
            DbValue::from("full_transcript"),
            DbValue::from("dev"),
            DbValue::from("active"),
            DbValue::from("creator"),
            DbValue::from(r#"{"send_mode":{"mode":"normal"}}"#),
        ],
    ))
    .await?;

    Ok(())
}

#[tokio::test]
async fn sqlite_migration_checksum_mismatch_errors() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    db.execute(DbStatement::with_params(
        "UPDATE bcs_schema_migrations SET checksum = ? WHERE version = ?",
        vec![DbValue::from("bad-checksum"), DbValue::from(1_i64)],
    ))
    .await?;

    let err = run_sqlite_migrations(&db)
        .await
        .expect_err("checksum mismatch should fail startup");

    assert!(err.to_string().contains("checksum mismatch"));
    Ok(())
}

#[tokio::test]
async fn sqlite_bootstrap_adds_internal_attributes_to_legacy_bots() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, PRIMARY KEY (bot_uuid, env))",
    ))
    .await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_bots (bot_uuid, env) VALUES ('legacy-bot', 'dev')",
    ))
    .await?;

    ensure_sqlite_bot_internal_attributes(&db).await?;

    let columns = column_names(&db, "bcs_bots").await?;
    assert!(columns.iter().any(|column| column == "user_visibility"));
    assert!(columns.iter().any(|column| column == "friend_ext"));
    assert!(
        columns
            .iter()
            .any(|column| column == "friend_check_in_strategy")
    );
    let rows = db
        .query(DbStatement::new(
            "SELECT user_visibility, friend_check_in_strategy FROM bcs_bots WHERE bot_uuid = 'legacy-bot'",
        ))
        .await?;
    let row = rows.first().expect("legacy Bot row");
    assert_eq!(
        db_get_column::<String>(row, "user_visibility")?,
        "protected"
    );
    assert_eq!(
        db_get_column::<String>(row, "friend_check_in_strategy")?,
        "APPROVAL"
    );
    Ok(())
}

// 建表要求: every edge-permission table must carry gmt_create / gmt_modified.
#[tokio::test]
async fn fresh_migrations_create_edge_tables_with_gmt_audit_columns() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    for table in [
        "edge_grants",
        "permission_profiles",
        "permission_requests",
        "capabilities",
        "authz_decision_logs",
    ] {
        let columns = column_names(&db, table).await?;
        assert!(
            columns.iter().any(|c| c == "gmt_create"),
            "{table} missing gmt_create"
        );
        assert!(
            columns.iter().any(|c| c == "gmt_modified"),
            "{table} missing gmt_modified"
        );
    }
    let request_columns = column_names(&db, "permission_requests").await?;
    assert!(request_columns.iter().any(|c| c == "request_id"));
    Ok(())
}

// Repair path: a legacy DB that created the edge tables without gmt_* must
// get the audit columns backfilled by the idempotent rebuild in the migration.
#[tokio::test]
async fn edge_table_audit_columns_backfilled_for_legacy_db() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    // Legacy shape: edge_grants as built before the gmt_* audit-column
    // requirement landed (current bigint PK + real columns, minus gmt_create/gmt_modified).
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, \
         from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, \
         grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', \
         originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT)",
    ))
    .await?;
    // add_sqlite_edge_permission_schema also ALTERs bcs_bots; give it a stub.
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, \
         PRIMARY KEY (bot_uuid, env))",
    ))
    .await?;
    // Re-running the edge-permission migration (v9) must ADD the gmt columns
    // via the idempotent rebuild repair (CREATE TABLE IF NOT EXISTS is a no-op
    // and SQLite forbids ADD COLUMN with a CURRENT_TIMESTAMP default).
    add_sqlite_edge_permission_schema(&db).await?;
    let columns = column_names(&db, "edge_grants").await?;
    assert!(columns.iter().any(|c| c == "gmt_create"));
    assert!(columns.iter().any(|c| c == "gmt_modified"));
    Ok(())
}
