//! Regression coverage for the additive D2 activity-covering indexes
//! (`sort=last_activity` cost gate, design D2 ladder (a)).
use super::*;

async fn index_columns(db: &dyn DbPlugin, index: &str) -> DbResult<Vec<String>> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
            vec![DbValue::from(index)],
        ))
        .await?;
    rows.into_iter()
        .map(|row| db_get_column(&row, "name"))
        .collect()
}

#[tokio::test]
async fn sqlite_message_activity_indexes_cover_env_and_activity_key() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;

    assert!(index_exists(&db, "idx_messages_env_group_created").await?);
    assert!(index_exists(&db, "idx_messages_env_session_created").await?);
    assert_eq!(
        index_columns(&db, "idx_messages_env_group_created").await?,
        vec!["env", "group_id", "created_at"]
    );
    assert_eq!(
        index_columns(&db, "idx_messages_env_session_created").await?,
        vec!["env", "session_id", "created_at"]
    );
    // The pre-existing per-key indexes the aggregates were planned against
    // stay in place: this migration is purely additive.
    assert!(index_exists(&db, "idx_messages_group_created").await?);
    assert!(index_exists(&db, "idx_messages_session_created").await?);

    let migration = SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .find(|migration| migration.version == 45)
        .expect("message_activity_indexes migration listed");
    assert_eq!(migration.name, "message_activity_indexes");
    let record = applied_sqlite_migration(&db, 45)
        .await?
        .expect("activity index migration recorded");
    assert_eq!(record.name, "message_activity_indexes");
    assert_eq!(record.dialect, "sqlite");
    assert_eq!(record.checksum, sqlite_migration_checksum(migration));

    // Repeated startup must not re-apply or rewrite the history.
    let history = db
        .query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version",
        ))
        .await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(
        db.query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version"
        ))
        .await?,
        history
    );
    Ok(())
}

#[test]
fn mysql_message_activity_indexes_are_additive_and_env_leading() {
    let migration =
        include_str!("../../../../../../migrations/mysql/047_message_activity_indexes.sql");
    for key in [
        "`idx_messages_env_group_created` (`env`, `group_id`, `created_at`)",
        "`idx_messages_env_session_created` (`env`, `session_id`, `created_at`)",
    ] {
        assert!(migration.contains(key), "missing index definition {key}");
    }
    assert!(!migration.to_ascii_uppercase().contains("DROP "));
    // Frozen baseline stays untouched: the new keys exist only in this
    // additive migration.
    let baseline = include_str!("../../../../../../migrations/mysql/001_init_schema.sql");
    assert!(!baseline.contains("idx_messages_env_group_created"));
    assert!(!baseline.contains("idx_messages_env_session_created"));
}