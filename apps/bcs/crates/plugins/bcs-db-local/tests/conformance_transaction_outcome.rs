#![allow(
    clippy::unwrap_used,
    reason = "conformance fixture setup and assertions"
)]
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_test_support::contract::plugin::db_transaction_outcome::db_transaction_outcome_contract_tests;

#[tokio::test]
async fn conformance_native_sqlite_transaction_outcome() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    bcs_test_support::contract::plugin::db_plugin_contract_tests(&db).await;
    db_transaction_outcome_contract_tests(&db).await;
}

#[tokio::test]
async fn conformance_native_file_sqlite_transaction_outcome() {
    let dir = tempfile::tempdir().unwrap();
    let db = LocalSqliteDbPlugin::new_file(dir.path().join("outcome.db")).unwrap();
    bcs_test_support::contract::plugin::db_plugin_contract_tests(&db).await;
    db_transaction_outcome_contract_tests(&db).await;
}
