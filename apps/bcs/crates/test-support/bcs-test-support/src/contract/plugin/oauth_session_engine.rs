//! `OAuthSessionEngine` lifecycle conformance harness (V1 API auth plugin
//! chain).
//!
//! Drivers pass the HMAC secret, a strict `AuthSessionIdentityPort` (the
//! in-crate test double, or Task 13's repository bridge over a real
//! store), the injected environment, and the idle window; the harness
//! drives the engine's public surface and pins the binding lifecycle
//! semantics from plan §8.5:
//!
//! - a NEW LOGIN installs a random session id at the freshly read revision
//!   and issues the credential only AFTER the CAS applies;
//! - same-`now` consecutive refreshes produce DIFFERENT tokens (the
//!   revision lives in the claims) while PRESERVING the session id, and
//!   every rotated-out JWT stops verifying (no binding);
//! - `revoke(old)` does not require the old hash to still be live and
//!   revokes BY SID — killing the rotated descendant with its ancestor;
//! - a repeated revoke of the dead instance is an idempotent `NotCurrent`.
//!
//! Signing keys never appear in assertions or messages; failures carry
//! only fixed categories.
//!
//! Real store/bridge coverage (Task 13) repeats this exact sequence by
//! calling [`oauth_session_engine_contract_tests`] with the production
//! bridge over a real persistence repo.

use std::sync::Arc;

use bcs_auth_api::session::{
    AuthSessionIdentityPort, SessionAuthError, SessionRevoke, SessionScope,
};
use bcs_auth_oauth::OAuthSessionEngine;
use bcs_jwt::OAuthSessionJwt;

/// Drive the pinned install → refresh*2 → revoke lifecycle through the
/// strict session engine.
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn oauth_session_engine_contract_tests(
    secret: &str,
    identities: Arc<dyn AuthSessionIdentityPort>,
    env: String,
    idle_secs: u64,
) {
    const NOW: u64 = 1_800_000_000;
    const PROVIDER: &str = "engine-contract-provider";
    const EXT_USER: &str = "engine-contract-ext-1";
    const DISPLAY_NAME: &str = "Engine 契约用户";

    // 1. NEW LOGIN identity via the strict port (never an Option lookup).
    let user_id = identities
        .ensure_identity(PROVIDER, EXT_USER, Some(DISPLAY_NAME), None, &env)
        .await
        .expect("ensure_identity creates the login identity");
    assert!(!user_id.is_empty());

    let engine = OAuthSessionEngine::new(
        OAuthSessionJwt::new(secret),
        identities,
        env.clone(),
        idle_secs,
    );

    // Scope values are the trusted internal id + configured provider/env,
    // never guessed from a JWT.
    let scope = SessionScope {
        user_id: user_id.clone(),
        provider: PROVIDER.to_string(),
        env: env.clone(),
    };

    // 2. Install session version A against the freshly read revision 0.
    let issued_a = engine
        .install(scope.clone(), Some(DISPLAY_NAME.to_string()), NOW)
        .await
        .expect("install applies for the fresh scope");
    assert_eq!(
        issued_a.expires_at,
        NOW + idle_secs,
        "the trusted window is now plus the injected idle seconds"
    );
    let snapshot_a = engine
        .verify(&issued_a.token, NOW)
        .await
        .expect("session A verifies right after install");
    assert_eq!(snapshot_a.scope, scope);
    assert_eq!(snapshot_a.version.revision, 1, "a fresh session starts at revision 1");
    assert_eq!(snapshot_a.expires_at, issued_a.expires_at);

    // 3. Two consecutive refreshes AT THE SAME `now`: the revision in the
    // claims makes every issue a different token even in the same second.
    let issued_b = engine
        .refresh(&issued_a.token, NOW)
        .await
        .expect("the first same-second refresh applies");
    let issued_c = engine
        .refresh(&issued_b.token, NOW)
        .await
        .expect("the second same-second refresh applies");
    assert_ne!(issued_a.token, issued_b.token, "same-`now` refresh must not reproduce the token");
    assert_ne!(issued_b.token, issued_c.token, "same-`now` refresh must advance the token");
    assert_ne!(issued_a.token, issued_c.token);

    // 4. Rotated-out JWTs carry no binding: they no longer verify.
    assert!(
        matches!(engine.verify(&issued_a.token, NOW).await, Err(SessionAuthError::Invalid)),
        "the rotated-out token A has no binding left"
    );
    assert!(
        matches!(engine.verify(&issued_b.token, NOW).await, Err(SessionAuthError::Invalid)),
        "the rotated-out token B has no binding left"
    );

    let snapshot_c = engine
        .verify(&issued_c.token, NOW)
        .await
        .expect("the newest JWT verifies after the rotations");
    assert_eq!(
        snapshot_c.version.revision, 3,
        "each successful refresh bumps the stored revision"
    );
    assert_eq!(
        snapshot_c.version.session_id, snapshot_a.version.session_id,
        "refresh PRESERVES the session id"
    );
    // The issued token and the trusted record name the SAME atomic version;
    // the claims are checked against the snapshot, not trusted alone.
    let claims_c = OAuthSessionJwt::new(secret)
        .verify_at(&issued_c.token, NOW)
        .expect("the issued JWT strict-verifies");
    assert_eq!(claims_c.revision, snapshot_c.version.revision);
    assert_eq!(claims_c.session_id, snapshot_c.version.session_id);
    assert_eq!(claims_c.sub, user_id);

    // 5. revoke(old) — the ORIGINAL token whose hash was rotated away —
    // still revokes by sid and kills the newest rotated descendant.
    assert_eq!(
        engine
            .revoke(&issued_a.token)
            .await
            .expect("revoke of the old instance is an outcome, not an error"),
        SessionRevoke::Revoked,
        "revoke does not require the old hash to still be live"
    );
    assert!(
        matches!(engine.verify(&issued_c.token, NOW).await, Err(SessionAuthError::Invalid)),
        "the rotated descendant dies with its revoked ancestor"
    );
    assert!(matches!(
        engine.refresh(&issued_c.token, NOW).await,
        Err(SessionAuthError::Invalid)
    ));

    // 6. Repeating the revoke on the dead instance is idempotent.
    assert_eq!(
        engine
            .revoke(&issued_a.token)
            .await
            .expect("a repeat revoke is an outcome, not an error"),
        SessionRevoke::NotCurrent
    );
}
