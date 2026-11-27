//! Public SQLite provider conformance for the node-isolated interaction
//! history repo (plan T3). The store is the real DbPlugin-backed
//! implementation; migrations run first so the tables exist in a fresh file.
#![allow(clippy::unwrap_used, reason = "contract assertion")]
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_dist_store::node_history::DbNodeHistoryStore;
use bcs_test_support::contract::distributed::node_history::{
    node_history_repo_contract_tests, same_bot_two_nodes_remain_private as same_bot_vector,
    trim_during_takeover_replays_exact_input as trim_vector,
};
use std::sync::Arc;

async fn store() -> (tempfile::TempDir, DbNodeHistoryStore) {
    let directory = tempfile::tempdir().unwrap();
    let db = LocalSqliteDbPlugin::new_file(directory.path().join("node-history.db")).unwrap();
    bcs_db_migrations::run_sqlite_migrations(&db).await.unwrap();
    let store = DbNodeHistoryStore::new(Arc::new(db)).unwrap();
    (directory, store)
}

#[tokio::test]
async fn conformance_node_history_repo() {
    let (_directory, store) = store().await;
    node_history_repo_contract_tests(&store).await.unwrap();
}

#[tokio::test]
async fn conformance_same_bot_two_nodes_remain_private() {
    let (_directory, store) = store().await;
    same_bot_vector(&store).await.unwrap();
}

#[tokio::test]
async fn conformance_trim_during_takeover_replays_exact_input() {
    let (_directory, store) = store().await;
    trim_vector(&store).await.unwrap();
}
