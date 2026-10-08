use super::*;

pub(super) async fn ensure_sqlite_group_opening_message_column(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_groups").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_groups").await?;
    if !columns
        .iter()
        .any(|column| column == "opening_message_json")
    {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_groups ADD COLUMN opening_message_json TEXT DEFAULT NULL",
        ))
        .await?;
    }
    Ok(())
}

pub(super) async fn ensure_sqlite_message_owner_bot_id(db: &dyn DbPlugin) -> DbResult<()> {
    let columns = db
        .query(DbStatement::new("PRAGMA table_info(bcs_messages)"))
        .await?;
    let mut has_owner_bot_id = false;
    for row in &columns {
        if row.get_string("name")?.as_deref() == Some("owner_bot_id") {
            has_owner_bot_id = true;
            break;
        }
    }
    if !has_owner_bot_id {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_messages ADD COLUMN owner_bot_id TEXT DEFAULT NULL",
        ))
        .await?;
    }
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_owner_created \
         ON bcs_messages(session_id, owner_bot_id, created_at, session_seq)",
    ))
    .await?;
    Ok(())
}

pub(super) async fn ensure_sqlite_bot_task_modes(db: &dyn DbPlugin) -> DbResult<()> {
    let columns = db
        .query(DbStatement::new("PRAGMA table_info(bcs_bots)"))
        .await?;
    let mut has_claim = false;
    let mut has_dream = false;
    for row in &columns {
        match row.get_string("name")?.as_deref() {
            Some("task_claim_mode") => has_claim = true,
            Some("task_dream_mode") => has_dream = true,
            _ => {}
        }
        if has_claim && has_dream {
            break;
        }
    }
    if !has_claim {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN task_claim_mode INTEGER NOT NULL DEFAULT 0",
        ))
        .await?;
    }
    if !has_dream {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN task_dream_mode INTEGER NOT NULL DEFAULT 0",
        ))
        .await?;
    }
    Ok(())
}

pub(super) async fn ensure_sqlite_bot_internal_attributes(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_bots").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_bots").await?;
    if !columns.iter().any(|column| column == "user_visibility") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN user_visibility TEXT NOT NULL DEFAULT 'protected'",
        ))
        .await?;
    }
    if !columns.iter().any(|column| column == "friend_ext") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN friend_ext TEXT DEFAULT NULL",
        ))
        .await?;
    }
    if !columns
        .iter()
        .any(|column| column == "friend_check_in_strategy")
    {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_bots ADD COLUMN friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL'",
        ))
        .await?;
    }
    Ok(())
}

pub(super) async fn ensure_sqlite_session_collected_column(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_session_participants").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_session_participants").await?;
    if !columns.iter().any(|column| column == "collected") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_session_participants ADD COLUMN collected INTEGER NOT NULL DEFAULT 0",
        ))
        .await?;
    }
    // collected_at: collected event timestamp (nullable). Added in the same
    // repair pass so legacy DBs gain both columns without a separate run.
    if !columns.iter().any(|column| column == "collected_at") {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_session_participants ADD COLUMN collected_at TEXT",
        ))
        .await?;
    }
    // The composite index covers (env, group_id, bot_uuid, collected) as a prefix
    // and so also serves any query the former idx_collected did — keep only this
    // one to avoid redundant write overhead. collected_at trailing lets the same
    // index satisfy the collected-list ORDER BY.
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_collected_at \
         ON bcs_session_participants(env, group_id, bot_uuid, collected, collected_at)",
    ))
    .await?;
    Ok(())
}

/// Ensure bcs_session_files table exists. For fresh databases the table is created
/// by run_sqlite_bootstrap_tables via SQLITE_DDL_STATEMENTS; this function handles
/// legacy databases and future schema repairs for the session_files table.
pub(super) async fn ensure_bcs_session_files(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_session_files").await? {
        db.execute(DbStatement::new(
            "CREATE TABLE IF NOT EXISTS bcs_session_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                env TEXT NOT NULL,
                file_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                owner_actor_kind TEXT NOT NULL,
                owner_actor_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT,
                storage_backend TEXT NOT NULL,
                object_handle TEXT NOT NULL,
                status TEXT NOT NULL
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_session_file \
             ON bcs_session_files (env, session_id, file_id)",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_env_file_id \
             ON bcs_session_files (env, file_id)",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE INDEX IF NOT EXISTS idx_session_files_session \
             ON bcs_session_files (env, session_id, gmt_create)",
        ))
        .await?;
    }
    Ok(())
}

pub(super) async fn add_sqlite_fixed_loop_runtime_schema(db: &dyn DbPlugin) -> DbResult<()> {
    // This migration contains thirteen plain DDL statements, without routines or
    // semicolons in literals. Keep the column guards tied to their exact DDL.
    let statements = include_str!("../../../../../migrations/sqlite/029_fixed_loop_runtime.sql")
        .split(';').map(str::trim).filter(|sql| !sql.is_empty()).collect::<Vec<_>>();
    let [plan, hash, compiler, failure_action, phase, owner, token, lease_until, progression, session_recovery, checkpoints, checkpoint_index, terminal_im_index] = statements.as_slice() else {
        return Err(DbError::InvalidInput("unexpected fixed Loop migration statements".into()));
    };
    for (table, additions) in [
        ("bcs_state_machine_definition_snapshots", vec![
            ("execution_plan_json", *plan),
            ("execution_plan_content_hash", *hash),
            ("execution_plan_compiler_version", *compiler),
        ]),
        ("bcs_state_machine_node_runs", vec![("failure_action", *failure_action),
            ("runtime_phase", *phase), ("recovery_lease_owner", *owner),
            ("recovery_lease_token", *token), ("recovery_lease_until_ms", *lease_until)]),
    ] {
        let columns = sqlite_table_columns(db, table).await?;
        for (column, sql) in additions {
            if !sql.starts_with(&format!("ALTER TABLE {table} ADD COLUMN {column} ")) {
                return Err(DbError::InvalidInput(format!("unexpected fixed Loop DDL for {table}.{column}")));
            }
            if !columns.iter().any(|existing| existing == column) {
                db.execute(DbStatement::new(sql)).await?;
            }
        }
    }
    // CREATE INDEX IF NOT EXISTS also resumes after an unrecorded partial apply.
    for sql in [*progression, *session_recovery, *checkpoints, *checkpoint_index, *terminal_im_index] {
        db.execute(DbStatement::new(sql)).await?;
    }
    Ok(())
}

pub(super) async fn add_sqlite_invite_code_id_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_invite_codes").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_invite_codes").await?;
    if columns.iter().any(|column| column == "id") {
        return Ok(());
    }
    db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE IF EXISTS bcs_invite_codes__id_migration",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "CREATE TABLE bcs_invite_codes__id_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT NOT NULL UNIQUE,
                code_hint TEXT NOT NULL,
                status TEXT NOT NULL,
                bound_user_id TEXT NULL UNIQUE,
                bound_at INTEGER NULL,
                created_by TEXT NULL,
                env TEXT NOT NULL,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "INSERT INTO bcs_invite_codes__id_migration (
                code_hash, code_hint, status, bound_user_id, bound_at, created_by, env, gmt_create, gmt_modified
            )
            SELECT code_hash, code_hint, status, bound_user_id, bound_at, created_by, env, gmt_create, gmt_modified
            FROM bcs_invite_codes",
        )),
        DbTransactionStep::Execute(DbStatement::new("DROP TABLE bcs_invite_codes")),
        DbTransactionStep::Execute(DbStatement::new(
            "ALTER TABLE bcs_invite_codes__id_migration RENAME TO bcs_invite_codes",
        )),
    ])
    .await?;
    Ok(())
}

pub(super) async fn add_sqlite_human_participant_message_visibility_schema(
    db: &dyn DbPlugin,
) -> DbResult<()> {
    if table_exists(db, "bcs_group_participants").await? {
        let columns = sqlite_table_columns(db, "bcs_group_participants").await?;
        if !columns.iter().any(|column| column == "message_view_scope") {
            db.execute(DbStatement::new(
                "ALTER TABLE bcs_group_participants \
                 ADD COLUMN message_view_scope TEXT NOT NULL DEFAULT 'full'",
            ))
            .await?;
        }
    }

    if table_exists(db, "bcs_group_sessions").await? {
        let columns = sqlite_table_columns(db, "bcs_group_sessions").await?;
        if !columns
            .iter()
            .any(|column| column == "message_visibility_version")
        {
            db.execute(DbStatement::new(
                "ALTER TABLE bcs_group_sessions \
                 ADD COLUMN message_visibility_version INTEGER NOT NULL DEFAULT 0",
            ))
            .await?;
        }
    }

    if table_exists(db, "bcs_messages").await? {
        let columns = sqlite_table_columns(db, "bcs_messages").await?;
        for (name, definition) in [
            ("visibility_domain", "TEXT DEFAULT NULL"),
            ("audience_kind", "TEXT DEFAULT NULL"),
            ("audience_actor_ids_json", "TEXT DEFAULT NULL"),
        ] {
            if !columns.iter().any(|column| column == name) {
                db.execute(DbStatement::new(format!(
                    "ALTER TABLE bcs_messages ADD COLUMN {name} {definition}"
                )))
                .await?;
            }
        }
        db.execute(DbStatement::new(
            "CREATE INDEX IF NOT EXISTS idx_messages_session_audience_created \
             ON bcs_messages(session_id, visibility_domain, audience_kind, created_at, session_seq)",
        ))
        .await?;
    }

    Ok(())
}

pub(super) async fn add_sqlite_one_shot_opening_message_override_schema(
    db: &dyn DbPlugin,
) -> DbResult<()> {
    if !table_exists(db, "bcs_state_machine_runs").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_state_machine_runs").await?;
    if !columns
        .iter()
        .any(|column| column == "opening_message_override_json")
    {
        db.execute(DbStatement::new(
            "ALTER TABLE bcs_state_machine_runs \
             ADD COLUMN opening_message_override_json TEXT DEFAULT NULL",
        ))
        .await?;
    }
    Ok(())
}

pub(super) async fn add_sqlite_state_machine_rerun_lineage_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_state_machine_runs").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_state_machine_runs").await?;
    for (name, definition) in [
        ("root_run_id", "TEXT DEFAULT NULL"),
        ("rerun_of", "TEXT DEFAULT NULL"),
        ("session_activation_count", "INTEGER DEFAULT NULL"),
    ] {
        if !columns.iter().any(|column| column == name) {
            db.execute(DbStatement::new(format!(
                "ALTER TABLE bcs_state_machine_runs ADD COLUMN {name} {definition}"
            )))
            .await?;
        }
    }
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_sm_run_rerun_of \
         ON bcs_state_machine_runs(env, rerun_of)",
    ))
    .await?;
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_sm_runs_root \
         ON bcs_state_machine_runs(env, root_run_id, created_at_ms)",
    ))
    .await?;
    Ok(())
}

pub(super) async fn add_sqlite_session_callback_lease_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_group_sessions").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_group_sessions").await?;
    for (name, definition) in [
        ("callback_lease_owner", "TEXT DEFAULT NULL"),
        ("callback_lease_token", "INTEGER DEFAULT NULL"),
        ("callback_lease_until_ms", "INTEGER DEFAULT NULL"),
    ] {
        if !columns.iter().any(|column| column == name) {
            db.execute(DbStatement::new(format!(
                "ALTER TABLE bcs_group_sessions ADD COLUMN {name} {definition}"
            )))
            .await?;
        }
    }
    db.execute(DbStatement::new(
        "CREATE INDEX IF NOT EXISTS idx_session_callback_recovery \
         ON bcs_group_sessions(env, session_kind, status, callback_status, \
         callback_lease_token, callback_lease_until_ms, session_id)",
    ))
    .await?;
    Ok(())
}

pub(super) async fn migrate_sqlite_eventing_plaintext_endpoint(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_event_subscription_revisions").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_event_subscription_revisions").await?;
    if columns.iter().any(|column| column == "endpoint_url") {
        return Ok(());
    }
    if !columns.iter().any(|column| column == "endpoint_ciphertext") {
        return Err(DbError::InvalidInput(
            "unsupported bcs_event_subscription_revisions schema".to_string(),
        ));
    }
    let rows = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS revision_count FROM bcs_event_subscription_revisions",
        ))
        .await?;
    let revision_count: i64 = db_get_column(&rows[0], "revision_count")?;
    if revision_count != 0 {
        return Err(DbError::InvalidInput(
            "cannot automatically replace encrypted Event Subscription endpoints; disable and recreate existing Subscriptions first"
                .to_string(),
        ));
    }

    db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE IF EXISTS bcs_event_subscription_revisions__plaintext_migration",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "CREATE TABLE bcs_event_subscription_revisions__plaintext_migration (
                subscription_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_filters_json TEXT NOT NULL,
                payload_mode TEXT NOT NULL,
                endpoint_url TEXT NOT NULL,
                request_timeout_ms INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                retired_at TEXT DEFAULT NULL,
                env TEXT NOT NULL,
                PRIMARY KEY(subscription_id, revision)
            )",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE bcs_event_subscription_revisions",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "ALTER TABLE bcs_event_subscription_revisions__plaintext_migration
             RENAME TO bcs_event_subscription_revisions",
        )),
    ])
    .await?;
    Ok(())
}

pub(super) async fn add_sqlite_group_participant_tags_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if table_exists(db, "bcs_group_participants").await? {
        let columns = sqlite_table_columns(db, "bcs_group_participants").await?;
        if !columns.iter().any(|column| column == "tags_json") {
            db.execute(DbStatement::new(
                "ALTER TABLE bcs_group_participants ADD COLUMN tags_json TEXT DEFAULT NULL",
            ))
            .await?;
        }
    }
    Ok(())
}

pub(super) async fn add_sqlite_human_input_output_metadata_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if table_exists(db, "bcs_state_machine_node_runs").await? {
        let columns = sqlite_table_columns(db, "bcs_state_machine_node_runs").await?;
        let additions = [
            ("outcome", "TEXT DEFAULT NULL"),
            ("responded_by", "TEXT DEFAULT NULL"),
        ];
        for (name, definition) in additions {
            if !columns.iter().any(|column| column == name) {
                db.execute(DbStatement::new(format!(
                    "ALTER TABLE bcs_state_machine_node_runs ADD COLUMN {name} {definition}"
                )))
                .await?;
            }
        }
    }
    Ok(())
}

pub(super) async fn add_sqlite_edge_permission_schema(db: &dyn DbPlugin) -> DbResult<()> {
    // Five edge-permission tables (idempotent; spec §3.1). `(create_sql,
    // data_columns)` pairs feed the gmt_* audit repair below; indexes are
    // created after the repair because a rebuilt table drops its old
    // indexes, which would otherwise be lost until the next migration run.
    const EDGE_TABLES: &[(&str, &str, &[&str])] = &[
        (
            "edge_grants",
            "CREATE TABLE IF NOT EXISTS edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            &["id", "env", "from_id", "to_id", "grant_kind", "grant_ref_id", "rules", "status", "originator_policy_type", "originator_policy_data"],
        ),
        (
            "permission_profiles",
            "CREATE TABLE IF NOT EXISTS permission_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL DEFAULT 'default', description TEXT, rules_template TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, digest TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', created_by TEXT NOT NULL, updated_by TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            &["id", "bot_id", "env", "name", "description", "rules_template", "revision", "digest", "is_default", "status", "created_by", "updated_by"],
        ),
        (
            "permission_requests",
            "CREATE TABLE IF NOT EXISTS permission_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, edge_id INTEGER, env TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, request_kind TEXT NOT NULL, requested_ref_id INTEGER, requested_rules TEXT, message TEXT, status TEXT NOT NULL DEFAULT 'pending', decision_reason TEXT, created_by TEXT NOT NULL, decided_by TEXT, decided_at TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            &["id", "request_id", "edge_id", "env", "from_id", "to_id", "request_kind", "requested_ref_id", "requested_rules", "message", "status", "decision_reason", "created_by", "decided_by", "decided_at"],
        ),
        (
            "capabilities",
            "CREATE TABLE IF NOT EXISTS capabilities (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, env TEXT NOT NULL, tool TEXT NOT NULL, operation TEXT, specifier_schema TEXT, source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', raw_metadata TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            &["id", "bot_id", "env", "tool", "operation", "specifier_schema", "source", "status", "raw_metadata"],
        ),
        (
            "authz_decision_logs",
            "CREATE TABLE IF NOT EXISTS authz_decision_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, task_id TEXT, run_id TEXT, from_id TEXT NOT NULL, to_id TEXT NOT NULL, originator TEXT, context_type TEXT NOT NULL, decision TEXT NOT NULL, reason_code TEXT NOT NULL, grant_refs TEXT NOT NULL, context_json TEXT, gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            &["id", "env", "task_id", "run_id", "from_id", "to_id", "originator", "context_type", "decision", "reason_code", "grant_refs", "context_json"],
        ),
    ];
    for (_, create_sql, _) in EDGE_TABLES {
        db.execute(DbStatement::new(*create_sql)).await?;
    }
    // Edge tables: backfill gmt_create / gmt_modified audit columns for DBs
    // that created the tables before the audit-column requirement landed.
    // CREATE TABLE IF NOT EXISTS will not add columns to an existing table,
    // and SQLite forbids ADD COLUMN with a non-constant default
    // (CURRENT_TIMESTAMP), so repair idempotently via table rebuild
    // (spec §3.1 — 建表要求 gmt_create/gmt_modified).
    for (table, create_sql, data_columns) in EDGE_TABLES {
        if !table_exists(db, table).await? {
            continue;
        }
        let columns = sqlite_table_columns(db, table).await?;
        let missing_audit_column = ["gmt_create", "gmt_modified"]
            .iter()
            .any(|name| !columns.iter().any(|column| column == name));
        if !missing_audit_column {
            continue;
        }
        rebuild_sqlite_edge_table_with_gmt_columns(db, table, create_sql, data_columns).await?;
    }
    for spec in [
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_from_to_env_ref ON edge_grants(from_id, to_id, env, grant_ref_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_from_env_status ON edge_grants(from_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_edge_to_env_status ON edge_grants(to_id, env, status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_profile_bot_env_default ON permission_profiles(bot_id, env, is_default) WHERE status = 'active'",
        "CREATE INDEX IF NOT EXISTS idx_profile_bot_env ON permission_profiles(bot_id, env, status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uk_req_request_id ON permission_requests(request_id)",
        "CREATE INDEX IF NOT EXISTS idx_req_to_env_status ON permission_requests(to_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_req_from_env_status ON permission_requests(from_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_req_edge ON permission_requests(edge_id)",
        "CREATE INDEX IF NOT EXISTS idx_cap_bot_env ON capabilities(bot_id, env, status)",
        "CREATE INDEX IF NOT EXISTS idx_adl_env_from_to ON authz_decision_logs(env, from_id, to_id)",
    ] {
        db.execute(DbStatement::new(spec)).await?;
    }
    Ok(())
}

/// Rebuild an edge-permission table so it carries the gmt_create/gmt_modified
/// audit columns with `DEFAULT CURRENT_TIMESTAMP`, preserving every other
/// column's values (binary rebuild: create shadow, copy, drop, rename).
pub(super) async fn rebuild_sqlite_edge_table_with_gmt_columns(
    db: &dyn DbPlugin,
    table: &str,
    create_sql: &str,
    data_columns: &[&str],
) -> DbResult<()> {
    let existing = sqlite_table_columns(db, table).await?;
    // Copy every data column the legacy table actually has; an audit column
    // that already exists keeps its values, a missing one takes the fresh
    // table's DEFAULT CURRENT_TIMESTAMP backfill.
    let mut copy_columns: Vec<&str> = data_columns
        .iter()
        .copied()
        .filter(|column| existing.iter().any(|present| present == column))
        .collect();
    for audit_column in ["gmt_create", "gmt_modified"] {
        if existing.iter().any(|present| present == audit_column) {
            copy_columns.push(audit_column);
        }
    }
    let copy_list = copy_columns.join(", ");
    let rebuild_table = format!("{table}__gmt_audit_rebuild");
    let create_rebuild =
        create_sql.replacen(&format!("{table} ("), &format!("{rebuild_table} ("), 1);
    db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(format!(
            "DROP TABLE IF EXISTS {rebuild_table}"
        ))),
        DbTransactionStep::Execute(DbStatement::new(create_rebuild)),
        DbTransactionStep::Execute(DbStatement::new(format!(
            "INSERT INTO {rebuild_table} ({copy_list}) SELECT {copy_list} FROM {table}"
        ))),
        DbTransactionStep::Execute(DbStatement::new(format!("DROP TABLE {table}"))),
        DbTransactionStep::Execute(DbStatement::new(format!(
            "ALTER TABLE {rebuild_table} RENAME TO {table}"
        ))),
    ])
    .await?;
    Ok(())
}

pub(super) async fn repair_sqlite_channel_bindings_audit_schema(db: &dyn DbPlugin) -> DbResult<()> {
    if !table_exists(db, "bcs_channel_bindings").await? {
        return Ok(());
    }
    let columns = sqlite_table_columns(db, "bcs_channel_bindings").await?;
    let has_created_at = columns.iter().any(|column| column == "created_at");
    let has_gmt_create = columns.iter().any(|column| column == "gmt_create");
    let has_gmt_modified = columns.iter().any(|column| column == "gmt_modified");
    if has_gmt_create && has_gmt_modified && !has_created_at {
        return Ok(());
    }

    let gmt_create_expr = sqlite_channel_audit_expr(has_gmt_create, has_created_at, "gmt_create");
    let gmt_modified_expr =
        sqlite_channel_audit_expr(has_gmt_modified, has_created_at, "gmt_modified");
    db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new(
            "DROP TABLE IF EXISTS bcs_channel_bindings__audit_migration",
        )),
        DbTransactionStep::Execute(DbStatement::new(
            "CREATE TABLE bcs_channel_bindings__audit_migration (
                id TEXT PRIMARY KEY,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                channel_type TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                target_json TEXT NOT NULL,
                group_chat_scope TEXT DEFAULT NULL,
                visibility TEXT NOT NULL,
                env TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT DEFAULT NULL,
                config_json TEXT NOT NULL
            )",
        )),
        DbTransactionStep::Execute(DbStatement::new(format!(
            "INSERT INTO bcs_channel_bindings__audit_migration \
             (id, gmt_create, gmt_modified, channel_type, account_ref, target_json, group_chat_scope, \
              visibility, env, status, created_by, config_json) \
             SELECT id, {gmt_create_expr}, {gmt_modified_expr}, channel_type, account_ref, target_json, group_chat_scope, \
                    visibility, env, status, created_by, config_json \
             FROM bcs_channel_bindings"
        ))),
        DbTransactionStep::Execute(DbStatement::new("DROP TABLE bcs_channel_bindings")),
        DbTransactionStep::Execute(DbStatement::new(
            "ALTER TABLE bcs_channel_bindings__audit_migration RENAME TO bcs_channel_bindings",
        )),
    ])
    .await?;
    Ok(())
}

pub(super) fn sqlite_channel_audit_expr(
    has_audit_column: bool,
    has_created_at: bool,
    audit_column: &'static str,
) -> &'static str {
    if has_audit_column {
        audit_column
    } else if has_created_at {
        "datetime(created_at / 1000, 'unixepoch')"
    } else {
        "CURRENT_TIMESTAMP"
    }
}
