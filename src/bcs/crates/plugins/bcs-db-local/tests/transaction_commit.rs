use bcs_db_api::{DbError, DbPlugin, DbResult, DbStatement, DbTransactionStep};
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn early_success_propagates_commit_failure_and_rolls_back() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("PRAGMA foreign_keys = ON")).await?;
    db.execute(DbStatement::new("CREATE TABLE parent (id INTEGER PRIMARY KEY)")).await?;
    db.execute(DbStatement::new(
        "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)",
    )).await?;
    let result = db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO child VALUES (1)")),
        DbTransactionStep::Execute(DbStatement::new(
            "UPDATE parent SET id = id + 1 WHERE id = 99",
        ).with_transaction_stop_on_no_rows()),
    ]).await;
    assert!(matches!(result, Err(DbError::Backend(message)) if message.starts_with("commit sqlite transaction:")),
        "the deferred foreign key fails at commit, not at INSERT");
    assert!(db.query(DbStatement::new("SELECT parent_id FROM child")).await?.is_empty());
    Ok(())
}
