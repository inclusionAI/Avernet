use super::*;
use bcs_db_local::LocalSqliteDbPlugin;
async fn previous(db: &dyn DbPlugin) -> DbResult<()> {
    run_sqlite_bootstrap_tables(db).await?;
    for m in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|m| m.version <= 49)
    {
        apply_sqlite_migration(db, m).await?;
    }
    Ok(())
}
#[tokio::test]
async fn query_indexes_preserve_existing_rows_and_frozen_history() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    previous(&db).await?;
    db.execute(DbStatement::new("INSERT INTO permission_requests (request_id,env,from_id,to_id,request_kind,status,created_by) VALUES ('retained-query','dev','a','b','connect','pending','human_a')")).await?;
    let history = db
        .query(DbStatement::new(
            "SELECT * FROM bcs_schema_migrations ORDER BY version",
        ))
        .await?;
    let data = db
        .query(DbStatement::new("SELECT * FROM permission_requests"))
        .await?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(
        db.query(DbStatement::new(
            "SELECT * FROM bcs_schema_migrations WHERE version<=49 ORDER BY version"
        ))
        .await?,
        history
    );
    assert_eq!(
        db.query(DbStatement::new("SELECT * FROM permission_requests"))
            .await?,
        data
    );
    for index in [
        "idx_agent_invoke_request_pair",
        "idx_agent_invoke_audit_time",
        "idx_agent_invoke_audit_action_time",
    ] {
        assert_eq!(
            db.query(DbStatement::with_params(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                vec![index.into()]
            ))
            .await?
            .len(),
            1
        );
    }
    Ok(())
}
#[tokio::test]
async fn query_index_ddl_and_history_record_roll_back_together() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    previous(&db).await?;
    db.execute(DbStatement::new("CREATE TRIGGER fail_query_history BEFORE INSERT ON bcs_schema_migrations WHEN NEW.version=50 BEGIN SELECT RAISE(ABORT,'query history fault'); END")).await?;
    assert!(run_sqlite_migrations(&db).await.is_err());
    assert!(
        db.query(DbStatement::new(
            "SELECT name FROM sqlite_master WHERE name='idx_agent_invoke_request_pair'"
        ))
        .await?
        .is_empty()
    );
    assert!(applied_sqlite_migration(&db, 50).await?.is_none());
    db.execute(DbStatement::new("DROP TRIGGER fail_query_history"))
        .await?;
    run_sqlite_migrations(&db).await?;
    Ok(())
}
#[test]
fn query_index_dialects_share_columns_and_stay_under_mysql_key_budget() {
    let sqlite =
        include_str!("../../../../../migrations/sqlite/050_agent_invoke_governance_queries.sql");
    let mysql =
        include_str!("../../../../../migrations/mysql/052_agent_invoke_governance_queries.sql");
    assert_eq!(sqlite, mysql);
    assert!(mysql.contains("(env, from_id, to_id, request_kind, status, id)"));
    assert!(mysql.contains("(scope_id, from_id, to_id, action, created_at_ms, id)"));
    assert!(64 * 4 + 2 * 256 * 4 + 32 * 4 + 16 * 4 + 8 <= 3072);
    assert!(8 + 2 * 256 * 4 + 32 * 4 + 8 + 8 <= 3072);
}
