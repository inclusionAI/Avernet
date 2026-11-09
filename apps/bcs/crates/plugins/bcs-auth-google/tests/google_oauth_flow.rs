//! Google OAuth login-flow fixtures over the real identity store.
//!
//! Task 11 (V1 API auth plan): the session lifecycle is now STRICT and
//! engine-owned. The legacy in-adapter JWT/hot-path cases that lived here
//! are REPLACED by the strict-side suites, with this file keeping the
//! google-specific coverage:
//!
//! - google `auth_url` shape (also pinned beside in google_plugin_tests.rs);
//! - identity-materialization on the callback: the shared login bridge
//!   calls `ensure_identity` on the SAME store the strict session
//!   snapshot joins its display fields from.
//!
//! Replaced cases (name -> successor):
//! - full_oauth_session_flow (legacy bind + plugin) ->
//!   `bcs-auth-oauth/tests/verify_session_expiry_contract.rs` (strict
//!   `verify_oauth_session` over the engine, incl. the no-cookie anonymous
//!   and invalid-signature -> None cases) plus the google-configured
//!   bootstrap server E2E in
//!   `crates/bootstrap/bcs/tests/integration_oauth_routes.rs`.
//! - expired_jwt_returns_none ->
//!   `an_expired_session_is_rejected_with_no_expiry_material`
//!   (same suite).
//! - verify_oauth_session_hot_path_does_not_refresh -> the engine's verify
//!   is read-only by contract (`bcs-auth-oauth/tests/session_lifecycle.rs`
//!   pins rotation/re-sign to the CAS lifecycle only).
//! - verify_oauth_session_rejects_unbound_jwt ->
//!   `bcs-auth-oauth/tests/strict_session.rs` (unbound/rotated-out/revoked
//!   hash rejections).
//! - token_storage_and_retrieval_via_port (update_token backdoor) ->
//!   `bcs-user-identity` strict session conformance
//!   (`conformance_auth_session_memory.rs` / `_sqlite.rs` +
//!   `bcs-test-support` `oauth_session_engine` harness).

use bcs_auth_api::OAuthProvider;
use bcs_auth_google::{GoogleOAuthConfig, GoogleOAuthProvider};
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::MemoryUserIdentityRepo;

#[test]
fn google_provider_auth_url_format() {
    let config = GoogleOAuthConfig {
        client_id: "123.apps.googleusercontent.com".to_string(),
        client_secret: "secret".to_string(),
    };
    let provider = GoogleOAuthProvider::new(config);
    let url = provider.auth_url("csrf-state-xyz", "http://localhost:21000/auth/callback/google");

    assert!(url.starts_with("https://accounts.google.com/o/oauth2/v2/auth"));
    assert!(url.contains("client_id=123.apps.googleusercontent.com"));
    assert!(url.contains("state=csrf-state-xyz"));
    assert!(url.contains("redirect_uri="));
    assert!(url.contains("scope="));
}

/// The Google callback materializes the identity row (idempotent on the
/// external subject) in the shared identity store — the same store the
/// strict session snapshot joins `username`/`avatar` display data from.
#[tokio::test]
async fn google_callback_materializes_the_identity_row() {
    let repo = MemoryUserIdentityRepo::new();
    let user_id = repo
        .ensure_identity("google", "google-sub-123", Some("Alice"), None, "default")
        .await
        .expect("first login ensure");
    let again = repo
        .ensure_identity("google", "google-sub-123", Some("Alice Renamed"), None, "default")
        .await
        .expect("repeat login ensure");
    assert_eq!(user_id, again, "repeat logins map to the same internal id");

    let display = repo
        .get_by_user_id_display(&user_id)
        .await
        .expect("display lookup");
    assert_eq!(display.auth_source, "google");
    assert_eq!(display.external_user_name.as_deref(), Some("Alice Renamed"));
}
