//! Rule 25 conformance drivers for the in-memory edge-permission stores.
//!
//! Same reusable contract functions the SQLite/MySQL drivers run, so the
//! in-memory bootstrap path is held to the same repo-port behavior as the
//! durable stores.

use std::sync::Arc;

use bcs_edge_permission_store::memory::{
    MemoryEdgeGrantStore, MemoryPermissionProfileStore, MemoryPermissionRequestStore,
};
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_test_support::contract::repo::{
    run_edge_grant_repo_contract, run_permission_profile_repo_contract,
    run_permission_request_repo_contract,
};

const ENV: &str = "test";

#[tokio::test]
async fn memory_edge_grant_repo_contract() {
    let profiles = Arc::new(MemoryPermissionProfileStore::new());
    profiles
        .ensure_default_profile("bot_target:001", ENV)
        .await
        .expect("seed default profile");
    let store = MemoryEdgeGrantStore::new(profiles);
    run_edge_grant_repo_contract(&store, ENV, "bot_target:001", "human_1").await;
}

#[tokio::test]
async fn memory_permission_profile_repo_contract() {
    let store = MemoryPermissionProfileStore::new();
    run_permission_profile_repo_contract(&store, ENV).await;
}

#[tokio::test]
async fn memory_permission_request_repo_contract() {
    let store = MemoryPermissionRequestStore::new();
    run_permission_request_repo_contract(&store, ENV).await;
}