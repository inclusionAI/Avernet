use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn legacy_friend_request_upgrade_preserves_ids_and_timestamps() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m| m.version <= 43) {
        apply_sqlite_migration(&db, migration).await?;
    }
    db.execute(DbStatement::new(
        "INSERT INTO bcs_friend_requests (request_id, from_bot, to_bot, status, env, gmt_create, gmt_modified) \
         VALUES ('old-pending-id', 'botA', 'botB', 'pending', 'dev', '2026-11-01 00:00:00', '2026-11-02 00:00:00')"
    )).await?;
    run_sqlite_migrations(&db).await?;
    let rows = db.query(DbStatement::new("SELECT request_id, env, status, from_id, to_id, gmt_create, gmt_modified FROM permission_requests WHERE request_id = 'old-pending-id'")).await?;
    assert_eq!(rows.len(), 1, "old pending request must remain addressable after cutover");
    assert_eq!(rows[0].get_string("status")?.as_deref(), Some("pending"));
    assert_eq!(rows[0].get_string("gmt_create")?.as_deref(), Some("2026-11-01 00:00:00"));
    assert_eq!(rows[0].get_string("gmt_modified")?.as_deref(), Some("2026-11-02 00:00:00"));
    Ok(())
}

async fn pre_cutover_db() -> DbResult<LocalSqliteDbPlugin> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m| m.version <= 43) {
        apply_sqlite_migration(&db, migration).await?;
    }
    Ok(db)
}

#[tokio::test]
async fn cutover_preserves_history_env_and_newer_decisions_on_rerun() -> DbResult<()> {
    let db = pre_cutover_db().await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_friend_requests (request_id, from_bot, to_bot, status, env) VALUES \
         ('old-approved', 'botA', 'botB', 'accepted', 'dev'), \
         ('old-rejected', 'botA', 'botB', 'rejected', 'dev'), \
         ('old-pending', 'botA', 'botB', 'pending', 'pre')"
    )).await?;
    assert!(ensure_friend_request_cutover(&db).await.is_err());
    let history = db.query(DbStatement::new("SELECT version, checksum FROM bcs_schema_migrations ORDER BY version")).await?;
    run_sqlite_migrations(&db).await?;
    ensure_friend_request_cutover(&db).await?;
    let rows = db.query(DbStatement::new(
        "SELECT request_id, status, env, from_id, to_id, created_by, request_kind FROM permission_requests ORDER BY request_id"
    )).await?;
    assert_eq!(rows.len(), 3);
    for (row, (id, status, env)) in rows.iter().zip([
        ("old-approved", "approved", "dev"), ("old-pending", "pending", "pre"), ("old-rejected", "rejected", "dev"),
    ]) {
        for (column, expected) in [("request_id", id), ("status", status), ("env", env),
            ("from_id", "botA"), ("to_id", "botB"), ("created_by", "botA"), ("request_kind", "connect")] {
            assert_eq!(row.get_string(column)?.as_deref(), Some(expected));
        }
    }
    db.execute(DbStatement::new("UPDATE permission_requests SET status = 'rejected' WHERE request_id = 'old-pending'")).await?;
    // Re-execute the body as well as the normal versioned runner: neither may
    // overwrite a decision made in the new system or create duplicate IDs.
    db.execute(DbStatement::new(include_str!("../../../../../migrations/sqlite/044_legacy_friend_request_cutover.sql"))).await?;
    run_sqlite_migrations(&db).await?;
    ensure_friend_request_cutover(&db).await?;
    let decided = db.query(DbStatement::new("SELECT status FROM permission_requests WHERE request_id = 'old-pending'")).await?;
    assert_eq!(decided[0].get_string("status")?.as_deref(), Some("rejected"));
    let counts = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM permission_requests")).await?;
    assert_eq!(counts[0].get_i64("n")?, Some(3));
    let retained = db.query(DbStatement::new("SELECT version, checksum FROM bcs_schema_migrations WHERE version <= 43 ORDER BY version")).await?;
    assert_eq!(history.len(), retained.len());
    for (before, after) in history.iter().zip(retained.iter()) {
        assert_eq!(before.get_string("checksum")?, after.get_string("checksum")?);
    }
    Ok(())
}

#[tokio::test]
async fn cutover_guard_rejects_collision_late_legacy_writes_and_db_errors() -> DbResult<()> {
    let db = pre_cutover_db().await?;
    run_sqlite_migrations(&db).await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_friend_requests (request_id, from_bot, to_bot, env) VALUES ('collision', 'botA', 'botB', 'dev')"
    )).await?;
    // An old writer after the migration must not silently strand its request.
    assert!(ensure_friend_request_cutover(&db).await.is_err());
    db.execute(DbStatement::new(
        "INSERT INTO permission_requests (request_id, env, from_id, to_id, request_kind, status, created_by) \
         VALUES ('collision', 'dev', 'botA', 'botB', 'rules', 'pending', 'botA')"
    )).await?;
    assert!(ensure_friend_request_cutover(&db).await.is_err());
    db.execute(DbStatement::new("UPDATE permission_requests SET request_kind = 'connect', env = 'pre'")).await?;
    assert!(ensure_friend_request_cutover(&db).await.is_err());
    db.execute(DbStatement::new("UPDATE permission_requests SET env = 'dev'")).await?;
    ensure_friend_request_cutover(&db).await?;
    db.execute(DbStatement::new("DROP TABLE permission_requests")).await?;
    assert!(ensure_friend_request_cutover(&db).await.is_err());
    Ok(())
}
