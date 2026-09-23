#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

//! Conformance + deterministic race tests for the in-memory
//! `AuthSessionRepoPort` implementation in
//! `bcs-user-identity::session_memory`.
//!
//! The driver test calls the central `bcs-test-support` harness with a real
//! `MemoryUserIdentityRepo` whose `scope` is obtained from a real legacy
//! `ensure_identity` call. The rest of this file exercises the plan's race
//! sequences:
//!
//! 1. rotate → revoke-by-`session_id` → late rotate Conflict (clock-free verbatim
//!    snippet from the task brief).
//! 2. New login C vs. old logout — the new install overrides the old session;
//!    the old rotate and old logout do not affect C.
//! 3. Simultaneous double refresh with `tokio::sync::Barrier` — exactly one
//!    `Applied` and one `Conflict`, no `sleep`.
//! 4. Expiry edge cases (now < expires_at → Some; now = expires_at → None; now >
//!    expires_at → None; rotate after expiry → Conflict).
//! 5. Cross-env scope isolation (same external identity in two envs yields two
//!    independent sessions; revoking one does not affect the other).
//! 6. Install CAS conflict (install pretending the revision is still 0 after a
//!    prior install).
//! 7. Revision-counter hardening — impl ignores `next.revision` and writes
//!    `expected.checked_add(1)`.
//!
//! Revision-overflow → `CorruptRecord` mapping is asserted only conceptually:
//! the public contract exposes u64 revisions and bumping `u64::MAX + 1`
//! through the public API is infeasible; per the brief, no public seam is
//! added solely for this assertion. See `session_memory`'s doc comment for
//! the `checked_add` invariant.

use std::sync::Arc;

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion, AuthSessionWrite,
    InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_user_identity::MemoryUserIdentityRepo;
use tokio::sync::Barrier;

/// Build a real `AuthSessionScope` from a fresh `ensure_identity` call so the
/// session API has a live identity row to attach to.
async fn fresh_scope(
    repo: &MemoryUserIdentityRepo,
    auth_source: &str,
    external_user_id: &str,
    external_user_name: Option<&str>,
    avatar: Option<&str>,
    env: &str,
) -> AuthSessionScope {
    let user_id = repo
        .ensure_identity(auth_source, external_user_id, external_user_name, avatar, env)
        .await
        .expect("ensure_identity must succeed");
    AuthSessionScope {
        user_id,
        provider: auth_source.to_string(),
        env: env.to_string(),
    }
}

#[tokio::test]
async fn memory_repo_passes_contract_harness() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = fresh_scope(&repo, "cookie", "ext-harness", Some("alice"), None, "dev").await;
    bcs_test_support::contract::repo::auth_session::auth_session_repo_port_contract_tests(
        &repo, scope,
    )
    .await;
}

#[tokio::test]
async fn rotate_then_revoke_by_sid_kills_session_then_late_rotate_conflicts() {
    // Verbatim-from-plan sequence: same_sid is A's session_id; B is revision+1
    // with the SAME session_id and next token hash; rotate succeeds, revoke by
    // the session_id kills B too (revoke is per-session-instance, not per-hash),
    // the just-rotated hash is no longer queryable, and a late rotate with the
    // stale expected version conflicts.
    let repo = MemoryUserIdentityRepo::new();
    let scope = fresh_scope(&repo, "cookie", "ext-rotate", None, None, "dev").await;

    let same_sid = "sid-a".to_string();
    let hash_a = "hash-a".to_string();
    let hash_b = "hash-b".to_string();

    let install_a = repo
        .install_login_session(InstallAuthSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: same_sid.clone(),
                revision: 1,
                token_hash: hash_a,
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("install A must be Ok, not Err");
    assert_eq!(
        install_a,
        AuthSessionWrite::Applied,
        "install A against fresh scope must be Applied, not Conflict"
    );

    let to_b = RotateAuthSession {
        scope: scope.clone(),
        expected: AuthSessionVersion {
            session_id: same_sid.clone(),
            revision: 1,
            token_hash: "hash-a".to_string(),
        },
        next: AuthSessionVersion {
            session_id: same_sid.clone(),
            revision: 2,
            token_hash: hash_b.clone(),
        },
        expires_at: 6_000_000_000,
        now: 2_000_000_000,
    };

    assert_eq!(
        repo.rotate_session(to_b.clone()).await.unwrap(),
        AuthSessionWrite::Applied,
        "rotate A→B against the live A version must be Applied"
    );
    assert_eq!(
        repo.revoke_session(&scope, &same_sid).await.unwrap(),
        AuthSessionRevoke::Revoked,
        "revoke by A's session_id (now bound to B) must be Revoked"
    );
    assert!(
        repo.get_session_by_hash(&scope, &to_b.next.token_hash, to_b.now)
            .await
            .unwrap()
            .is_none(),
        "after revoke, B's hash must not be queryable"
    );
    assert_eq!(
        repo.rotate_session(to_b).await.unwrap(),
        AuthSessionWrite::Conflict,
        "a late rotate with the stale expected version A must Conflict"
    );
}

#[tokio::test]
async fn new_login_after_old_does_not_allow_old_logout_to_clear_new() {
    // Plan's "new login C vs old logout" scenario: install A, then install C
    // (a fresh login reads revision=1 and CAS-installs at expected=1).
    // The old rotate fails Conflict; the old logout returns NotCurrent; C's
    // hash is still queryable.
    let repo = MemoryUserIdentityRepo::new();
    let scope = fresh_scope(&repo, "cookie", "ext-c", None, None, "dev").await;

    let sid_a = "sid-a".to_string();
    let hash_a = "hash-a".to_string();
    let sid_c = "sid-c".to_string();
    let hash_c = "hash-c".to_string();

    repo.install_login_session(InstallAuthSession {
        scope: scope.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: sid_a.clone(),
            revision: 1,
            token_hash: hash_a.clone(),
        },
        expires_at: 5_000_000_000,
    })
    .await
    .unwrap();

    // Caller's next.revision is intentionally wrong (99); impl writes
    // expected.checked_add(1) = 2 regardless.
    repo.install_login_session(InstallAuthSession {
        scope: scope.clone(),
        expected_revision: 1,
        next: AuthSessionVersion {
            session_id: sid_c.clone(),
            revision: 99,
            token_hash: hash_c.clone(),
        },
        expires_at: 5_500_000_000,
    })
    .await
    .unwrap();

    let stale_rotate = RotateAuthSession {
        scope: scope.clone(),
        expected: AuthSessionVersion {
            session_id: sid_a.clone(),
            revision: 1,
            token_hash: hash_a.clone(),
        },
        next: AuthSessionVersion {
            session_id: sid_a.clone(),
            revision: 2,
            token_hash: "hash-a2".to_string(),
        },
        expires_at: 6_000_000_000,
        now: 3_000_000_000,
    };
    assert_eq!(
        repo.rotate_session(stale_rotate).await.unwrap(),
        AuthSessionWrite::Conflict,
        "old session's rotate must fail Conflict now that C holds the scope"
    );

    assert_eq!(
        repo.revoke_session(&scope, &sid_a).await.unwrap(),
        AuthSessionRevoke::NotCurrent,
        "revoke of the old session_id must be NotCurrent (C lives under a different sid)"
    );

    let snapshot = repo
        .get_session_by_hash(&scope, &hash_c, 3_000_000_001)
        .await
        .unwrap()
        .expect("C must remain live and queryable");
    assert_eq!(snapshot.version.session_id, sid_c);
    assert_eq!(snapshot.version.token_hash, hash_c);
    assert_eq!(
        snapshot.version.revision, 2,
        "stored revision must be 2 (expected_revision + 1), not the caller's next.revision=99"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn double_refresh_with_barrier_yields_exactly_one_applied() {
    // Two concurrent rotate_session calls against the SAME expected version.
    // Determinism target: exactly one Applied and one Conflict, regardless of
    // which task wins the lock. No sleeps.
    let repo = Arc::new(MemoryUserIdentityRepo::new());
    let scope = fresh_scope(&repo, "cookie", "ext-race", None, None, "dev").await;

    let sid_x = "sid-x".to_string();
    let hash_x = "hash-x".to_string();
    repo.install_login_session(InstallAuthSession {
        scope: scope.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: sid_x.clone(),
            revision: 1,
            token_hash: hash_x.clone(),
        },
        expires_at: 5_000_000_000,
    })
    .await
    .unwrap();

    let barrier = Arc::new(Barrier::new(2));
    let mut handles = Vec::new();
    for i in 0..2u8 {
        let repo = repo.clone();
        let scope = scope.clone();
        let barrier = barrier.clone();
        let sid_x = sid_x.clone();
        let hash_x = hash_x.clone();
        let next_hash = format!("hash-y-{i}");
        handles.push(tokio::spawn(async move {
            barrier.wait().await;
            repo.rotate_session(RotateAuthSession {
                scope,
                expected: AuthSessionVersion {
                    session_id: sid_x,
                    revision: 1,
                    token_hash: hash_x,
                },
                next: AuthSessionVersion {
                    session_id: "sid-x".to_string(),
                    revision: 2,
                    token_hash: next_hash,
                },
                expires_at: 6_000_000_000,
                now: 2_000_000_000,
            })
            .await
            .unwrap()
        }));
    }
    let r1 = handles.remove(0).await.unwrap();
    let r2 = handles.remove(0).await.unwrap();
    let applied = [r1.clone(), r2.clone()]
        .iter()
        .filter(|r| **r == AuthSessionWrite::Applied)
        .count();
    let conflict = [r1, r2]
        .iter()
        .filter(|r| **r == AuthSessionWrite::Conflict)
        .count();
    assert_eq!(applied, 1, "exactly one refresh must be Applied");
    assert_eq!(conflict, 1, "exactly one refresh must be Conflict");

    // One of the two next hashes (hash-y-0 or hash-y-1) is the live hash; the
    // other is non-queryable. Exactly one and not both.
    let live_y = repo
        .get_session_by_hash(&scope, "hash-y-0", 2_000_000_001)
        .await
        .unwrap();
    let live_y1 = repo
        .get_session_by_hash(&scope, "hash-y-1", 2_000_000_001)
        .await
        .unwrap();
    assert!(
        live_y.is_some() ^ live_y1.is_some(),
        "exactly one of the two rotated hashes must be live (got {:?} and {:?})",
        live_y.map(|s| s.version.token_hash),
        live_y1.map(|s| s.version.token_hash)
    );
}

#[tokio::test]
async fn expiry_edge_cases() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = fresh_scope(&repo, "cookie", "ext-exp", None, None, "dev").await;

    let sid = "sid-exp".to_string();
    let hash = "hash-exp".to_string();
    repo.install_login_session(InstallAuthSession {
        scope: scope.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: sid.clone(),
            revision: 1,
            token_hash: hash.clone(),
        },
        expires_at: 1_000,
    })
    .await
    .unwrap();

    // Boundary checks: strict `stored_expires_at > now`.
    assert!(
        repo.get_session_by_hash(&scope, &hash, 500)
            .await
            .unwrap()
            .is_some(),
        "now < expires_at → Some"
    );
    assert!(
        repo.get_session_by_hash(&scope, &hash, 1_000)
            .await
            .unwrap()
            .is_none(),
        "now = expires_at → None (strictly greater)"
    );
    assert!(
        repo.get_session_by_hash(&scope, &hash, 1_500)
            .await
            .unwrap()
            .is_none(),
        "now > expires_at → None"
    );

    // Rotate after the stored expiry must Conflict.
    assert_eq!(
        repo.rotate_session(RotateAuthSession {
            scope: scope.clone(),
            expected: AuthSessionVersion {
                session_id: sid.clone(),
                revision: 1,
                token_hash: hash.clone(),
            },
            next: AuthSessionVersion {
                session_id: sid.clone(),
                revision: 2,
                token_hash: "hash-exp-2".to_string(),
            },
            expires_at: 2_000,
            now: 1_500,
        })
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "rotate after expiry must Conflict"
    );

    // Rotate inside the window succeeds.
    assert_eq!(
        repo.rotate_session(RotateAuthSession {
            scope: scope.clone(),
            expected: AuthSessionVersion {
                session_id: sid.clone(),
                revision: 1,
                token_hash: hash.clone(),
            },
            next: AuthSessionVersion {
                session_id: sid.clone(),
                revision: 2,
                token_hash: "hash-exp-2".to_string(),
            },
            expires_at: 2_000,
            now: 500,
        })
        .await
        .unwrap(),
        AuthSessionWrite::Applied,
        "rotate within the expiry window must be Applied"
    );

    // After Applied rotate, the old hash is no longer queryable and the new
    // one is.
    assert!(
        repo.get_session_by_hash(&scope, "hash-exp", 1_500)
            .await
            .unwrap()
            .is_none(),
        "old hash must not be queryable after rotate"
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-exp-2", 1_500)
            .await
            .unwrap()
            .is_some(),
        "new hash must be queryable after rotate"
    );

    // Revoke on a live (not-yet-expired at storage level) session still clears
    // the binding.
    assert_eq!(
        repo.revoke_session(&scope, &sid).await.unwrap(),
        AuthSessionRevoke::Revoked,
        "revoke on the live session must be Revoked"
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-exp-2", 1_500)
            .await
            .unwrap()
            .is_none(),
        "revoked hash must not be queryable"
    );
}

#[tokio::test]
async fn cross_env_scope_isolation() {
    let repo = MemoryUserIdentityRepo::new();
    let scope_dev = fresh_scope(&repo, "cookie", "ext-isolation", Some("dev-user"), None, "dev").await;
    let scope_prod = fresh_scope(&repo, "cookie", "ext-isolation", Some("prod-user"), None, "prod").await;
    assert_ne!(
        scope_dev.user_id, scope_prod.user_id,
        "different env yields distinct internal user_ids"
    );

    let dev_sid = "sid-dev".to_string();
    let prod_sid = "sid-prod".to_string();
    let dev_hash = "hash-dev".to_string();
    let prod_hash = "hash-prod".to_string();

    repo.install_login_session(InstallAuthSession {
        scope: scope_dev.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: dev_sid.clone(),
            revision: 1,
            token_hash: dev_hash.clone(),
        },
        expires_at: 5_000_000_000,
    })
    .await
    .unwrap();
    repo.install_login_session(InstallAuthSession {
        scope: scope_prod.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: prod_sid.clone(),
            revision: 1,
            token_hash: prod_hash.clone(),
        },
        expires_at: 5_000_000_000,
    })
    .await
    .unwrap();

    // Each env resolves its own hash.
    assert!(
        repo.get_session_by_hash(&scope_dev, &dev_hash, 1_000)
            .await
            .unwrap()
            .is_some(),
        "dev scope resolves dev hash"
    );
    assert!(
        repo.get_session_by_hash(&scope_prod, &prod_hash, 1_000)
            .await
            .unwrap()
            .is_some(),
        "prod scope resolves prod hash"
    );

    // Cross-env hash lookup yields None.
    assert!(
        repo.get_session_by_hash(&scope_dev, &prod_hash, 1_000)
            .await
            .unwrap()
            .is_none(),
        "prod hash is not queryable under dev scope"
    );
    assert!(
        repo.get_session_by_hash(&scope_prod, &dev_hash, 1_000)
            .await
            .unwrap()
            .is_none(),
        "dev hash is not queryable under prod scope"
    );

    // Revoke on dev must not affect prod.
    assert_eq!(
        repo.revoke_session(&scope_dev, &dev_sid).await.unwrap(),
        AuthSessionRevoke::Revoked
    );
    assert!(
        repo.get_session_by_hash(&scope_prod, &prod_hash, 1_000)
            .await
            .unwrap()
            .is_some(),
        "prod session must survive a revoke of the dev session"
    );
    assert!(
        repo.get_session_by_hash(&scope_dev, &dev_hash, 1_000)
            .await
            .unwrap()
            .is_none(),
        "dev session is cleared by its own revoke"
    );

    // Re-revoke on dev yields NotCurrent.
    assert_eq!(
        repo.revoke_session(&scope_dev, &dev_sid).await.unwrap(),
        AuthSessionRevoke::NotCurrent
    );
}

#[tokio::test]
async fn install_with_stale_expected_revision_conflicts() {
    // CAS conflict: install once successful; a second install pretending the
    // revision is still 0 must Conflict and must NOT overwrite the live
    // session.
    let repo = MemoryUserIdentityRepo::new();
    let scope = fresh_scope(&repo, "cookie", "ext-cas", None, None, "dev").await;

    repo.install_login_session(InstallAuthSession {
        scope: scope.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: "sid-1".to_string(),
            revision: 1,
            token_hash: "hash-1".to_string(),
        },
        expires_at: 5_000_000_000,
    })
    .await
    .unwrap();

    let conflict = repo
        .install_login_session(InstallAuthSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: AuthSessionVersion {
                session_id: "sid-2".to_string(),
                revision: 1,
                token_hash: "hash-2".to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .unwrap();
    assert_eq!(
        conflict,
        AuthSessionWrite::Conflict,
        "stale install must Conflict (CAS), never overwrite"
    );

    // The original session is still the live binding.
    let live = repo
        .get_session_by_hash(&scope, "hash-1", 1_000)
        .await
        .unwrap()
        .expect("session 1 must still be live");
    assert_eq!(live.version.session_id, "sid-1");
    assert!(
        repo.get_session_by_hash(&scope, "hash-2", 1_000)
            .await
            .unwrap()
            .is_none(),
        "session 2's hash is not queryable (its install conflicted)"
    );
}

#[tokio::test]
async fn install_ignores_next_revision_enforces_expected_plus_one() {
    // Revision-counter hardening: the impl writes `expected_revision + 1`,
    // not the caller-supplied `next.revision`. Asserts the plan's "ignoring/
    // hardening mismatched next.revision" invariant from a public-API eye.
    let repo = MemoryUserIdentityRepo::new();
    let scope = fresh_scope(&repo, "cookie", "ext-ign", None, None, "dev").await;

    repo.install_login_session(InstallAuthSession {
        scope: scope.clone(),
        expected_revision: 0,
        next: AuthSessionVersion {
            session_id: "sid-ign".to_string(),
            revision: 99, // intentionally wrong
            token_hash: "hash-ign".to_string(),
        },
        expires_at: 5_000_000_000,
    })
    .await
    .unwrap();

    assert_eq!(
        repo.read_session_revision(&scope).await.unwrap(),
        1,
        "stored revision must be expected_revision + 1, not next.revision"
    );

    // A second install at expected=1 (the actual stored revision) succeeds,
    // proving the counter observed was 1, not 99.
    let outcome = repo
        .install_login_session(InstallAuthSession {
            scope,
            expected_revision: 1,
            next: AuthSessionVersion {
                session_id: "sid-2".to_string(),
                revision: 2,
                token_hash: "hash-2".to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .unwrap();
    assert_eq!(outcome, AuthSessionWrite::Applied);
}
