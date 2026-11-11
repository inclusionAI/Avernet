use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn friend_cutover_does_not_import_legacy_requests_or_change_history() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m| m.version <= 43) {
        apply_sqlite_migration(&db, migration).await?;
    }
    db.execute(DbStatement::new(
        "INSERT INTO bcs_friend_requests (request_id, from_bot, to_bot, status, env) VALUES \
         ('old-pending', 'botA', 'botB', 'pending', 'dev'), \
         ('old-approved', 'botA', 'botB', 'accepted', 'dev'), \
         ('old-rejected', 'botA', 'botB', 'rejected', 'pre')"
    )).await?;
    db.execute(DbStatement::new(
        "INSERT INTO permission_requests (request_id, env, from_id, to_id, request_kind, status, created_by) \
         VALUES ('new-request', 'dev', 'botA', 'botB', 'connect', 'pending', 'botA')"
    )).await?;
    let history_sql = "SELECT version, name, checksum FROM bcs_schema_migrations ORDER BY version";
    let history = db.query(DbStatement::new(history_sql)).await?;
    for _ in 0..2 {
        run_sqlite_migrations(&db).await?;
        let requests = db.query(DbStatement::new(
            "SELECT request_id, status FROM permission_requests ORDER BY request_id"
        )).await?;
        assert_eq!(requests.len(), 1, "cutover must not import any legacy request");
        assert_eq!(requests[0].get_string("request_id")?.as_deref(), Some("new-request"));
        assert_eq!(requests[0].get_string("status")?.as_deref(), Some("pending"));
        let retained = db.query(DbStatement::new(history_sql)).await?;
        assert_eq!(retained.len(), history.len(), "cutover adds no migration");
        for (before, after) in history.iter().zip(&retained) {
            for column in ["name", "checksum"] {
                assert_eq!(before.get_string(column)?, after.get_string(column)?);
            }
            assert_eq!(before.get_i64("version")?, after.get_i64("version")?);
        }
        let old = db.query(DbStatement::new(
            "SELECT request_id, status FROM bcs_friend_requests ORDER BY request_id"
        )).await?;
        assert_eq!(old.len(), 3);
        for (row, status) in old.iter().zip(["accepted", "pending", "rejected"]) {
            assert_eq!(row.get_string("status")?.as_deref(), Some(status));
        }
    }
    Ok(())
}
