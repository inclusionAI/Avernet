//! Run explicitly against a disposable, EMPTY MySQL database. The same shared
//! reader-first harness runs in the default SQLite and memory conformance suites.
//! This does not exercise future Agent-authorization management transactions.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_edge_permission_store::DbEdgeGrantStore;
use bcs_test_support::contract::repo::edge_grant::run_agent_invoke_repo_contract;

#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable EMPTY MySQL database"]
async fn agent_invoke_repo_contract_mysql() {
    let database = mysql_chain::MysqlConformanceDb::open_empty().await;
    database.apply_full_migration_chain().await;
    let store = DbEdgeGrantStore::mysql(database.plugin.clone());
    run_agent_invoke_repo_contract(&store, "agent-test", "agent-caller", "agent-target").await;
    database.close().await;
}
