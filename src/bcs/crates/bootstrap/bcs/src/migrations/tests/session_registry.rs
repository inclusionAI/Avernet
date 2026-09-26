//! Upgrade from the committed SQLite 31 schema without rewriting its history.
use super::*;

#[tokio::test]
async fn session_registry_upgrade_backfills_group_identity_and_isolates_environment() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|migration| migration.version <= 31) {
        apply_sqlite_migration(&db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(&db).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (env, session_id, group_id, participants, current_msg_seq) VALUES ('dev', 'shared', 'group', '[]', 42)")).await?;
    let history = "SELECT version, name, dialect, checksum FROM bcs_schema_migrations WHERE version <= 31 ORDER BY version";
    let before = db.query(DbStatement::new(history)).await?;
    assert!(!column_names(&db, "bcs_chat_runs").await?.contains(&"delivery_id".to_string()));
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(before, db.query(DbStatement::new(history)).await?);
    let rows = db.query(DbStatement::new("SELECT session_type, current_msg_seq FROM bcs_session_registry WHERE env = 'dev' AND session_id = 'shared'")).await?;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get_string("session_type")?.as_deref(), Some("group"));
    assert_eq!(rows[0].get("current_msg_seq"), Some(&DbValue::Null));
    let group = db.query(DbStatement::new("SELECT current_msg_seq FROM bcs_group_sessions WHERE env = 'dev' AND session_id = 'shared'")).await?;
    assert_eq!(db_get_column::<i64>(&group[0], "current_msg_seq")?, 42);
    assert!(db.execute(DbStatement::new("INSERT INTO bcs_session_registry (env, session_id, session_type, current_msg_seq) VALUES ('dev', 'shared', 'direct_a2a', 0)")).await.is_err());
    db.execute(DbStatement::new("INSERT INTO bcs_session_registry (env, session_id, session_type, current_msg_seq) VALUES ('test', 'shared', 'direct_a2a', 0)")).await?;
    for (env, id) in [("dev", "message-dev"), ("test", "message-test")] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_messages (env, message_id, group_id, session_id, session_seq, sender_id, sender_type, message_type, content, created_at) VALUES (?, ?, '', 'shared', 1, 'sender', 'bot', 'chat', '{}', 1)", vec![env.into(), id.into()])).await?;
    }
    assert!(db.execute(DbStatement::new("INSERT INTO bcs_messages (env, message_id, group_id, session_id, session_seq, sender_id, sender_type, message_type, content, created_at) VALUES ('dev', 'duplicate', '', 'shared', 1, 'sender', 'bot', 'chat', '{}', 1)")).await.is_err());
    assert!(column_names(&db, "bcs_chat_runs").await?.contains(&"source_message_id".to_string()));
    Ok(())
}
