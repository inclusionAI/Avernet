//! Atomic OAuth session lifecycle (V1 API auth plugin chain, Task 8):
//! install / same-`now` refresh / revoke drive the strict identity port as
//! compare-and-swap commands — the session credential is issued only AFTER
//! the CAS applies, a refresh conflict is the 401-side `Invalid` (never
//! retried), and revoke kills the whole session instance by sid, including
//! every rotated descendant.
//!
//! The binding lifecycle sequence itself lives in the central conformance
//! harness so a real store/bridge (Task 13) repeats THE SAME assertions;
//! the tests here drive that harness through [`TestSessionIdentity`] and add
//! the failure-taxonomy, overflow, and redaction coverage the harness pins
//! generically.

mod support;

use std::sync::Arc;

use support::session_identity::TestSessionIdentity;

use bcs_auth_api::session::{
    AuthSessionIdentityPort, SessionAuthError, SessionRevoke, SessionScope, SessionVersion,
};
use bcs_auth_oauth::OAuthSessionEngine;
use bcs_jwt::{OAuthSessionClaims, OAuthSessionJwt};
use bcs_test_support::contract::plugin::oauth_session_engine_contract_tests;

const SECRET: &str = "session-lifecycle-contract-secret";
const ENV: &str = "dev";
const PROVIDER: &str = "github";
const NOW: u64 = 1_800_000_000;
const IDLE_SECS: u64 = 3_600;

fn engine_for(identities: &Arc<TestSessionIdentity>, idle_secs: u64) -> OAuthSessionEngine {
    OAuthSessionEngine::new(
        OAuthSessionJwt::new(SECRET),
        identities.clone(),
        ENV.to_string(),
        idle_secs,
    )
}

async fn registered_scope(identities: &Arc<TestSessionIdentity>) -> SessionScope {
    let user_id = identities
        .ensure_identity(PROVIDER, "ext-lifecycle-1", Some("Bob"), None, ENV)
        .await
        .expect("ensure_identity must create the login identity");
    SessionScope {
        user_id,
        provider: PROVIDER.to_string(),
        env: ENV.to_string(),
    }
}

fn seeded_scope() -> SessionScope {
    SessionScope {
        user_id: "user-seeded".to_string(),
        provider: PROVIDER.to_string(),
        env: ENV.to_string(),
    }
}

/// Sign an arbitrary strict token for the STORED scope, so tests can drive
/// the engine against fixture-seeded versions.
fn sign_for_seed(session_id: &str, revision: u64, exp: u64) -> String {
    let claims = OAuthSessionClaims {
        sub: "user-seeded".to_string(),
        src: PROVIDER.to_string(),
        env: ENV.to_string(),
        session_id: session_id.to_string(),
        revision,
        iat: NOW,
        exp,
        name: None,
    };
    OAuthSessionJwt::new(SECRET).sign(&claims).expect("sign the seeded token")
}

fn count_op(identities: &Arc<TestSessionIdentity>, op: &str) -> usize {
    identities.operations().iter().filter(|name| **name == op).count()
}

#[tokio::test]
async fn central_lifecycle_contract_holds_over_the_strict_double() {
    let identities = Arc::new(TestSessionIdentity::new());
    oauth_session_engine_contract_tests(
        SECRET,
        identities.clone(),
        ENV.to_string(),
        IDLE_SECS,
    )
    .await;
}

#[tokio::test]
async fn install_lost_login_race_is_conflict_without_retry() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;

    // A concurrent login batch won between the revision read and the CAS.
    identities.force_install_conflict();
    let outcome = engine.install(scope.clone(), None, NOW).await;
    assert!(
        matches!(&outcome, Err(SessionAuthError::Conflict)),
        "a lost login race is the 409-side Conflict, got {outcome:?}"
    );
    assert!(
        !matches!(&outcome, Ok(_)),
        "no session credential is issued after a lost race"
    );
    assert_eq!(
        count_op(&identities, "install_login_session"),
        1,
        "a lost install race is never retried inside the engine"
    );
}

#[tokio::test]
async fn install_revision_overflow_is_internal_without_a_write() {
    for overflow_revision in [i64::MAX as u64, u64::MAX] {
        let identities = Arc::new(TestSessionIdentity::new());
        let engine = engine_for(&identities, IDLE_SECS);
        let scope = seeded_scope();
        identities.seed_identity_and_session(
            scope.clone(),
            SessionVersion {
                session_id: "seeded-sid".to_string(),
                revision: overflow_revision,
                token_hash: "seeded-hash".to_string(),
            },
            NOW + IDLE_SECS,
            None,
        );

        let outcome = engine.install(scope.clone(), None, NOW).await;
        assert!(
            matches!(&outcome, Err(SessionAuthError::Internal)),
            "revision overflow (BIGINT and u64) is an internal failure, got {outcome:?}"
        );
        assert!(
            !identities.called("install_login_session"),
            "an overflowing revision must never attempt the CAS write"
        );
    }
}

#[tokio::test]
async fn install_expiry_overflow_is_internal_without_a_write() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, u64::MAX);
    let scope = registered_scope(&identities).await;

    let outcome = engine.install(scope.clone(), None, NOW).await;
    assert!(
        matches!(&outcome, Err(SessionAuthError::Internal)),
        "now + idle window overflow is an internal failure, got {outcome:?}"
    );
    assert!(
        !identities.called("install_login_session"),
        "an overflowing expiry must never attempt the CAS write"
    );
}

#[tokio::test]
async fn refresh_cas_conflict_is_invalid_and_never_retried() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("install session A");

    // A concurrent refresh won the rotation race between the read and the
    // CAS: the loser gets the 401-side Invalid, exactly one attempt.
    identities.force_rotate_conflict();
    let outcome = engine.refresh(&issued.token, NOW + 1).await;
    assert!(
        matches!(&outcome, Err(SessionAuthError::Invalid)),
        "a refresh conflict maps to the 401-side Invalid, never a retried write, got {outcome:?}"
    );
    assert_eq!(
        count_op(&identities, "rotate_session"),
        1,
        "a lost refresh is never retried into an unconditional write"
    );
}

#[tokio::test]
async fn refresh_storage_failure_is_unavailable_not_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("install session A");

    identities.fail_with(
        "rotate_session",
        bcs_auth_api::session::SessionStoreError::Unavailable,
    );
    let outcome = engine.refresh(&issued.token, NOW + 1).await;
    assert!(
        matches!(&outcome, Err(SessionAuthError::Unavailable)),
        "storage failures are 503-side Unavailable, never Invalid, got {outcome:?}"
    );
}

#[tokio::test]
async fn refresh_of_a_rotated_out_token_is_invalid() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let a = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("install session A");
    let _b = engine
        .refresh(&a.token, NOW)
        .await
        .expect("rotate A to B");

    // A (already rotated out) can never rotate again.
    assert!(matches!(
        engine.refresh(&a.token, NOW + 1).await,
        Err(SessionAuthError::Invalid)
    ));
}

#[tokio::test]
async fn refresh_revision_overflow_is_internal_without_a_write() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = seeded_scope();
    // The token's claims match the stored version exactly — both at
    // u64::MAX revision — so the overflow check is hit AFTER verification.
    let token = sign_for_seed("seeded-sid", u64::MAX, NOW + IDLE_SECS);
    identities.seed_identity_and_session(
        scope,
        SessionVersion {
            session_id: "seeded-sid".to_string(),
            revision: u64::MAX,
            token_hash: bcs_jwt::token_hash(&token),
        },
        NOW + IDLE_SECS,
        None,
    );

    let outcome = engine.refresh(&token, NOW + 1).await;
    assert!(
        matches!(&outcome, Err(SessionAuthError::Internal)),
        "refresh revision overflow is an internal failure, got {outcome:?}"
    );
    assert!(
        !identities.called("rotate_session"),
        "an overflowing refresh must never attempt the CAS write"
    );
}

#[tokio::test]
async fn refresh_expiry_overflow_is_internal_without_a_write() {
    let identities = Arc::new(TestSessionIdentity::new());
    let seeded_engine = engine_for(&identities, IDLE_SECS);
    let scope = seeded_scope();
    let token = sign_for_seed("seeded-sid", 1, NOW + IDLE_SECS);
    identities.seed_identity_and_session(
        scope.clone(),
        SessionVersion {
            session_id: "seeded-sid".to_string(),
            revision: 1,
            token_hash: bcs_jwt::token_hash(&token),
        },
        NOW + IDLE_SECS,
        None,
    );
    // The verified version is healthy; only now + idle window overflows.
    let expiry_engine = OAuthSessionEngine::new(
        OAuthSessionJwt::new(SECRET),
        identities.clone(),
        ENV.to_string(),
        u64::MAX - NOW,
    );
    // idempotence guard: the seeded engine proves the token is readable.
    seeded_engine
        .verify(&token, NOW)
        .await
        .expect("the seeded session verifies");

    let outcome = expiry_engine.refresh(&token, NOW + 1).await;
    assert!(
        matches!(&outcome, Err(SessionAuthError::Internal)),
        "refresh expiry overflow is an internal failure, got {outcome:?}"
    );
    assert!(
        !identities.called("rotate_session", ),
        "an overflowing refresh expiry must never attempt the CAS write"
    );
}

#[tokio::test]
async fn revoke_accepts_an_expired_but_integrally_signed_token() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, 10);
    let scope = registered_scope(&identities).await;
    let issued = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("install a short-lived session");

    // Past the expiry the token can neither verify nor refresh …
    assert!(matches!(
        engine.verify(&issued.token, NOW + 10).await,
        Err(SessionAuthError::Invalid)
    ));
    assert!(matches!(
        engine.refresh(&issued.token, NOW + 10).await,
        Err(SessionAuthError::Invalid)
    ));
    // … but logout revokes it irrecoverably (only `exp` is skipped).
    assert_eq!(
        engine
            .revoke(&issued.token)
            .await
            .expect("revoke of an expired instance is an outcome, not an error"),
        SessionRevoke::Revoked
    );
    assert_eq!(
        engine
            .revoke(&issued.token)
            .await
            .expect("a repeat revoke is an outcome, not an error"),
        SessionRevoke::NotCurrent,
        "re-revoking a dead instance is idempotent NotCurrent"
    );
}

#[tokio::test]
async fn revoke_of_a_foreign_env_token_is_forbidden_without_storage_access() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);

    // Signed by the same key, bound to another environment.
    let claims = OAuthSessionClaims {
        sub: "user-1".to_string(),
        src: PROVIDER.to_string(),
        env: "prod".to_string(),
        session_id: "sid-prod".to_string(),
        revision: 1,
        iat: NOW,
        exp: NOW + IDLE_SECS,
        name: None,
    };
    let token = OAuthSessionJwt::new(SECRET)
        .sign(&claims)
        .expect("sign the foreign-env token");

    assert!(matches!(
        engine.revoke(&token).await,
        Err(SessionAuthError::Forbidden)
    ));
    assert!(
        !identities.called("revoke_session"),
        "a foreign-env revoke must fail fast before any storage access"
    );
}

#[tokio::test]
async fn revoke_of_an_unknown_session_id_is_not_current() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let token = sign_for_seed("ghost-sid", 1, NOW + IDLE_SECS);

    // Signed and same-env but names an instance that was never current
    // here: the revoke is an idempotent miss, not a denial.
    assert_eq!(
        engine
            .revoke(&token)
            .await
            .expect("an unknown-instance revoke is an outcome, not an error"),
        SessionRevoke::NotCurrent
    );
}

#[tokio::test]
async fn revoke_storage_failure_is_unavailable() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("install session A");

    identities.fail_with(
        "revoke_session",
        bcs_auth_api::session::SessionStoreError::Unavailable,
    );
    assert!(matches!(
        engine.revoke(&issued.token).await,
        Err(SessionAuthError::Unavailable)
    ));
}

#[tokio::test]
async fn issued_session_redaction_and_sizing() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("install session A");

    // IssuedSession Debug elides the bearer token (plan §8.4).
    let debug = format!("{issued:?}");
    assert!(
        !debug.contains(&issued.token),
        "IssuedSession Debug must never print the token"
    );
    assert!(
        !debug.contains("user-seeded") && !debug.contains("jwt"),
        "IssuedSession Debug carries only the expiry {debug}"
    );

    // Forward constraints from the storage review: the sid fits 64 chars
    // and the fingerprint is the 64-char lowercase SHA-256 hex.
    let claims = OAuthSessionJwt::new(SECRET)
        .verify_at(&issued.token, NOW)
        .expect("the issued token strict-verifies");
    assert!(
        claims.session_id.len() <= 64,
        "session ids must fit the storage VARCHAR(64)"
    );
    let snapshot = engine
        .verify(&issued.token, NOW)
        .await
        .expect("the issued token verifies");
    assert_eq!(snapshot.version.token_hash.len(), 64);

    // A second login issues a NEW random sid at the next revision
    // (no implicit overwrite: install replaces the active instance via CAS).
    let relogin = engine
        .install(scope.clone(), None, NOW)
        .await
        .expect("a new login installs the next version");
    let second = OAuthSessionJwt::new(SECRET)
        .verify_at(&relogin.token, NOW)
        .expect("the re-login token strict-verifies");
    assert_ne!(claims.session_id, second.session_id);
    assert_eq!(second.revision, claims.revision + 1);
}

#[tokio::test]
async fn refresh_provider_policy_runs_on_verified_state_before_any_write() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine.install(scope.clone(), None, NOW).await.expect("install");
    let before = identities.current_version(&scope);
    let result = engine.refresh_with_provider_check(&issued.token, NOW, |provider| {
        assert_eq!(provider, PROVIDER);
        false
    }).await;
    assert!(matches!(result, Err(SessionAuthError::Forbidden)));
    assert_eq!(count_op(&identities, "get_session_by_hash"), 1);
    assert_eq!(count_op(&identities, "rotate_session"), 0);
    assert_eq!(identities.current_version(&scope), before);

    // An allowed request reuses its verification read, then rotates exactly once.
    let rotated = engine.refresh_with_provider_check(&issued.token, NOW, |provider| {
        provider == PROVIDER
    }).await.expect("allowed refresh");
    assert_ne!(rotated.token, issued.token);
    assert_eq!(count_op(&identities, "get_session_by_hash"), 2);
    assert_eq!(count_op(&identities, "rotate_session"), 1);
}

#[tokio::test]
async fn refresh_provider_policy_never_sees_invalid_or_unbound_credentials() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine.install(scope, None, NOW).await.expect("install");
    for (token, now) in [
        ("malformed".to_string(), NOW),
        (issued.token, NOW + IDLE_SECS),
        (sign_for_seed("unbound", 1, NOW + IDLE_SECS), NOW),
    ] {
        let result = engine.refresh_with_provider_check(&token, now, |_| {
            panic!("policy must never inspect an unverified provider")
        }).await;
        assert!(matches!(result, Err(SessionAuthError::Invalid)));
    }
    assert_eq!(count_op(&identities, "rotate_session"), 0);
}

#[tokio::test]
async fn allowed_provider_cannot_bypass_a_lost_refresh_cas() {
    let identities = Arc::new(TestSessionIdentity::new());
    let engine = engine_for(&identities, IDLE_SECS);
    let scope = registered_scope(&identities).await;
    let issued = engine.install(scope, None, NOW).await.expect("install");
    identities.force_rotate_conflict();
    let result = engine.refresh_with_provider_check(&issued.token, NOW, |_| true).await;
    assert!(matches!(result, Err(SessionAuthError::Invalid)));
    assert_eq!(count_op(&identities, "get_session_by_hash"), 1);
    assert_eq!(count_op(&identities, "rotate_session"), 1);
}
