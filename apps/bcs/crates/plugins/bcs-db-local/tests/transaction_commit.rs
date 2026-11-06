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
async fn query_checked_zero_rows_stops_and_commits_earlier_writes() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("CREATE TABLE t (k INTEGER PRIMARY KEY, v TEXT)")).await?;
    let results = db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO t VALUES (1, 'before')")),
        DbTransactionStep::QueryChecked {
            statement: DbStatement::new("SELECT * FROM t WHERE k = 99"),
            expected_min_rows: 1,
        },
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO t VALUES (2, 'after')")),
    ]).await?;
    // Earlier write persisted, later step skipped.
    assert_eq!(results.len(), 2, "only first two steps should execute");
    let rows = db.query(DbStatement::new("SELECT v FROM t ORDER BY k")).await?;
    assert_eq!(rows.len(), 1);
    Ok(())
}

#[tokio::test]
async fn query_checked_enough_rows_continues_to_later_steps() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("CREATE TABLE t (k INTEGER PRIMARY KEY, v TEXT)")).await?;
    db.execute(DbStatement::new("INSERT INTO t VALUES (1, 'row')")).await?;
    let results = db.transaction(vec![
        DbTransactionStep::QueryChecked {
            statement: DbStatement::new("SELECT * FROM t WHERE k = 1"),
            expected_min_rows: 1,
        },
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO t VALUES (2, 'later')")),
    ]).await?;
    assert_eq!(results.len(), 2, "both steps should execute");
    let rows = db.query(DbStatement::new("SELECT k FROM t ORDER BY k")).await?;
    assert_eq!(rows.len(), 2);
    Ok(())
}

#[tokio::test]
async fn query_checked_with_expected_min_rows_zero_always_continues() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("CREATE TABLE t (k INTEGER PRIMARY KEY)")).await?;
    let results = db.transaction(vec![
        DbTransactionStep::QueryChecked {
            statement: DbStatement::new("SELECT * FROM t"),
            expected_min_rows: 0,
        },
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO t VALUES (1)")),
    ]).await?;
    assert_eq!(results.len(), 2);
    let rows = db.query(DbStatement::new("SELECT k FROM t")).await?;
    assert_eq!(rows.len(), 1);
    Ok(())
}

#[tokio::test]
async fn query_checked_stop_skips_later_steps() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    db.execute(DbStatement::new("CREATE TABLE t (k INTEGER PRIMARY KEY, v TEXT)")).await?;
    db.execute(DbStatement::new("INSERT INTO t VALUES (1, 'alpha')")).await?;
    // Query returns 2 rows, expected_min_rows=5 → stops.
    // The later INSERT step is skipped entirely because the transaction
    // breaks after QueryChecked, regardless of whether later steps have bindings.
    let results = db.transaction(vec![
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO t VALUES (2, 'before')")),
        DbTransactionStep::QueryChecked {
            statement: DbStatement::new("SELECT * FROM t"),
            expected_min_rows: 5,
        },
        DbTransactionStep::Execute(DbStatement::new("INSERT INTO t VALUES (3, 'must-not-execute')")),
    ]).await?;
    assert_eq!(results.len(), 2, "third step must not execute");
    assert!(db.query(DbStatement::new("SELECT k FROM t WHERE k = 3")).await?.is_empty());
    Ok(())
}
