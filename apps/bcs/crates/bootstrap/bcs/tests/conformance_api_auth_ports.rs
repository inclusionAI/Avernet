//! R25 conformance drivers for the V1 auth application ports (plan Task
//! 14) over the REAL bootstrap bridges: the provider chain bridge, the
//! strict session bridge on the Task 8 engine, and the pending-login
//! bridge on the Task 9 memory store. The harnesses live in central
//! `bcs-test-support`; this file only assembles production wiring.

use std::sync::Arc;

use bcs_auth_api::OAuthProvider;
use bcs_auth_github::{GitHubOAuthConfig, GitHubOAuthProvider};
use bcs_auth_oauth::{MemoryPendingOAuthLoginStore, OAuthSessionEngine};
use bcs::api_auth_ports::{
    ConfiguredOAuthProviderBridge, PendingLoginStoreBridge, StrictSessionPortBridge,
};
use bcs::identity_session_wiring::RepoAuthSessionIdentityPort;
use bcs_jwt::OAuthSessionJwt;
use bcs_test_support::contract::port::{
    o_auth_provider_port_contract_tests, o_auth_session_port_contract_tests,
    pending_o_auth_login_port_contract_tests,
};
use bcs_user_identity::MemoryUserIdentityRepo;

/// The provider-chain bridge over a REAL GitHub provider satisfies the
/// `OAuthProviderPort` contract (chain membership, state→URL, unenrolled
/// rejection). No network is touched: only `names`/`auth_url` run.
#[tokio::test]
async fn configured_provider_chain_passes_the_provider_port_contract() {
    let provider = GitHubOAuthProvider::new(GitHubOAuthConfig {
        client_id: "harness-github-client-id".to_string(),
        client_secret: "harness-github-client-secret".to_string(),
    });
    let bridge = ConfiguredOAuthProviderBridge::new(vec![(
        "github".to_string(),
        Arc::new(provider) as Arc<dyn OAuthProvider>,
    )]);
    o_auth_provider_port_contract_tests(&bridge).await;
}

/// The strict session bridge over the shared engine and the strict
/// identity port satisfies the `OAuthSessionPort` contract (atomic
/// install/rotate/revoke lifecycle, §8.5).
#[tokio::test]
async fn strict_session_bridge_passes_the_session_port_contract() {
    let store: Arc<MemoryUserIdentityRepo> = Arc::new(MemoryUserIdentityRepo::new());
    let identities: Arc<RepoAuthSessionIdentityPort> =
        Arc::new(RepoAuthSessionIdentityPort::new(store.clone(), store));
    let engine = OAuthSessionEngine::new(
        OAuthSessionJwt::new("harness-session-signing-material"),
        identities.clone(),
        "harness-env".to_string(),
        3_600,
    );
    let bridge =
        StrictSessionPortBridge::new(Arc::new(engine), identities, "harness-env".to_string());
    o_auth_session_port_contract_tests(&bridge).await;
}

/// The pending-login bridge over the production memory store satisfies
/// the `PendingOAuthLoginPort` contract (browser-bound one-time state,
/// §8.4).
#[tokio::test]
async fn pending_login_bridge_passes_the_pending_port_contract() {
    let bridge =
        PendingLoginStoreBridge::new(Arc::new(MemoryPendingOAuthLoginStore::new()));
    pending_o_auth_login_port_contract_tests(&bridge).await;
}
