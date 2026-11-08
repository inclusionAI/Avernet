//! Retirement preserves the deployed schema, legacy values and execution facts.
use super::*;

#[tokio::test]
async fn retired_service_policy_preserves_schema_and_history_across_restarts() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|m| m.version <= 37)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(&db).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_groups (group_id, status, driver_bot, env, service_spec) VALUES ('retired-policy', 'active', 'driver', 'dev', '{\"max_concurrency\":1}')")).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, session_kind, status, activation_count, output, completed_at, callback_status, participants) VALUES ('retired-policy:12345678', 'retired-policy', 'dev', 'service_invocation', 'completed', 4, '{\"answer\":42}', 1234, 'pending', '[]')")).await?;
    let schema_sql = "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name";
    let schema = db.query(DbStatement::new(schema_sql)).await?;
    let history = migration_rows(&db).await?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(schema_sql)).await?, schema);
    assert_eq!(migration_rows(&db).await?, history);
    assert!(
        column_names(&db, "bcs_groups")
            .await?
            .contains(&"service_spec".to_string())
    );
    let columns = column_names(&db, "bcs_group_sessions").await?;
    for column in ["callback_status", "callback_lease_owner", "callback_lease_token", "callback_lease_until_ms"] {
        assert!(columns.iter().any(|name| name == column), "missing {column}");
    }
    assert!(index_exists(&db, "idx_sessions_callback_status").await?);
    assert!(index_exists(&db, "idx_session_callback_recovery").await?);
    let groups = db.query(DbStatement::new("SELECT service_spec FROM bcs_groups WHERE group_id = 'retired-policy'")).await?;
    assert_eq!(db_get_column::<String>(&groups[0], "service_spec")?, r#"{"max_concurrency":1}"#);
    let rows = db.query(DbStatement::new("SELECT status, activation_count, output, completed_at, callback_status FROM bcs_group_sessions WHERE session_id = 'retired-policy:12345678'")).await?;
    assert_eq!(rows.len(), 1);
    assert_eq!(db_get_column::<String>(&rows[0], "callback_status")?, "pending");
    assert_eq!(db_get_column::<String>(&rows[0], "status")?, "completed");
    assert_eq!(db_get_column::<i64>(&rows[0], "activation_count")?, 4);
    assert_eq!(db_get_column::<i64>(&rows[0], "completed_at")?, 1234);
    assert_eq!(
        db_get_column::<String>(&rows[0], "output")?,
        r#"{"answer":42}"#
    );
    Ok(())
}
