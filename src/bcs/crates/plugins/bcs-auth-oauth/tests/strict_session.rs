//! Strict-per-request OAuth session verification tests.
//!
//! Drives `OAuthSessionEngine::verify` against the
//! [`TestSessionIdentity`] fake to assert the error taxonomy:
//! - [`SessionAuthError::Invalid`]: bad signature, missing `env` binding,
//!   unknown hash (revoked), sid/revision mismatch (post-rotate stale
//!   token), expired token.
//! - [`SessionAuthError::Unavailable`]: any per-method
//!   [`SessionStoreError::Unavailable`] injection on the lookup path.
//!
//! `Forbidden`, `Internal`, and `Conflict` are not returned by `verify`
//! (forbidden lives in the HTTP layer; internal/conflict surface only on
//! install/refresh/revoke).
//!
//! Plus the binding-claims assertion: `verify` returns the store's
//! [`SessionSnapshot`] (carrying `display_name`/`avatar` from the identity
//! row) once the JWT's `session_id`/`revision` match the live version.

#![allow(clippy::unwrap_used, clippy::expect_used)]

#[path = "support/session_identity.rs"]
mod support;

use std::sync::Arc;

use bcs_auth_api::{AuthSessionIdentityPort, SessionAuthError, SessionScope};
use bcs_jwt::OAuthSessionJwt;

use bcs_auth_oauth::OAuthSessionEngine;
use support::{PortMethod, TestSessionIdentity};

const JWT_SECRET: &str = "test-secret";
const ENV_LOCAL: &str = "local";
const IDLE_SECS: u64 = 3_600;

/// Build an engine and keep a handle to the fake for injection/assertions.
fn engine_with(
    fake: TestSessionIdentity,
    env: &str,
    idle_secs: u64,
) -> (OAuthSessionEngine, Arc<TestSessionIdentity>) {
    let jwt = OAuthSessionJwt::new(JWT_SECRET);
    let arc = Arc::new(fake);
    let port: Arc<dyn AuthSessionIdentityPort> = arc.clone();
    let engine = OAuthSessionEngine::new(jwt, port, env.to_string(), idle_secs);
    (engine, arc)
}

fn scope(uid: &str, prov: &str, env: &str) -> SessionScope {
    SessionScope {
        user_id: uid.to_string(),
        provider: prov.to_string(),
        env: env.to_string(),
    }
}

/// Install a session for `uid`/`github`/`ENV_LOCAL` and return the bearer
/// token. Asserts success so verify tests can focus on verify behavior.
async fn install_token(engine: &OAuthSessionEngine, uid: &str, now: u64) -> String {
    engine
        .install(scope(uid, "github", ENV_LOCAL), None, now)
        .await
        .expect("install for verify fixture must succeed")
        .token
}

#[tokio::test]
async fn verify_accepts_a_freshly_installed_token_and_returns_snapshot() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", Some("alice"), Some("https://avatar/a.png"), ENV_LOCAL)
        .await
        .unwrap();
    let s = scope(&uid, "github", ENV_LOCAL);
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = engine
        .install(s.clone(), None, now)
        .await
        .expect("install must succeed")
        .token;
    let snapshot = engine
        .verify(&token, now + 10)
        .await
        .expect("freshly installed token must verify");
    assert_eq!(snapshot.scope, s);
    assert_eq!(snapshot.version.revision, 1);
    assert_eq!(snapshot.display_name.as_deref(), Some("alice"));
    assert_eq!(snapshot.avatar.as_deref(), Some("https://avatar/a.png"));
    assert_eq!(arc.call_count(PortMethod::GetSessionByHash), 1);
}

#[tokio::test]
async fn verify_rejects_tampered_signature_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;
    // Append a sentinel char to the signature segment → signature mismatch.
    let tampered = format!("{token}X");
    let err = engine.verify(&tampered, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
    // Signature failure happens before the store call.
    assert_eq!(_arc.call_count(PortMethod::GetSessionByHash), 0);
}

#[tokio::test]
async fn verify_rejects_token_signed_with_a_different_secret() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;
    // Decode with the SAME secret the engine signed it with, then re-sign
    // with a different secret. The forged token's signature will fail
    // against the engine's secret.
    let claims = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&token).expect("decode for re-sign");
    let other_jwt = OAuthSessionJwt::new("other-secret");
    let forged = other_jwt.sign(&claims).expect("sign");
    let err = engine.verify(&forged, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn verify_rejects_expired_token_with_invalid_without_store_call() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;
    // `idle_secs = 3600`; `exp = now + 3600`. At `now + 3600` the token is
    // expired (`exp <= now`).
    let err = engine.verify(&token, now + IDLE_SECS).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
    // The JWT-time `exp` check fails first; the store is never queried.
    assert_eq!(arc.call_count(PortMethod::GetSessionByHash), 0);
}

#[tokio::test]
async fn verify_rejects_env_mismatch_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;

    // Verify against an engine bound to a different env: `verify_at`
    // succeeds but the env check fails before any store call.
    let wrong_env_engine = OAuthSessionEngine::new(
        OAuthSessionJwt::new(JWT_SECRET),
        Arc::new(TestSessionIdentity::new()),
        "staging".to_string(),
        IDLE_SECS,
    );
    let err = wrong_env_engine.verify(&token, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn verify_rejects_unknown_hash_with_invalid_after_revoke() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;
    engine.revoke(&token).await.expect("revoke works");
    let err = engine.verify(&token, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn verify_rejects_stale_token_after_rotate_via_revision_mismatch() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;
    // Refresh rotates the binding: claims.revision=1, store revision=2.
    let _new = engine.refresh(&token, now + 10).await.expect("refresh works");
    let err = engine.verify(&token, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn verify_returns_unavailable_when_store_get_by_hash_fails() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", None, None, ENV_LOCAL)
        .await
        .unwrap();
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let token = install_token(&engine, &uid, now).await;
    arc.inject_error(PortMethod::GetSessionByHash, true);
    let err = engine.verify(&token, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Unavailable);
}

#[tokio::test]
async fn verify_rejects_malformed_token_with_invalid() {
    let fake = TestSessionIdentity::new();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let malformed = "not-a-jwt";
    let err = engine.verify(malformed, 1_000_000_010).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}
