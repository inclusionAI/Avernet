//! `AuthSessionRepoPort` conformance harness.
//!
//! Concrete implementations (in-memory, SQLite, MySQL, plugin-API bridge …)
//! call [`auth_session_repo_port_contract_tests`] from a
//! `tests/conformance_auth_session.rs` driver, passing a `&dyn
//! AuthSessionRepoPort` and a fresh [`AuthSessionScope`]. The harness is
//! object-safe and uses only the trait surface, so the same suite covers every
//! implementation.
//!
//! ## The `Ok(0)`-for-missing-scope contract
//!
//! [`AuthSessionRepoPort::read_session_revision`] is the contract's punctuation
//! point between "no row yet" and "row exists". The harness asserts that a
//! scope with no identity row yields `Ok(0)`, NOT `Err`. This choice keeps
//! the missing-row signal out of [`AuthSessionStoreError`]: a missing identity
//! row is not a storage failure and is not auto-created by a blind read; the
//! caller drives install explicitly via [`InstallAuthSession`] with
//! `expected_revision = 0`. A store that returns `Err` for missing rows
//! breaks the contract.
//!
//! ## Why every assertion discriminates the returned enum
//!
//! The harness deliberately checks each write/revoke result against the
//! expected variant (`Applied` vs `Conflict`, `Revoked` vs `NotCurrent`)
//! rather than bare `.is_ok()`. The contract separates *success-typed
//! outcomes* (CAS conflict, not-current) from `AuthSessionStoreError`; a
//! store that conflates them — e.g. returning `Err(Unavailable)` on a
//! conflict, or `Ok(Applied)` on a stale revoke — would pass a bare
//! `.is_ok()` check while violating the contract.

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion,
    AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};

/// Conformance suite for [`AuthSessionRepoPort`].
///
/// Operation sequence (driven against a fresh `scope` so the suite is
/// idempotent across store implementations):
///
/// 1. `read_session_revision` → `Ok(0)` (missing row, NOT an error).
/// 2. `install_login_session` A with `expected_revision = 0` →
///    `Ok(AuthSessionWrite::Applied)`.
/// 3. `get_session_by_hash(scope, hash_a, now)` → `Ok(Some(snapshot))` whose
///    `version.session_id` / `version.token_hash` match A.
/// 4. `rotate_session` A → B → `Ok(AuthSessionWrite::Applied)`.
/// 5. `get_session_by_hash(scope, hash_a, now)` → `Ok(None)` (rotated-away
///    hash is cleared and must not be queryable).
/// 6. `revoke_session(scope, session_b_id)` →
///    `Ok(AuthSessionRevoke::Revoked)` (revoke also bumps revision).
/// 7. `get_session_by_hash(scope, hash_b, now)` → `Ok(None)` (revoked hash
///    is cleared and must not be queryable).
/// 8. `revoke_session(scope, session_b_id)` again →
///    `Ok(AuthSessionRevoke::NotCurrent)` (already cleared, success-typed).
///
/// The harness is detached from JWT signing: it uses opaque `token_hash`
/// and `session_id` strings chosen so the same suite fits any signing
/// strategy. Implementations supply a fresh `scope` per driver run; the
/// harness does NOT call `read_session_revision` on a scope that a previous
/// driver run has populated.
pub async fn auth_session_repo_port_contract_tests(
    repo: &dyn AuthSessionRepoPort,
    scope: AuthSessionScope,
) {
    // 1. Fresh scope yields revision 0 (missing row is NOT an error).
    let initial_revision = repo
        .read_session_revision(&scope)
        .await
        .expect("read_session_revision on a fresh scope must be Ok(0), not Err");
    assert_eq!(
        initial_revision, 0,
        "missing identity row is signaled by revision 0, not by an error"
    );

    // 2. Install session A with expected_revision = 0.
    let session_a_id = "contract-auth-session-a".to_string();
    let hash_a = "contract-auth-hash-a".to_string();
    let install_a_outcome = repo
        .install_login_session(InstallAuthSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: session_a_id.clone(),
                revision: 1,
                token_hash: hash_a.clone(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("install_login_session A must be Ok, not Err");
    assert_eq!(
        install_a_outcome,
        AuthSessionWrite::Applied,
        "install of A against fresh scope must be Applied, not Conflict"
    );

    // 3. get_session_by_hash(A.hash) → Some, snapshot matches A.
    let snapshot_a = repo
        .get_session_by_hash(&scope, &hash_a, 1_000_000_000)
        .await
        .expect("get_session_by_hash A must be Ok, not Err");
    let snapshot_a = snapshot_a.expect("freshly installed hash A must resolve to a live session");
    assert_eq!(snapshot_a.scope, scope, "snapshot scope matches the requested scope");
    assert_eq!(
        snapshot_a.version.session_id, session_a_id,
        "snapshot carries the installed session_id"
    );
    assert_eq!(
        snapshot_a.version.token_hash, hash_a,
        "snapshot carries the installed token_hash"
    );
    assert_eq!(
        snapshot_a.version.revision, 1,
        "installed snapshot reports revision 1"
    );

    // 4. Rotate A → B (expected = A's version).
    let session_b_id = "contract-auth-session-b".to_string();
    let hash_b = "contract-auth-hash-b".to_string();
    let rotate_b_outcome = repo
        .rotate_session(RotateAuthSession {
            scope: scope.clone(),
            expected: AuthSessionVersion {
                session_id: session_a_id.clone(),
                revision: 1,
                token_hash: hash_a.clone(),
            },
            next: AuthSessionVersion {
                session_id: session_b_id.clone(),
                revision: 2,
                token_hash: hash_b.clone(),
            },
            expires_at: 6_000_000_000,
            now: 2_000_000_000,
        })
        .await
        .expect("rotate_session B must be Ok, not Err");
    assert_eq!(
        rotate_b_outcome,
        AuthSessionWrite::Applied,
        "rotate to B against the live A version must be Applied, not Conflict"
    );

    // 5. get_session_by_hash(A.hash) → None (A no longer live, cleared by rotate).
    let stale_a = repo
        .get_session_by_hash(&scope, &hash_a, 2_000_000_001)
        .await
        .expect("get_session_by_hash for rotated-away hash A must be Ok, not Err");
    assert!(
        stale_a.is_none(),
        "rotated-away hash A must not be queryable (Ok(None))"
    );

    // 6. Revoke session_id=B → Revoked (revoke also bumps revision).
    let revoke_b_outcome = repo
        .revoke_session(&scope, &session_b_id)
        .await
        .expect("revoke_session on live session_b must be Ok, not Err");
    assert_eq!(
        revoke_b_outcome,
        AuthSessionRevoke::Revoked,
        "revoke on the live session_b must be Revoked, not NotCurrent"
    );

    // 7. get_session_by_hash(B.hash) → None (revoked hash is cleared).
    let stale_b = repo
        .get_session_by_hash(&scope, &hash_b, 2_000_000_002)
        .await
        .expect("get_session_by_hash for revoked hash B must be Ok, not Err");
    assert!(
        stale_b.is_none(),
        "revoked hash B must not be queryable after revoke (Ok(None))"
    );

    // 8. Revoke same session_id again → NotCurrent.
    let revoke_again_outcome = repo
        .revoke_session(&scope, &session_b_id)
        .await
        .expect("repeat revoke_session must be Ok, not Err");
    assert_eq!(
        revoke_again_outcome,
        AuthSessionRevoke::NotCurrent,
        "revoke on already-cleared session_b must be NotCurrent, not Revoked"
    );
}
