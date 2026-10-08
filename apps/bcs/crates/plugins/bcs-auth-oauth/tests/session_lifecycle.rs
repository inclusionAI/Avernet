//! OAuth session lifecycle tests: `install`, `refresh`, `revoke`.
//!
//! Per the plan's "same-`now` double refresh / revoke old kills successor /
//! install CAS conflict / DB unavailable / revision overflow" temperatures.
//! All flows run against the `TestSessionIdentity` fake; no bootstrap
//! coupling.

#![allow(clippy::unwrap_used, clippy::expect_used)]

#[path = "support/session_identity.rs"]
mod support;

use std::sync::Arc;

use bcs_auth_api::{
    AuthSessionIdentityPort, IssuedSession, SessionAuthError, SessionRevoke, SessionScope,
};
use bcs_jwt::OAuthSessionJwt;

use bcs_auth_oauth::OAuthSessionEngine;
use support::{PortMethod, TestSessionIdentity};

const JWT_SECRET: &str = "test-secret";
const ENV_LOCAL: &str = "local";
const IDLE_SECS: u64 = 3_600;

/// Build an engine plus a still-cloned handle to the fake for injection.
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

async fn seed_identity(fake: &TestSessionIdentity, provider: &str, external: &str, env: &str) -> String {
    fake.ensure_identity(provider, external, None, None, env).await.unwrap()
}

#[tokio::test]
async fn install_creates_a_live_session_with_expected_claims() {
    let fake = TestSessionIdentity::new();
    let uid = fake
        .ensure_identity("github", "ext-1", Some("alice"), None, ENV_LOCAL)
        .await
        .unwrap();
    let s = scope(&uid, "github", ENV_LOCAL);
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let issued = engine.install(s.clone(), Some("alice".into()), now).await.expect("install");
    assert_eq!(issued.expires_at, now + IDLE_SECS);
    // JWT decoded claims carry the expected fields.
    let claims = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&issued.token).expect("decode");
    assert_eq!(claims.sub, uid);
    assert_eq!(claims.src, "github");
    assert_eq!(claims.env, ENV_LOCAL);
    assert_eq!(claims.revision, 1);
    assert_eq!(claims.iat, now);
    assert_eq!(claims.exp, now + IDLE_SECS);
    assert_eq!(claims.name.as_deref(), Some("alice"));
    // The store holds a binding keyed by sha256(issued.token).
    assert_eq!(arc.call_count(PortMethod::ReadSessionRevision), 1);
    assert_eq!(arc.call_count(PortMethod::InstallLoginSession), 1);
    let snapshot = arc
        .get_session_by_hash(&s, &bcs_jwt::token_hash(&issued.token), now + 1)
        .await
        .expect("store get")
        .expect("live binding exists");
    assert_eq!(snapshot.version.session_id, claims.session_id);
    assert_eq!(snapshot.version.revision, 1);
    assert_eq!(snapshot.version.token_hash, bcs_jwt::token_hash(&issued.token));
}

#[tokio::test]
async fn install_with_no_display_name_omits_name_claim() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let issued = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    let claims = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&issued.token).expect("decode");
    assert!(claims.name.is_none(), "name claim omitted when display_name is None");
}

#[tokio::test]
async fn install_on_missing_identity_row_returns_conflict() {
    // If the caller forgets to ensure_identity, the store has no row and
    // rejects the install with Conflict — the engine surfaces that as
    // SessionAuthError::Conflict, never as Applied.
    let fake = TestSessionIdentity::new();
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let err = engine.install(scope("u", "github", ENV_LOCAL), None, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Conflict);
}

#[tokio::test]
async fn install_cas_conflict_via_injected_race_returns_err_conflict() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    // The fake will bump the stored revision between read_session_revision
    // and install_login_session, so the engine's CAS misses.
    arc.bump_revision_on_next_install_once(true);
    let err = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Conflict);
    // No overwrite: the injection bumped revision to 1 and left
    // token_hash = None (the engine's intended session was NOT persisted).
    assert_eq!(arc.current_revision(&scope(&uid, "github", ENV_LOCAL)), 1);
    assert!(arc.current_token_hash(&scope(&uid, "github", ENV_LOCAL)).is_none());
}

#[tokio::test]
async fn install_unavailable_when_store_read_faults() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    arc.inject_error(PortMethod::ReadSessionRevision, true);
    let err = engine.install(scope(&uid, "github", ENV_LOCAL), None, 1_000_000_000).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Unavailable);
}

#[tokio::test]
async fn install_unavailable_when_store_install_faults() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    arc.inject_error(PortMethod::InstallLoginSession, true);
    let err = engine.install(scope(&uid, "github", ENV_LOCAL), None, 1_000_000_000).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Unavailable);
}

#[tokio::test]
async fn install_revision_overflow_returns_internal() {
    let fake = TestSessionIdentity::new();
    // Seed the fake to start new scopes at u64::MAX so the engine's
    // `expected.checked_add(1)` overflows.
    fake.set_initial_revision_for_new_scope(u64::MAX);
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let err = engine
        .install(scope(&uid, "github", ENV_LOCAL), None, 1_000_000_000)
        .await
        .unwrap_err();
    assert_eq!(err, SessionAuthError::Internal);
}

#[tokio::test]
async fn refresh_twice_same_now_yields_distinct_tokens_and_advances_revision() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let s = scope(&uid, "github", ENV_LOCAL);
    let install = engine.install(s.clone(), None, now).await.expect("install");
    // Two refresh calls with the SAME `now`. Spec: refresh bumps the stored
    // revision and rotates the binding each time, even if `iat`/`exp` are
    // unchanged.
    let first: IssuedSession = engine.refresh(&install.token, now).await.expect("refresh 1");
    let second: IssuedSession = engine.refresh(&first.token, now).await.expect("refresh 2");
    assert_ne!(first.token, second.token, "tokens must differ across refreshes");
    assert_ne!(install.token, first.token);
    // Decode and assert revision advances.
    let c_install = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&install.token).unwrap();
    let c_first = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&first.token).unwrap();
    let c_second = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&second.token).unwrap();
    assert_eq!(c_install.revision, 1);
    assert_eq!(c_first.revision, 2);
    assert_eq!(c_second.revision, 3);
    // All three tokens share the same `session_id` (refresh preserves sid).
    assert_eq!(c_install.session_id, c_first.session_id);
    assert_eq!(c_first.session_id, c_second.session_id);
    // The install + first-refresh tokens' hashes are no longer queryable.
    assert!(_arc
        .get_session_by_hash(&s, &bcs_jwt::token_hash(&install.token), now)
        .await
        .unwrap()
        .is_none(), "install-token hash is no longer queryable");
    assert!(_arc
        .get_session_by_hash(&s, &bcs_jwt::token_hash(&first.token), now)
        .await
        .unwrap()
        .is_none(), "first-refresh hash is no longer queryable");
    let snap = _arc
        .get_session_by_hash(&s, &bcs_jwt::token_hash(&second.token), now)
        .await
        .unwrap()
        .expect("second-refresh hash is live");
    assert_eq!(snap.version.revision, 3);
}

#[tokio::test]
async fn refresh_rejects_expired_token_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    let err = engine.refresh(&install.token, now + IDLE_SECS).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn refresh_rejects_env_mismatch_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    // Wrong-env engine: verify_at passes, env check fails, store never hit.
    let wrong_env = OAuthSessionEngine::new(
        OAuthSessionJwt::new(JWT_SECRET),
        Arc::new(TestSessionIdentity::new()),
        "staging".into(),
        IDLE_SECS,
    );
    let err = wrong_env.refresh(&install.token, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn refresh_rejects_unknown_token_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    engine.revoke(&install.token).await.expect("revoke kills binding");
    let err = engine.refresh(&install.token, now + 10).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn refresh_rejects_token_signed_by_other_secret_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    // Decode the install token with the SAME secret the engine signed it
    // with, then re-sign the claims under a different secret. The forged
    // token's signature will fail against the engine's secret.
    let claims = OAuthSessionJwt::new(JWT_SECRET).verify_for_revoke(&install.token).expect("decode");
    let other_jwt = OAuthSessionJwt::new("other-secret");
    let forged = other_jwt.sign(&claims).expect("sign");
    let err = engine.refresh(&forged, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn refresh_unavailable_when_store_get_by_hash_faults() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    arc.inject_error(PortMethod::GetSessionByHash, true);
    let err = engine.refresh(&install.token, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Unavailable);
}

#[tokio::test]
async fn refresh_unavailable_when_store_rotate_faults() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    arc.inject_error(PortMethod::RotateSession, true);
    let err = engine.refresh(&install.token, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Unavailable);
}

#[tokio::test]
async fn refresh_cas_conflict_surfaces_as_invalid_per_spec() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    // The fake bumps the stored revision between get_session_by_hash and
    // rotate_session, so the rotate CAS misses and returns Conflict; the
    // engine maps Conflict → Invalid (per the spec's "refresh conflict →
    // 401 Invalid at engine level").
    arc.bump_revision_on_next_rotate_once(true);
    let err = engine.refresh(&install.token, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn revoke_old_token_kills_refreshed_successor() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    // Refresh: fresh token, same sid, new revision.
    let refreshed: IssuedSession = engine.refresh(&install.token, now).await.expect("refresh");
    // Revoke the (stale) install token. Spec: revoke by session_id, so the
    // refreshed successor (same sid) is ALSO revoked.
    let out = engine.revoke(&install.token).await.expect("revoke");
    assert_eq!(out, SessionRevoke::Revoked);
    // The refreshed token no longer verifies.
    let err = engine.verify(&refreshed.token, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
    // The install token also no longer verifies.
    let err = engine.verify(&install.token, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn revoke_works_on_expired_token() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    // Revoke skips exp check, so an expired token can still be revoked.
    let out = engine.revoke(&install.token).await.expect("revoke still ok");
    assert_eq!(out, SessionRevoke::Revoked);
    // After revoke, verify against an in-the-past `now` (where the token's
    // signature + exp WOULD be valid) still fails because the hash is gone.
    let err = engine.verify(&install.token, now).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn revoke_is_idempotent_returns_not_current() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    let first = engine.revoke(&install.token).await.expect("revoke 1");
    assert_eq!(first, SessionRevoke::Revoked);
    let second = engine.revoke(&install.token).await.expect("revoke 2");
    assert_eq!(second, SessionRevoke::NotCurrent);
}

#[tokio::test]
async fn revoke_rejects_invalid_signature_with_invalid() {
    let fake = TestSessionIdentity::new();
    let _uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let err = engine.revoke("not-a-jwt").await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn revoke_rejects_env_mismatch_with_invalid() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, _arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    let wrong_env = OAuthSessionEngine::new(
        OAuthSessionJwt::new(JWT_SECRET),
        Arc::new(TestSessionIdentity::new()),
        "staging".into(),
        IDLE_SECS,
    );
    let err = wrong_env.revoke(&install.token).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Invalid);
}

#[tokio::test]
async fn revoke_unavailable_when_store_revoke_faults() {
    let fake = TestSessionIdentity::new();
    let uid = seed_identity(&fake, "github", "ext-1", ENV_LOCAL).await;
    let (engine, arc) = engine_with(fake, ENV_LOCAL, IDLE_SECS);
    let now = 1_000_000_000;
    let install = engine.install(scope(&uid, "github", ENV_LOCAL), None, now).await.expect("install");
    arc.inject_error(PortMethod::RevokeSession, true);
    let err = engine.revoke(&install.token).await.unwrap_err();
    assert_eq!(err, SessionAuthError::Unavailable);
}
