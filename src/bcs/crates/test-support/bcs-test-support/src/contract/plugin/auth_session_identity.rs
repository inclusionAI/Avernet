//! `AuthSessionIdentityPort` conformance harness.
//!
//! Concrete bridges (the bootstrap `RepoAuthSessionIdentityPort`,
//! memory-backed stand-ins, future plugin-API bridges) call
//! [`auth_session_identity_port_contract_tests`] from a
//! `tests/conformance_auth_session_identity.rs` driver, passing a `&dyn
//! AuthSessionIdentityPort` and a fresh [`SessionScope`]. The harness is
//! object-safe and uses only the trait surface, so the same suite covers
//! every implementation that targets the strict identity port.
//!
//! ## Why this harness is parallel to the `auth_session_repo_port_contract_tests`
//!
//! The strict `AuthSessionIdentityPort` (in `bcs-auth-api`) has the SAME
//! parameter/result semantics as `AuthSessionRepoPort` (in `bcs-service-api`)
//! plus an extra `ensure_identity` method. The repo-side harness exercises
//! the persistence layer directly; this harness exercises the bridge
//! surface that auth plugins consume. The two harnesses share the
//! `Ok(0)`-for-missing-scope contract and the CAS conflict / `NotCurrent`
//! success-typed-enum contract, but assert them through the strict port's
//! own `Session*` types. The harness additionally covers
//! `ensure_identity` create-or-confirm (the legacy idempotent path the
//! repo port does not have).
//!
//! ## `ensure_identity` create-or-confirm
//!
//! The harness asserts two calls to `ensure_identity` with the same
//! `(provider, external_user_id, env)` and different `(name, avatar)`
//! refresh values return the SAME `user_id` (the call is idempotent
//! create-or-confirm; confirmation refreshes the display fields but never
//! allocates a second row). The display-field refresh is checked via the
//! post-install `get_session_by_hash` snapshot's `display_name`/`avatar`:
//! the harness installs a session after the refresh call and asserts the
//! snapshot reflects the refreshed names.
//!
//! ## DB-fault classification is NOT part of this generic harness
//!
//! Fault-injection (`Unavailable` mapping under storage failure) is
//! specific to the concrete store/bridge combination, so it lives in the
//! consumer `conformance_auth_session_identity.rs` test, NOT here. This
//! harness stays object-safe and store-agnostic.

use bcs_auth_api::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionVersion, SessionWrite,
};

/// Conformance suite for [`AuthSessionIdentityPort`].
///
/// Operation sequence (driven against a fresh `scope` so the suite is
/// idempotent across bridge implementations):
///
/// 1. `read_session_revision` → `Ok(0)` (missing row, NOT an error).
/// 2. `install_login_session` A with `expected_revision = 0` →
///    `Ok(SessionWrite::Applied)`.
/// 3. `get_session_by_hash(scope, hash_a, now)` → `Ok(Some(snapshot))`
///    whose `version.session_id` / `version.token_hash` match A.
/// 4. `rotate_session` A → B → `Ok(SessionWrite::Applied)`.
/// 5. `get_session_by_hash(scope, hash_a, now)` → `Ok(None)` (rotated-away
///    hash is cleared and must not be queryable).
/// 6. `revoke_session(scope, session_b_id)` →
///    `Ok(SessionRevoke::Revoked)`.
/// 7. `get_session_by_hash(scope, hash_b, now)` → `Ok(None)`.
/// 8. `revoke_session(scope, session_b_id)` again →
///    `Ok(SessionRevoke::NotCurrent)`.
/// 9. `ensure_identity` create → returns a `user_id`; the same call again
///    with refreshed `name`/`avatar` returns the SAME `user_id`.
/// 10. Post-refresh display fields surface on the next installed session's
///     snapshot (`display_name`/`avatar` reflect the refresh).
/// 11. Cross-revision: an install against a stale `expected_revision`
///     returns `Ok(SessionWrite::Conflict)`, never `Err`.
/// 12. Revoking an unknown `session_id` returns
///     `Ok(SessionRevoke::NotCurrent)`, never `Err`. An unknown hash
///     returns `Ok(None)`, never `Err`.
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn auth_session_identity_port_contract_tests(
    port: &dyn AuthSessionIdentityPort,
    scope: SessionScope,
) {
    // 1. Fresh scope yields revision 0 (missing row is NOT an error).
    let initial_revision = port
        .read_session_revision(&scope)
        .await
        .expect("read_session_revision on a fresh scope must be Ok(0), not Err");
    assert_eq!(
        initial_revision, 0,
        "missing identity row is signaled by revision 0, not by an error"
    );

    // 2. Install session A with expected_revision = 0.
    let session_a_id = "contract-identity-session-a".to_string();
    let hash_a = "contract-identity-hash-a".to_string();
    let install_a_outcome = port
        .install_login_session(InstallSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: SessionVersion {
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
        SessionWrite::Applied,
        "install of A against fresh scope must be Applied, not Conflict"
    );

    // 3. get_session_by_hash(A.hash) → Some, snapshot matches A.
    let snapshot_a = port
        .get_session_by_hash(&scope, &hash_a, 1_000_000_000)
        .await
        .expect("get_session_by_hash A must be Ok, not Err")
        .expect("freshly installed hash A must resolve to a live session");
    assert_eq!(
        snapshot_a.scope, scope,
        "snapshot scope matches the requested scope"
    );
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
    let session_b_id = "contract-identity-session-b".to_string();
    let hash_b = "contract-identity-hash-b".to_string();
    let rotate_b_outcome = port
        .rotate_session(RotateSession {
            scope: scope.clone(),
            expected: SessionVersion {
                session_id: session_a_id.clone(),
                revision: 1,
                token_hash: hash_a.clone(),
            },
            next: SessionVersion {
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
        SessionWrite::Applied,
        "rotate to B against the live A version must be Applied, not Conflict"
    );

    // 5. get_session_by_hash(A.hash) → None (A no longer live, cleared by rotate).
    let stale_a = port
        .get_session_by_hash(&scope, &hash_a, 2_000_000_001)
        .await
        .expect("get_session_by_hash for rotated-away hash A must be Ok, not Err");
    assert!(
        stale_a.is_none(),
        "rotated-away hash A must not be queryable (Ok(None))"
    );

    // 6. Revoke session_id=B → Revoked.
    let revoke_b_outcome = port
        .revoke_session(&scope, &session_b_id)
        .await
        .expect("revoke_session on live session_b must be Ok, not Err");
    assert_eq!(
        revoke_b_outcome,
        SessionRevoke::Revoked,
        "revoke on the live session_b must be Revoked, not NotCurrent"
    );

    // 7. get_session_by_hash(B.hash) → None (revoked hash is cleared).
    let stale_b = port
        .get_session_by_hash(&scope, &hash_b, 2_000_000_002)
        .await
        .expect("get_session_by_hash for revoked hash B must be Ok, not Err");
    assert!(
        stale_b.is_none(),
        "revoked hash B must not be queryable after revoke (Ok(None))"
    );

    // 8. Revoke same session_id again → NotCurrent.
    let revoke_again_outcome = port
        .revoke_session(&scope, &session_b_id)
        .await
        .expect("repeat revoke_session must be Ok, not Err");
    assert_eq!(
        revoke_again_outcome,
        SessionRevoke::NotCurrent,
        "revoke on already-cleared session_b must be NotCurrent, not Revoked"
    );

    // 9. ensure_identity create-or-confirm: same external id twice → same user_id,
    //    with refreshed `name`/`avatar`. Use a dedicated provider so we don't
    //    clash with the fixture's `scope.provider` if a test reuses the
    //    scope (the harness uses a fresh scope per driver).
    let external_user_id = "contract-identity-external";
    let user_id_first = port
        .ensure_identity(
            &scope.provider,
            external_user_id,
            Some("Initial Name"),
            Some("https://avatar.invalid/initial.png"),
            &scope.env,
        )
        .await
        .expect("ensure_identity (create) must be Ok, not Err");
    // Internal user_id is a non-empty string and is NOT the external one
    // (the legacy allocate-or-confirm path).
    assert!(
        !user_id_first.is_empty(),
        "ensure_identity must return a non-empty internal user_id"
    );
    assert_ne!(
        user_id_first, external_user_id,
        "ensure_identity returns the INTERNAL user_id, not the external one"
    );

    let user_id_second = port
        .ensure_identity(
            &scope.provider,
            external_user_id,
            Some("Refreshed Name"),
            Some("https://avatar.invalid/refreshed.png"),
            &scope.env,
        )
        .await
        .expect("ensure_identity (confirm) must be Ok, not Err");
    assert_eq!(
        user_id_first, user_id_second,
        "ensure_identity with the same external_id must return the same user_id (create-or-confirm)"
    );

    // 10. Post-refresh display fields surface on the next installed session's
    //     snapshot for that scope. Install a fresh session under the
    //     user/external-id just confirmed and read the display snapshot.
    let refresh_scope = SessionScope {
        user_id: user_id_first.clone(),
        provider: scope.provider.clone(),
        env: scope.env.clone(),
    };
    // The refresh scope is a fresh identity row's row (it may already exist
    // if the harness scope's user_id was ever ensure_identity'd by the test
    // driver; we use a disjoint user_id so read_session_revision yields 0).
    let refresh_initial = port
        .read_session_revision(&refresh_scope)
        .await
        .expect("read_session_revision on the refresh scope must be Ok");
    assert_eq!(
        refresh_initial, 0,
        "the refresh scope must start at revision 0 before install"
    );
    let hash_refresh = "contract-identity-hash-refresh".to_string();
    let session_refresh_id = "contract-identity-session-refresh".to_string();
    port.install_login_session(InstallSession {
        scope: refresh_scope.clone(),
        expected_revision: 0,
        next: SessionVersion {
            session_id: session_refresh_id.clone(),
            revision: 1,
            token_hash: hash_refresh.clone(),
        },
        expires_at: 7_000_000_000,
    })
    .await
    .expect("install_login_session for refresh scope must be Ok, not Err");
    let refreshed_snapshot = port
        .get_session_by_hash(&refresh_scope, &hash_refresh, 3_000_000_000)
        .await
        .expect("get_session_by_hash for refresh scope must be Ok, not Err")
        .expect("refresh hash must resolve to a live session");
    assert_eq!(
        refreshed_snapshot.display_name.as_deref(),
        Some("Refreshed Name"),
        "snapshot carries the refreshed external_user_name as display_name"
    );
    assert_eq!(
        refreshed_snapshot.avatar.as_deref(),
        Some("https://avatar.invalid/refreshed.png"),
        "snapshot carries the refreshed avatar"
    );

    // 11. Cross-revision rejection: install against a stale expected_revision
    //     (the refresh scope is now at revision 1) MUST return
    //     `Ok(SessionWrite::Conflict)`, never `Err`.
    let stale_outcome = port
        .install_login_session(InstallSession {
            scope: refresh_scope.clone(),
            expected_revision: 0, // live is revision 1; CAS fails
            next: SessionVersion {
                session_id: "conflict-sid".to_string(),
                revision: 1,
                token_hash: "conflict-hash".to_string(),
            },
            expires_at: 8_000_000_000,
        })
        .await
        .expect("stale install must be Ok, never Err");
    assert_eq!(
        stale_outcome,
        SessionWrite::Conflict,
        "install against a stale revision must be Conflict, never Err"
    );

    // 12. Revoking an unknown session_id is `NotCurrent`, never `Err`. An
    //     unknown hash returns `Ok(None)`, never `Err`.
    let unknown_revoke = port
        .revoke_session(&scope, "sid-never-existed")
        .await
        .expect("revoke_session on unknown sid must be Ok, never Err");
    assert_eq!(
        unknown_revoke,
        SessionRevoke::NotCurrent,
        "revoke on a never-existed session_id must be NotCurrent"
    );
    let unknown_hash = port
        .get_session_by_hash(&scope, "hash-never-existed", 1_000_000_000)
        .await
        .expect("get_session_by_hash for unknown hash must be Ok, never Err");
    assert!(
        unknown_hash.is_none(),
        "query for an unknown hash must be Ok(None), never Err"
    );
}
