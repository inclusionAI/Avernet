//! Regression coverage for SQLite migration 31
//! (`group_human_mention_notify_mode`).
//!
//! The fresh-runner path must prove the baseline does NOT already contain the
//! column; otherwise a duplicate `ADD COLUMN` in version 31 would hide behind
//! the fresh path.

use super::*;

#[tokio::test]
async fn fresh_sqlite_schema_contains_group_human_mention_notify_mode() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    // Baseline proof: bootstrap tables alone must not carry the column, so a
    // duplicate column between baseline and version 31 cannot hide.
    run_sqlite_bootstrap_tables(&db).await?;
    let baseline_columns = column_names(&db, "bcs_groups").await?;
    assert!(
        !baseline_columns
            .iter()
            .any(|column| column == "human_mention_notify_mode"),
        "the SQLite baseline must not pre-create the column"
    );

    run_sqlite_migrations(&db).await?;
    let columns = column_names(&db, "bcs_groups").await?;
    assert!(
        columns
            .iter()
            .any(|column| column == "human_mention_notify_mode")
    );
    Ok(())
}

#[tokio::test]
async fn sqlite_version_30_upgrade_adds_default_all_and_is_repeatable() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 30)
    {
        apply_sqlite_migration(&db, migration).await?;
    }
    run_sqlite_bootstrap_indexes(&db).await?;
    assert!(
        !column_names(&db, "bcs_groups")
            .await?
            .iter()
            .any(|name| name == "human_mention_notify_mode")
    );
    db.execute(DbStatement::new(
        "INSERT INTO bcs_groups (group_id, status, driver_bot, env) VALUES ('notify-upgrade', 'active', 'driver', 'test')",
    ))
    .await?;

    let history_sql = "SELECT version, name, dialect, checksum, applied_at FROM bcs_schema_migrations ORDER BY version";
    let old_history = db.query(DbStatement::new(history_sql)).await?;
    run_sqlite_migrations(&db).await?;
    let rows = db
        .query(DbStatement::new(
            "SELECT human_mention_notify_mode FROM bcs_groups WHERE group_id = 'notify-upgrade'",
        ))
        .await?;
    assert_eq!(
        db_get_column::<String>(&rows[0], "human_mention_notify_mode")?,
        "all"
    );
    let history = db.query(DbStatement::new(history_sql)).await?;
    assert_eq!(&history[..30], old_history.as_slice());
    assert_eq!(history.len(), 31);
    let record = applied_sqlite_migration(&db, 31)
        .await?
        .expect("version 31 must be recorded");
    assert_eq!(record.name, "group_human_mention_notify_mode");
    assert_eq!(current_sqlite_version(&db, true).await?, Some(31));
    let columns = column_names(&db, "bcs_groups").await?;
    assert!(
        columns
            .iter()
            .any(|column| column == "human_mention_notify_mode")
    );

    // A second runner invocation must be a no-op: new checksums stay valid and
    // no additional history rows appear.
    run_sqlite_migrations(&db).await?;
    assert_eq!(current_sqlite_version(&db, true).await?, Some(31));
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, history);
    Ok(())
}
