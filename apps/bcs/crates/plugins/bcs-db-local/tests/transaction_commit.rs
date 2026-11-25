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

#[tokio::test]
#[allow(clippy::unwrap_used, reason = "native transaction failure assertions")]
async fn detailed_commit_failure_is_conservatively_unknown() -> DbResult<()> {
    use bcs_db_api::{DbTransactionOptions, DbTransactionOutcome};
    let db=LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("PRAGMA foreign_keys=ON")).await?;
    db.execute(DbStatement::new("CREATE TABLE parent (id INTEGER PRIMARY KEY)")).await?;
    db.execute(DbStatement::new("CREATE TABLE child (id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")).await?;
    let failed=db.transaction_with_outcome(vec![DbTransactionStep::Execute(
        DbStatement::new("INSERT INTO child VALUES (1)"))],DbTransactionOptions::unbounded()).await.unwrap_err();
    assert_eq!(failed.outcome(),DbTransactionOutcome::CommitUnknown);
    assert!(matches!(failed.error(),DbError::Backend(message) if message.starts_with("commit sqlite transaction:")));
    assert!(db.query(DbStatement::new("SELECT id FROM child")).await?.is_empty());
    Ok(())
}

#[tokio::test]
#[allow(clippy::unwrap_used, reason = "native transaction failure assertions")]
async fn detailed_unconfirmed_explicit_rollback_is_not_reported_definite() -> DbResult<()> {
    use bcs_db_api::{DbTransactionOptions, DbTransactionOutcome};
    let db=LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("CREATE TABLE items (id INTEGER)")).await?;
    // RAISE(ROLLBACK) ends the backend transaction. A subsequent explicit
    // rollback cannot be acknowledged; the executor must not invent certainty
    // from the statement's error text, even though this fixture can inspect it.
    db.execute(DbStatement::new("CREATE TRIGGER rollback_items BEFORE INSERT ON items WHEN NEW.id=2 BEGIN SELECT RAISE(ROLLBACK,'fixture rollback'); END")).await?;
    let failed=db.transaction_with_outcome(vec![
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO items VALUES (1)")),
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO items VALUES (2)")),
    ],DbTransactionOptions::unbounded()).await.unwrap_err();
    assert_eq!(failed.outcome(),DbTransactionOutcome::CommitUnknown);
    assert!(db.query(DbStatement::new("SELECT id FROM items")).await?.is_empty());
    Ok(())
}
