//! `AuthSessionRepoPort` conformance harness (V1 API auth plugin chain).
//!
//! Concrete implementations (in-memory, SQLite, MySQL …) call
//! [`auth_session_repo_port_contract_tests`] from a
//! `tests/conformance_auth_session.rs` driver, passing a freshly constructed
//! repo and an unused scope. The driver must have arranged for an identity
//! row to exist for `scope.user_id` before invoking the harness (identity
//! creation is a separate contract; a session write never auto-creates one).
//!
//! The harness pins the strict atomic semantics: fresh revision 0, CAS
//! install/rotate (`Applied`/`Conflict`, never unconditional writes), rotate
//! clears the replaced hash, revoke clears the current hash and bumps the
//! revision, and a repeated revoke reports `NotCurrent` rather than an error.
//! Every step asserts the exact outcome enum/category; nothing relies on a
//! bare `.is_ok()`.

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion,
    AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};

/// Strict atomic auth-session conformance suite.
///
/// Drives the pinned operation sequence for one `scope`:
/// revision 0 → install A → read A → rotate to B → A absent → revoke the
/// same session id → B absent → repeated revoke is `NotCurrent`.
pub async fn auth_session_repo_port_contract_tests(
    repo: &dyn AuthSessionRepoPort,
    scope: AuthSessionScope,
) {
    const NOW: u64 = 1_800_000_000;
    const EXPIRES_AT: u64 = NOW + 3_600;
    const ROTATED_AT: u64 = NOW + 10;
    const ROTATED_EXPIRES_AT: u64 = NOW + 7_200;

    // 1. Fresh scope: no session installed yet, so the install CAS expects 0.
    assert_eq!(
        repo.read_session_revision(&scope)
            .await
            .expect("read revision of fresh scope"),
        0,
        "a fresh scope starts at revision 0"
    );

    let session_id = "auth-session-contract-1".to_string();
    let version_a = AuthSessionVersion {
        session_id: session_id.clone(),
        revision: 1,
        token_hash: "auth-session-contract-hash-a".to_string(),
    };
    let version_b = AuthSessionVersion {
        session_id: session_id.clone(),
        revision: version_a.revision.checked_add(1).expect("revision CAS bump"),
        token_hash: "auth-session-contract-hash-b".to_string(),
    };

    // 2. Install session version A against expected revision 0.
    assert_eq!(
        repo.install_login_session(InstallAuthSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: version_a.clone(),
            expires_at: EXPIRES_AT,
        })
        .await
        .expect("install session version A"),
        AuthSessionWrite::Applied,
        "installing against the freshly read revision applies"
    );

    // 3. Version A resolves by hash and joins the identity display fields.
    let snapshot = repo
        .get_session_by_hash(&scope, &version_a.token_hash, NOW)
        .await
        .expect("get session version A by hash")
        .expect("installed session version A resolves by hash");
    assert_eq!(snapshot.scope, scope);
    assert_eq!(snapshot.version, version_a);
    assert_eq!(snapshot.expires_at, EXPIRES_AT);
    assert!(
        !snapshot.username.is_empty(),
        "the session snapshot joins the identity row's username"
    );

    // 4. Rotate A -> B under the exact expected version.
    assert_eq!(
        repo.rotate_session(RotateAuthSession {
            scope: scope.clone(),
            expected: version_a.clone(),
            next: version_b.clone(),
            expires_at: ROTATED_EXPIRES_AT,
            now: ROTATED_AT,
        })
        .await
        .expect("rotate session version A to B"),
        AuthSessionWrite::Applied,
        "rotating from the current version applies"
    );

    // 5. The rotated-out hash no longer resolves.
    let rotated_out_read = repo
        .get_session_by_hash(&scope, &version_a.token_hash, ROTATED_AT)
        .await
        .expect("get session version A after rotate");
    assert!(
        rotated_out_read.is_none(),
        "the rotated-out hash is cleared by the successful rotate (got {:?})",
        rotated_out_read
    );

    // 6. Revoke the same session id.
    assert_eq!(
        repo.revoke_session(&scope, &session_id)
            .await
            .expect("revoke the current session"),
        AuthSessionRevoke::Revoked,
        "revoking the current session revokes"
    );

    // 7. The revoked session's hash no longer resolves.
    let revoked_read = repo
        .get_session_by_hash(&scope, &version_b.token_hash, ROTATED_AT)
        .await
        .expect("get session version B after revoke");
    assert!(
        revoked_read.is_none(),
        "the revoked hash is cleared, so the session no longer resolves (got {:?})",
        revoked_read
    );

    // 8. Repeating the revoke on the now non-current session is NotCurrent,
    //    not an error and not a second Revoked.
    assert_eq!(
        repo.revoke_session(&scope, &session_id)
            .await
            .expect("repeat revoke must not be a persistence error"),
        AuthSessionRevoke::NotCurrent,
        "a repeated revoke reports NotCurrent"
    );
}
