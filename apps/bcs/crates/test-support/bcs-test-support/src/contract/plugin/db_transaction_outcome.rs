//! Native outcome capability conformance shared by bundled SQL plugins.
#![allow(
    clippy::unwrap_used,
    reason = "shared conformance assertions must fail the test on unexpected outcomes"
)]
use bcs_db_api::*;
use std::time::Duration;

fn execute(sql: &str) -> DbTransactionStep {
    DbTransactionStep::Execute(DbStatement::new(sql))
}
fn insert(id: &str) -> DbTransactionStep {
    DbTransactionStep::Execute(DbStatement::with_params(
        "INSERT INTO contract_outcome_items (id,value) VALUES (?,1)",
        vec![id.into()],
    ))
}
async fn empty<P: DbPlugin>(plugin: &P, id: &str) {
    assert!(
        plugin
            .query(DbStatement::with_params(
                "SELECT id FROM contract_outcome_items WHERE id=?",
                vec![id.into()]
            ))
            .await
            .unwrap()
            .is_empty()
    );
}
pub async fn db_transaction_outcome_contract_tests<P: DbPlugin>(plugin: &P) {
    plugin.execute(DbStatement::new("CREATE TABLE IF NOT EXISTS contract_outcome_items (id VARCHAR(64) PRIMARY KEY,value INTEGER NOT NULL)")).await.unwrap();
    plugin
        .execute(DbStatement::new("DELETE FROM contract_outcome_items"))
        .await
        .unwrap();
    let options = DbTransactionOptions::with_deadline(Duration::from_secs(30));
    assert!(
        plugin
            .transaction_with_outcome(vec![], options.clone())
            .await
            .unwrap()
            .is_empty()
    );
    let success = plugin
        .transaction_with_outcome(
            vec![
                insert("success"),
                DbTransactionStep::Query(DbStatement::new(
                    "SELECT id FROM contract_outcome_items WHERE id='success'",
                )),
                DbTransactionStep::ExecuteChecked {
                    statement: DbStatement::with_transaction_params(
                        "UPDATE contract_outcome_items SET value=2 WHERE id=?",
                        vec![DbTransactionParam::query_result(1, 0, "id")],
                    ),
                    expected_affected_rows: 1,
                },
            ],
            options.clone(),
        )
        .await
        .unwrap();
    assert_eq!(success.len(), 3);
    assert_eq!(
        plugin
            .query(DbStatement::new(
                "SELECT value FROM contract_outcome_items WHERE id='success'"
            ))
            .await
            .unwrap()[0]
            .get_i64("value")
            .unwrap(),
        Some(2)
    );

    // Each case must roll back a real earlier DML write, not just report an
    // enum on a transaction that never had effects to undo.
    for (id, step) in [
        (
            "sql-error",
            execute("INSERT INTO missing_outcome_contract_table VALUES (1)"),
        ),
        ("duplicate", insert("success")),
        (
            "condition",
            DbTransactionStep::ExecuteChecked {
                statement: DbStatement::new(
                    "UPDATE contract_outcome_items SET value=3 WHERE id='missing'",
                ),
                expected_affected_rows: 1,
            },
        ),
        (
            "binding",
            DbTransactionStep::Execute(DbStatement::with_transaction_params(
                "UPDATE contract_outcome_items SET value=?",
                vec![DbTransactionParam::query_result(99, 0, "missing")],
            )),
        ),
    ] {
        let failed = plugin
            .transaction_with_outcome(vec![insert(id), step], options.clone())
            .await
            .unwrap_err();
        assert_eq!(
            failed.outcome(),
            DbTransactionOutcome::NotCommitted,
            "{id}: {failed:?}"
        );
        empty(plugin, id).await;
    }
    let timeout = plugin
        .transaction_with_outcome(
            vec![insert("deadline")],
            DbTransactionOptions::with_deadline(Duration::ZERO),
        )
        .await
        .unwrap_err();
    assert_eq!(timeout.outcome(), DbTransactionOutcome::NotCommitted);
    assert!(matches!(timeout.error(), DbError::Timeout));
    empty(plugin, "deadline").await;

    let early = plugin
        .transaction_with_outcome(
            vec![
                insert("early"),
                DbTransactionStep::Execute(
                    DbStatement::new(
                        "UPDATE contract_outcome_items SET value=3 WHERE id='missing'",
                    )
                    .with_transaction_stop_on_no_rows(),
                ),
                execute("INSERT INTO missing_outcome_contract_table VALUES (1)"),
            ],
            options.clone(),
        )
        .await
        .unwrap();
    assert_eq!(early.len(), 2, "early success still commits its prefix");
    assert_eq!(
        plugin
            .query(DbStatement::new(
                "SELECT id FROM contract_outcome_items WHERE id='early'"
            ))
            .await
            .unwrap()
            .len(),
        1
    );
    // A forbidden command anywhere is rejected before even the prefix runs.
    for sql in [
        "CREATE TABLE forbidden_outcome (id INT)",
        "COMMIT",
        "SELECT 1; COMMIT",
        "SELECT 1 /*! COMMIT */",
    ] {
        let failed = plugin
            .transaction_with_outcome(
                vec![insert("invalid-prefix"), execute(sql)],
                options.clone(),
            )
            .await
            .unwrap_err();
        assert_eq!(failed.outcome(), DbTransactionOutcome::NotCommitted);
        assert!(matches!(failed.error(), DbError::Unsupported(_)));
        empty(plugin, "invalid-prefix").await;
    }
}
