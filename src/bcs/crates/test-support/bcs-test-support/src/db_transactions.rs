//! Shared early-success transaction contract, exercised by SQLite and MySQL.

use bcs_db_api::{DbError, DbPlugin, DbStatement, DbTransactionParam, DbTransactionStep, DbTransactionStepResult};

fn write(sql: &str) -> DbTransactionStep {
    DbTransactionStep::Execute(DbStatement::new(sql))
}

fn stop(sql: &str) -> DbTransactionStep {
    DbTransactionStep::Execute(DbStatement::new(sql).with_transaction_stop_on_no_rows())
}

#[allow(clippy::expect_used, reason = "shared conformance assertions")]
pub(super) async fn stop_on_no_rows_contract<P: DbPlugin>(plugin: &P) {
    let empty = plugin.transaction(vec![
        stop("UPDATE contract_items SET active = active + 1 WHERE id = 'missing-stop'"),
        // This must never reach the driver or resolve its invalid binding.
        DbTransactionStep::Execute(DbStatement::with_transaction_params(
            "INSERT INTO missing_contract_table VALUES (?)",
            vec![DbTransactionParam::query_result(99, 0, "missing")],
        )),
    ]).await.expect("zero-row Execute ends successfully");
    assert_eq!(empty.len(), 1);
    assert!(matches!(&empty[0], DbTransactionStepResult::Executed(result) if result.affected_rows == 0));

    let prefix = plugin.transaction(vec![
        write("INSERT INTO contract_items (id, name, active) VALUES ('stop-prefix', 'before', 1)"),
        stop("UPDATE contract_items SET active = active + 1 WHERE id = 'missing-stop'"),
        write("INSERT INTO contract_items (id, name, active) VALUES ('stop-skipped', 'after', 1)"),
        DbTransactionStep::Query(DbStatement::new("SELECT * FROM missing_contract_table")),
    ]).await.expect("commit executed prefix and skip subsequent SQL");
    assert_eq!(prefix.len(), 2);
    let rows = plugin.query(DbStatement::new(
        "SELECT id FROM contract_items WHERE id IN ('stop-prefix', 'stop-skipped')",
    )).await.expect("read committed prefix");
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get_string("id").expect("id").as_deref(), Some("stop-prefix"));

    let continued = plugin.transaction(vec![
        DbTransactionStep::Query(DbStatement::new("SELECT id FROM contract_items WHERE id = 'stop-prefix'")),
        DbTransactionStep::Execute(DbStatement::with_transaction_params(
            "UPDATE contract_items SET active = active + 1 WHERE id = ?",
            vec![DbTransactionParam::query_result(0, 0, "id")],
        ).with_transaction_stop_on_no_rows()),
        DbTransactionStep::Query(DbStatement::new("SELECT active FROM contract_items WHERE id = 'stop-prefix'")),
    ]).await.expect("non-empty Execute continues, including result bindings");
    assert_eq!(continued.len(), 3);
    assert!(matches!(&continued[2], DbTransactionStepResult::Rows(rows)
        if rows[0].get_i64("active").expect("active") == Some(2)));

    let ordinary = plugin.transaction(vec![
        write("UPDATE contract_items SET active = active + 1 WHERE id = 'missing-stop'"),
        DbTransactionStep::Query(DbStatement::new("SELECT id FROM contract_items WHERE id = 'missing-stop'")),
        write("UPDATE contract_items SET active = active + 1 WHERE id = 'stop-prefix'"),
    ]).await.expect("ordinary empty Execute and Query still continue");
    assert_eq!(ordinary.len(), 3);

    for steps in [
        vec![
            stop("UPDATE contract_items SET active = active + 1 WHERE id = 'stop-prefix'"),
            write("INSERT INTO contract_items (id, name, active) VALUES ('stop-prefix', 'duplicate', 1)"),
        ],
        vec![
            write("UPDATE contract_items SET active = active + 1 WHERE id = 'stop-prefix'"),
            stop("INSERT INTO contract_items (id, name, active) VALUES ('stop-prefix', 'duplicate', 1)"),
        ],
    ] {
        assert!(plugin.transaction(steps).await.is_err(), "SQL errors must not become early success");
        let rows = plugin.query(DbStatement::new(
            "SELECT active FROM contract_items WHERE id = 'stop-prefix'",
        )).await.expect("read after rollback");
        assert_eq!(rows[0].get_i64("active").expect("active"), Some(3), "all earlier writes roll back");
    }

    let flagged = || DbStatement::new("UPDATE contract_items SET active = active + 1 WHERE id = 'stop-prefix'")
        .with_transaction_stop_on_no_rows();
    assert!(matches!(plugin.execute(flagged()).await, Err(DbError::InvalidInput(_))));
    assert!(matches!(plugin.query(flagged()).await, Err(DbError::InvalidInput(_))));
    for invalid in [
        DbTransactionStep::Query(flagged()),
        DbTransactionStep::ExecuteChecked { statement: flagged(), expected_affected_rows: 1 },
    ] {
        assert!(matches!(plugin.transaction(vec![invalid]).await, Err(DbError::InvalidInput(_))));
    }
}
