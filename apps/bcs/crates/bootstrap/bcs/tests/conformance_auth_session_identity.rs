//! `AuthSessionIdentityPort` conformance driver (V1 API auth plugin chain).
//!
//! Runs the central `bcs-test-support` harness against bootstrap's
//! `RepoAuthSessionIdentityPort` — the pure field-by-field translation from
//! the persistence `AuthSessionRepoPort`/`UserIdentityRepoPort` contracts
//! to the strict plugin-API port. ONE `MemoryUserIdentityRepo` instance
//! backs both repo roles so identity creation and session reads join over
//! the same state.

use std::sync::Arc;

use bcs::identity_session_wiring::RepoAuthSessionIdentityPort;
use bcs_test_support::contract::plugin::auth_session_identity_port_contract_tests;
use bcs_user_identity::MemoryUserIdentityRepo;

#[tokio::test]
async fn repo_bridge_passes_auth_session_identity_contract() {
    let store: Arc<MemoryUserIdentityRepo> = Arc::new(MemoryUserIdentityRepo::new());
    auth_session_identity_port_contract_tests(
        store.clone(),
        store,
        |repo, identities| Arc::new(RepoAuthSessionIdentityPort::new(repo, identities)),
    )
    .await;
}
