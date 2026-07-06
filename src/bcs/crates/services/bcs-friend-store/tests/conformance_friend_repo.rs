use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_friend_store::{
    DbFriendRequestStore, DbFriendStore, MemoryFriendRepo, MemoryFriendRequestRepo,
};
use std::sync::Arc;

#[tokio::test]
async fn memory_friend_repo_passes_friend_repo_port_contract() {
    let repo = MemoryFriendRepo::new();

    bcs_test_support::contract::repo::friend_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn memory_friend_request_repo_passes_friend_request_repo_port_contract() {
    let repo = MemoryFriendRequestRepo::new();

    bcs_test_support::contract::repo::friend_request_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn db_friend_store_passes_friend_repo_contract() {
    let db = sqlite_db().await;
    let repo = DbFriendStore::sqlite(db);

    bcs_test_support::contract::repo::friend_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn db_friend_request_store_passes_friend_request_repo_contract() {
    let db = sqlite_db().await;
    let repo = DbFriendRequestStore::sqlite(db);

    bcs_test_support::contract::repo::friend_request_repo_port_contract_tests(&repo).await;
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friendships (
            left_bot TEXT NOT NULL,
            right_bot TEXT NOT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (left_bot, right_bot, env)
        )",
    ))
    .await
    .expect("create friendships table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friend_requests (
            request_id TEXT PRIMARY KEY,
            from_bot TEXT NOT NULL,
            to_bot TEXT NOT NULL,
            status TEXT NOT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )",
    ))
    .await
    .expect("create friend requests table");
    db
}
