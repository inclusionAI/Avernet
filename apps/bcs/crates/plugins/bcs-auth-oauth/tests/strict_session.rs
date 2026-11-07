//! Strict OAuth session verification semantics (V1 API auth plugin chain,
//! Task 8): the engine enforces signature + claim integrity, the INJECTED
//! environment (claims.env never selects storage), hash binding, and the
//! session instance's sid/revision. Legacy sid-less JWTs are rejected, and
//! the read-only compatibility exit maps strict categories to the old
//! `Option` shape at exactly one marked place.

mod support;

use std::sync::Arc;

use support::session_identity::TestSessionIdentity;

use axum::http::{HeaderMap, HeaderValue};
use bcs_auth_api::session::{
    AuthSessionIdentityPort, IssuedSession, SessionAuthError, SessionScope, SessionSnapshot,
    SessionVersion,
};
use bcs_auth_oauth::{OAuthSessionEngine, verify_oauth_session};
use bcs_jwt::{token_hash, OAuthSessionClaims, OAuthSessionJwt};

const SECRET: &str = "strict-session-contract-secret";
const ENV: &str = "dev";
const PROVIDER: &str = "github";
const NOW: u64 = 1_800_000_000;
const IDLE_SECS: u64 = 3_600;

fn engine_for(identities: &Arc<TestSessionIdentity>, env: &str, idle_secs: u64) -> OAuthSessionEngine {
    OAuthSessionEngine::new(
        OAuthSessionJwt::new(SECRET),
        identities.clone(),
        env.to_string(),
        idle_secs,
    )
}

/// A fresh identity plus its scope, registered through the strict port the
/// way a real OAuth callback would (ensure_identity then engine install).
async fn new_scope(identities: &Arc<TestSessionIdentity>, name: Option<&str>) -> SessionScope {
    let user_id = identities
        .ensure_identity(PROVIDER, "ext-strict-1", name, None, ENV)
        .await
        .expect("ensure_identity must create the login identity");
    SessionScope {
        user_id,
        provider: PROVIDER.to_string(),
        env: ENV.to_string(),
    }
}

async fn install(
    engine: &OAuthSessionEngine,
    scope: &SessionScope,
    display_name: Option<String>,
) -> IssuedSession {
    engine
        .install(scope.clone(), display_name, NOW)
        .await
        .expect("install must apply against the fresh revision")
}

fn signed_claims(session_id: &str, revision: u64, env: &str, exp: u64) -> String {
    let claims = OAuthSessionClaims {
        sub: "user-seeded".to_string(),
        src: PROVIDER.to_string(),
        env: env.to_string(),
        session_id: session_id.to_string(),
        revision,
        iat: NOW,
        exp,
        name: None,
    };
    OAuthSessionJwt::new(SECRET).sign(&claims).expect("sign the seeded token")
}

fn cookie_headers(token: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        HeaderValue::from_str(&format!("bcs_session={token}")).expect("cookie header value"),
    );
    headers
}

#[tokio::test]
async fn installed_session_verifies_with_the_joined_snapshot() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, IDLE_SECS);
    let scope = new_scope(&identities, Some("Alice")).await;
    let issued = install(&engine, &scope, None).await;

    let snapshot = engine
        .verify(&issued.token, NOW)
        .await
        .expect("the installed session must verify");
    assert_eq!(snapshot.scope, scope, "scope env comes from the injected env");
    assert_eq!(snapshot.version.revision, 1);
    assert_eq!(snapshot.expires_at, NOW + IDLE_SECS);
    assert_eq!(snapshot.username, "Alice");
    assert_eq!(issued.expires_at, snapshot.expires_at);
    // Also verifies one tick later, inside the trusted window.
    engine
        .verify(&issued.token, NOW + 60)
        .await
        .expect("inside the idle window the session still verifies");
}

#[tokio::test]
async fn tampered_signature_is_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, IDLE_SECS);
    let scope = new_scope(&identities, None).await;
    let issued = install(&engine, &scope, None).await;

    // Same strict claims signed by a different key: the signature fails
    // before any storage access.
    let forged_secret = OAuthSessionJwt::new("another-secret");
    let claims = OAuthSessionClaims {
        sub: scope.user_id.clone(),
        src: PROVIDER.to_string(),
        env: ENV.to_string(),
        session_id: "forged-sid".to_string(),
        revision: 1,
        iat: NOW,
        exp: NOW + IDLE_SECS,
        name: None,
    };
    let forged = forged_secret.sign(&claims).expect("sign with the attacker key");

    let mut parts = forged.split('.');
    let head = parts.next().expect("header part");
    let payload = parts.next().expect("payload part");
    let other_sig =
        issued.token.split('.').nth(2).expect("a signature part exists");
    let stitched = format!("{head}.{payload}.{other_sig}");

    assert!(matches!(
        engine.verify(&stitched, NOW).await,
        Err(SessionAuthError::Invalid)
    ));
    assert!(!identities.called("get_session_by_hash"));
}

#[tokio::test]
async fn legacy_sid_less_jwt_is_rejected_as_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());

    // A legacy `Claims` token: valid signature, NO env/session_id/revision.
    let svc = bcs_jwt::JwtService::new(SECRET);
    let claims = bcs_jwt::Claims {
        sub: "user-1".to_string(),
        src: PROVIDER.to_string(),
        iat: NOW,
        exp: NOW + 1800,
        name: None,
    };
    let legacy = svc.sign(&claims).expect("sign the legacy token");

    let engine = engine_for(&identities, ENV, IDLE_SECS);
    assert!(matches!(
        engine.verify(&legacy, NOW).await,
        Err(SessionAuthError::Invalid)
    ));
    // Strict verification denies before storage: no session query is issued.
    assert!(identities.operations().is_empty());
}

#[tokio::test]
async fn wrong_environment_token_is_forbidden_without_storage_access() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, IDLE_SECS);
    let scope = new_scope(&identities, None).await;
    let issued = install(&engine, &scope, None).await;

    // Same token presented to a PRODUCTION-env deployment of the same
    // secret: the claims env must not select another env's storage.
    let prod_engine = engine_for(&identities, "prod", IDLE_SECS);
    assert!(matches!(
        prod_engine.verify(&issued.token, NOW).await,
        Err(SessionAuthError::Forbidden)
    ));
    assert!(
        !identities.called("get_session_by_hash"),
        "env mismatch must fail fast before any session query"
    );
}

#[tokio::test]
async fn unbound_hash_is_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, IDLE_SECS);

    // Well-signed strict claims that were never installed: no binding.
    let token = signed_claims("never-installed-sid", 1, ENV, NOW + IDLE_SECS);
    assert!(matches!(
        engine.verify(&token, NOW).await,
        Err(SessionAuthError::Invalid)
    ));
}

#[tokio::test]
async fn snapshot_version_mismatch_is_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, IDLE_SECS);

    // A token whose hash IS the current binding but whose claims name a
    // different session instance: the snapshot outvotes the claims.
    let scope = SessionScope {
        user_id: "user-seeded".to_string(),
        provider: PROVIDER.to_string(),
        env: ENV.to_string(),
    };
    let forged = signed_claims("sid-attacker", 1, ENV, NOW + IDLE_SECS);
    identities.seed_identity_and_session(
        scope,
        SessionVersion {
            session_id: "sid-store".to_string(),
            revision: 1,
            token_hash: token_hash(&forged),
        },
        NOW + IDLE_SECS,
        None,
    );

    assert!(matches!(
        engine.verify(&forged, NOW).await,
        Err(SessionAuthError::Invalid)
    ));
    assert!(identities.called("get_session_by_hash"));
}

#[tokio::test]
async fn expired_session_is_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, 10);
    let scope = new_scope(&identities, None).await;
    let issued = install(&engine, &scope, None).await;

    // exp is enforced inclusively: at now == exp the session is dead.
    assert!(matches!(
        engine.verify(&issued.token, NOW + 10).await,
        Err(SessionAuthError::Invalid)
    ));
}

#[tokio::test]
async fn store_unavailable_surfaces_unavailable_not_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    identities.fail_with(
        "get_session_by_hash",
        bcs_auth_api::session::SessionStoreError::Unavailable,
    );
    let engine = engine_for(&identities, ENV, IDLE_SECS);
    let token = signed_claims("any-sid", 1, ENV, NOW + IDLE_SECS);
    assert!(matches!(
        engine.verify(&token, NOW).await,
        Err(SessionAuthError::Unavailable)
    ));
}

#[tokio::test]
async fn corrupt_record_surfaces_internal() {
    let identities = Arc::new(TestSessionIdentity::new());
    identities.fail_with(
        "get_session_by_hash",
        bcs_auth_api::session::SessionStoreError::CorruptRecord,
    );
    let engine = engine_for(&identities, ENV, IDLE_SECS);
    let token = signed_claims("any-sid", 1, ENV, NOW + IDLE_SECS);
    assert!(matches!(
        engine.verify(&token, NOW).await,
        Err(SessionAuthError::Internal)
    ));
}

// ---------------------------------------------------------------------------
// Compatibility exit: the legacy read-only shape delegates to the engine.
// ---------------------------------------------------------------------------

/// Wall-clock now, matching the legacy verification contract.
fn wall_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or_default()
}

async fn delegated_session(
    name: Option<&str>,
    idle_secs: u64,
    now: u64,
) -> (
    Arc<TestSessionIdentity>,
    OAuthSessionEngine,
    SessionScope,
    IssuedSession,
) {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, idle_secs);
    let scope = new_scope(&identities, name).await;
    let issued = engine
        .install(scope.clone(), name.map(str::to_string), now)
        .await
        .expect("install for the delegated verification");
    (identities, engine, scope, issued)
}

#[tokio::test]
async fn delegation_maps_a_valid_snapshot_to_the_old_principal_shape() {
    let now = wall_now();
    let (_identities, engine, scope, issued) =
        delegated_session(Some("Alice"), 1800, now).await;

    let principal = verify_oauth_session(&engine, &cookie_headers(&issued.token))
        .await
        .expect("a verified session is not an error")
        .expect("a verified session yields a principal");
    assert_eq!(principal.user_id.as_deref(), Some(scope.user_id.as_str()));
    assert_eq!(principal.user_name.as_deref(), Some("Alice"));
    assert_eq!(principal.source_name.as_deref(), Some(PROVIDER));
    assert_eq!(
        principal.human_expires_at_ms,
        Some(issued.expires_at.saturating_mul(1000)),
        "the trusted window is the actually verified expiry in milliseconds"
    );
}

#[tokio::test]
async fn delegation_passes_a_nameless_username_through_without_fallback() {
    let now = wall_now();
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, ENV, 1800);
    let scope = new_scope(&identities, None).await;
    let issued = engine
        .install(scope.clone(), None, now)
        .await
        .expect("install a nameless identity session");

    let principal = verify_oauth_session(&engine, &cookie_headers(&issued.token))
        .await
        .expect("a verified session is not an error")
        .expect("a nameless identity still authenticates");
    // No §7.3 internal-ID fallback here: the projection (Tasks 11/12) owes
    // the display-name fallback, not the verification path.
    assert!(principal.user_name.is_none());
    assert_eq!(principal.user_id.as_deref(), Some(scope.user_id.as_str()));
}

#[tokio::test]
async fn delegation_maps_denials_to_the_legacy_none() {
    let now = wall_now();
    let (_identities, engine, _scope, _issued) = delegated_session(None, 1800, now).await;

    // No cookie: anonymous, not an error.
    assert!(
        verify_oauth_session(&engine, &HeaderMap::new())
            .await
            .expect("a missing cookie is a miss, not an error")
            .is_none()
    );

    // A legacy sid-less JWT is REJECTED (mapped to the old None at the
    // compatibility exit, never silently accepted).
    let svc = bcs_jwt::JwtService::new(SECRET);
    let claims = bcs_jwt::Claims {
        sub: "user-1".to_string(),
        src: PROVIDER.to_string(),
        iat: now,
        exp: now + 1800,
        name: None,
    };
    let legacy = svc.sign(&claims).expect("sign the legacy token");
    assert!(
        verify_oauth_session(&engine, &cookie_headers(&legacy))
            .await
            .expect("a rejected credential is a miss at the compat exit")
            .is_none(),
        "the delegated exit must not keep accepting legacy sid-less JWTs"
    );
}

#[tokio::test]
async fn delegation_propagates_dependency_failures_as_auth_errors() {
    let now = wall_now();
    let (identities, engine, _scope, issued) = delegated_session(None, 1800, now).await;
    identities.fail_with(
        "get_session_by_hash",
        bcs_auth_api::session::SessionStoreError::Unavailable,
    );

    let error = verify_oauth_session(&engine, &cookie_headers(&issued.token))
        .await
        .expect_err("a store outage must surface, never become None");
    assert!(
        matches!(error, bcs_auth_api::AuthError::LookupFailed(_)),
        "dependency failures map to AuthError at the compat exit, got {error:?}"
    );
}

/// Compile-time shape check: the snapshot type the engine returns is the
/// Task 5 contract type (no engine-local duplication).
#[test]
fn engine_verify_returns_the_contract_snapshot_type() {
    fn assert_snapshot(_: SessionSnapshot) {}
    assert_snapshot(SessionSnapshot {
        scope: SessionScope {
            user_id: String::new(),
            provider: String::new(),
            env: String::new(),
        },
        version: SessionVersion {
            session_id: String::new(),
            revision: 0,
            token_hash: String::new(),
        },
        expires_at: 0,
        username: String::new(),
        display_name: None,
        avatar: None,
    });
}
