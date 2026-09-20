use super::*;

async fn sqlite_before_fixed_loop(db: &dyn DbPlugin) -> DbResult<()> {
    run_sqlite_bootstrap_tables(db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|migration| migration.version <= 28) {
        apply_sqlite_migration(db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(db).await
}

async fn assert_fixed_loop_schema(db: &dyn DbPlugin) -> DbResult<()> {
    let columns = column_names(db, "bcs_state_machine_definition_snapshots").await?;
    for name in ["execution_plan_json", "execution_plan_content_hash", "execution_plan_compiler_version"] {
        assert!(columns.iter().any(|column| column == name));
    }
    let node_columns = column_names(db, "bcs_state_machine_node_runs").await?;
    for name in ["failure_action", "runtime_phase", "recovery_lease_owner", "recovery_lease_token", "recovery_lease_until_ms"] {
        assert!(node_columns.iter().any(|column| column == name));
    }
    assert!(index_exists(db, "idx_sm_runs_progression").await?);
    assert!(index_exists(db, "idx_session_running_recovery").await?);
    assert!(index_exists(db, "idx_collaboration_checkpoints_run").await?);
    assert!(index_exists(db, "idx_collaboration_terminal_im_scan").await?);
    let checkpoint_columns = column_names(db, "bcs_collaboration_delivery_checkpoints").await?;
    for name in ["env", "operation_key", "aggregate_kind", "aggregate_id", "operation_kind", "payload_json", "progress_json", "status", "created_at_ms", "delivered_at_ms", "node_id", "aggregate_attempt", "deadline_ms", "lease_owner", "lease_token", "lease_until_ms", "last_error"] {
        assert!(checkpoint_columns.iter().any(|column| column == name));
    }
    assert_eq!(current_sqlite_version(db, true).await?, Some(29));
    Ok(())
}

#[tokio::test]
async fn fixed_loop_runtime_migration_is_applied_on_fresh_bootstrap() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(migration_rows(&db).await?.into_iter().map(|row| row.0).collect::<Vec<_>>(),
        (1..=29).collect::<Vec<_>>());
    assert_fixed_loop_schema(&db).await
}

#[tokio::test]
async fn fixed_loop_runtime_migration_upgrades_28_and_resumes_each_partial_step() -> DbResult<()> {
    let statements = include_str!("../../../../../../migrations/sqlite/029_fixed_loop_runtime.sql")
        .split(';').map(str::trim).filter(|sql| !sql.is_empty()).collect::<Vec<_>>();
    assert_eq!(statements.len(), 13);
    for completed_steps in 0..=statements.len() {
        let db = LocalSqliteDbPlugin::new()?;
        sqlite_before_fixed_loop(&db).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_state_machine_definition_snapshots (env, run_id, group_id, session_id, group_version, definition_id, definition_version, definition_content_hash, snapshot_json) VALUES ('test', 'legacy-run', 'group', 'session', 1, 'definition', 1, 'hash', '{\"version\":1}')")).await?;
        db.execute(DbStatement::new("INSERT INTO bcs_state_machine_node_runs (env, run_id, node_id, status, assignee_bot_id, error_message) VALUES ('test', 'legacy-run', 'node', 'failed', 'bot', 'legacy failure')")).await?;
        db.execute(DbStatement::new("UPDATE bcs_schema_migrations SET applied_at = '2026-01-01 00:00:00'")).await?;
        for sql in &statements[..completed_steps] {
            db.execute(DbStatement::new(*sql)).await?;
        }
        assert!(applied_sqlite_migration(&db, 29).await?.is_none());
        run_sqlite_migrations(&db).await?;
        run_sqlite_migrations(&db).await?;
        assert_fixed_loop_schema(&db).await?;
        let row = db.query(DbStatement::new("SELECT snapshot_json, execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version FROM bcs_state_machine_definition_snapshots WHERE run_id = 'legacy-run'")).await?.remove(0);
        assert_eq!(db_get_column::<String>(&row, "snapshot_json")?, "{\"version\":1}");
        for column in ["execution_plan_json", "execution_plan_content_hash", "execution_plan_compiler_version"] {
            assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, column)?, None);
        }
        let row = db.query(DbStatement::new("SELECT error_message, failure_action, runtime_phase, recovery_lease_owner, recovery_lease_token, recovery_lease_until_ms FROM bcs_state_machine_node_runs WHERE run_id = 'legacy-run'")).await?.remove(0);
        assert_eq!(db_get_column::<String>(&row, "error_message")?, "legacy failure");
        assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, "failure_action")?, None);
        assert_eq!(db_get_column::<i64>(&row, "recovery_lease_token")?, 0);
        for column in ["runtime_phase", "recovery_lease_owner", "recovery_lease_until_ms"] {
            assert_eq!(bcs_db_api::db_get_column_opt::<String>(&row, column)?, None);
        }
        let old_records = db.query(DbStatement::new("SELECT applied_at FROM bcs_schema_migrations WHERE version <= 28")).await?;
        assert_eq!(old_records.len(), 28);
        for row in old_records { assert_eq!(db_get_column::<String>(&row, "applied_at")?, "2026-01-01 00:00:00"); }
    }
    Ok(())
}

#[tokio::test]
async fn fixed_loop_runtime_migration_records_only_after_all_steps_succeed() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    sqlite_before_fixed_loop(&db).await?;
    db.execute(DbStatement::new("DROP TABLE bcs_group_sessions")).await?;
    let migration = SQLITE_VERSIONED_MIGRATIONS.iter().find(|migration| migration.version == 29).unwrap();
    assert!(apply_sqlite_migration(&db, migration).await.is_err());
    assert!(applied_sqlite_migration(&db, 29).await?.is_none());
    assert!(index_exists(&db, "idx_sm_runs_progression").await?);
    // Recreate the missing prerequisite, then resume the unrecorded prefix.
    run_sqlite_migrations(&db).await?;
    assert_fixed_loop_schema(&db).await
}

#[tokio::test]
async fn fixed_loop_runtime_migration_rejects_conflicting_record() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    sqlite_before_fixed_loop(&db).await?;
    let draft = SqliteMigration { version: 29, name: "fixed_loop_execution_plan" };
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (29, ?, 'sqlite', ?)",
        vec![DbValue::from(draft.name), DbValue::from(sqlite_migration_checksum(&draft))],
    )).await?;
    assert!(run_sqlite_migrations(&db).await.unwrap_err().to_string().contains("checksum mismatch"));
    assert!(!column_names(&db, "bcs_state_machine_node_runs").await?.iter().any(|column| column == "failure_action"));
    assert_eq!(applied_sqlite_migration(&db, 29).await?.unwrap().name, draft.name);
    Ok(())
}
