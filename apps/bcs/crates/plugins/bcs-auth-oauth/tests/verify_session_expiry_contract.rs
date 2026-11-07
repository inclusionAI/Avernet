//! P2-A slice 2 conformance: the production OAuth session verification
//! carries its *actually verified* session expiry into
//! `AuthPrincipal.human_expires_at_ms` — the only production source of the
//! trusted window the strict caller-identity extraction consumes.
//!
//! Task 8 rework: verification now delegates to the STRICT engine
//! (`verify_oauth_session(engine, headers)`), so the contract drives the
//! engine path with the real strict JWT service and `TestSessionIdentity`
//! (only the identity/session store is a stub). Legacy sid-less JWTs are
//! rejected by this path by contract.

mod support;

use std::sync::Arc;

use support::session_identity::TestSessionIdentity;

use axum::http::{HeaderMap, HeaderValue};
use bcs_auth_api::session::AuthSessionIdentityPort;
use bcs_auth_oauth::{OAuthSessionEngine, verify_oauth_session};
use bcs_jwt::OAuthSessionJwt;

const SECRET: &str = "contract-secret";
const ENV: &str = "dev";
const PROVIDER: &str = "google";

fn engine(identities: &Arc<TestSessionIdentity>, idle_secs: u64) -> OAuthSessionEngine {
    OAuthSessionEngine::new(
        OAuthSessionJwt::new(SECRET),
        identities.clone(),
        ENV.to_string(),
        idle_secs,
    )
}

async fn install_session(
    identities: &Arc<TestSessionIdentity>,
    idle_secs: u64,
    now: u64,
) -> String {
    let user_id = identities
        .ensure_identity(PROVIDER, "contract-user-1", None, None, ENV)
        .await
        .expect("ensure the login identity");
    let engine = engine(identities, idle_secs);
    let scope = bcs_auth_api::session::SessionScope {
        user_id,
        provider: PROVIDER.to_string(),
        env: ENV.to_string(),
    };
    engine
        .install(scope, None, now)
        .await
        .expect("install the session")
        .token
}

fn session_headers(token: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        HeaderValue::from_str(&format!("bcs_session={token}")).expect("cookie header value"),
    );
    headers
}

fn wall_now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default()
}

#[tokio::test]
async fn verified_session_carries_its_real_expiry_in_milliseconds() {
    let identities = Arc::new(TestSessionIdentity::new());
    let now = wall_now_secs();
    let token = install_session(&identities, 1800, now).await;
    let engine = engine(&identities, 1800);

    let principal = verify_oauth_session(&engine, &session_headers(&token))
        .await
        .expect("no IO failure")
        .expect("strictly verified session yields a principal");
    // The trusted window is the verified session expiry converted to ms.
    assert_eq!(principal.human_expires_at_ms, Some((now + 1800) * 1000));
}

#[tokio::test]
async fn no_cookie_yields_no_principal_and_no_expiry_material() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine(&identities, 1800);
    let principal = verify_oauth_session(&engine, &HeaderMap::new())
        .await
        .expect("no cookie is a miss, not an error");
    assert!(principal.is_none());
}

#[tokio::test]
async fn an_expired_session_is_rejected_with_no_expiry_material() {
    let identities = Arc::new(TestSessionIdentity::new());
    let now = wall_now_secs();
    // Installed two seconds ago with a 1-second window: exp = now - 1 is
    // already behind any wall clock and rejected before any expiry
    // material exists.
    let token = install_session(&identities, 1, now - 2).await;
    let engine = engine(&identities, 1);
    let principal = verify_oauth_session(&engine, &session_headers(&token))
        .await
        .expect("expired is a miss, not an error");
    assert!(principal.is_none(), "expired session must not yield a principal");
}

#[tokio::test]
async fn wrong_secret_signature_never_yields_a_principal() {
    let identities = Arc::new(TestSessionIdentity::new());
    let now = wall_now_secs();
    let token = install_session(&identities, 1800, now).await;

    // Engine bound to a DIFFERENT secret: signature verification fails first.
    let other_engine = OAuthSessionEngine::new(
        OAuthSessionJwt::new("other-secret"),
        identities.clone(),
        ENV.to_string(),
        1800,
    );
    let principal = verify_oauth_session(&other_engine, &session_headers(&token))
        .await
        .expect("bad signature is a miss, not an error");
    assert!(principal.is_none());
}
