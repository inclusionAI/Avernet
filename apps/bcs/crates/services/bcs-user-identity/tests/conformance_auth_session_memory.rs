//! In-memory `AuthSessionRepoPort` conformance + deterministic race driver
//! (V1 API auth plugin chain).
//!
//! Runs the central `bcs-test-support` harness verbatim against
//! `MemoryUserIdentityRepo`, seeding a real identity row per scope first
//! (`ensure_identity` — the harness asserts a non-empty joined username, so
//! every scope must be rooted in a real identity row). The driver then pins
//! the driver-level sequences the harness deliberately leaves out: the
//! rotate → revoke → re-rotate conflict ladder, stale-CAS install/rotate
//! conflicts, new-login vs old-logout independence, the Barrier-driven
//! double-refresh race (EXACTLY ONE `Applied`), expiry filtering, and
//! cross-env scope isolation.

use std::sync::Arc;

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionVersion,
    AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;
use bcs_test_support::contract::repo::auth_session_repo_port_contract_tests;
use bcs_user_identity::MemoryUserIdentityRepo;

const NOW: u64 = 1_800_000_000;

/// Seed a real identity row and return its trusted session scope
/// `(user_id, provider, env)`. The internal `user_id` comes from
/// `ensure_identity`, so no scope is ever derived from unverified data.
async fn seeded_scope(
    repo: &MemoryUserIdentityRepo,
    external_user_id: &str,
    env: &str,
) -> AuthSessionScope {
    let user_id = repo
        .ensure_identity(
            "github",
            external_user_id,
            Some("Contract 用户"),
            Some("https://avatars.example/contract.png"),
            env,
        )
        .await
        .expect("seed identity row for the scope");
    assert!(!user_id.is_empty(), "ensure_identity must allocate an internal user id");
    AuthSessionScope {
        user_id,
        provider: "github".to_string(),
        env: env.to_string(),
    }
}

fn version(session_id: &str, revision: u64, token_hash: &str) -> AuthSessionVersion {
    AuthSessionVersion {
        session_id: session_id.to_string(),
        revision,
        token_hash: token_hash.to_string(),
    }
}

fn install_command(
    scope: &AuthSessionScope,
    expected_revision: u64,
    next: AuthSessionVersion,
    expires_at: u64,
) -> InstallAuthSession {
    InstallAuthSession {
        scope: scope.clone(),
        expected_revision,
        next,
        expires_at,
    }
}

fn rotate_command(
    scope: &AuthSessionScope,
    expected: AuthSessionVersion,
    next: AuthSessionVersion,
    expires_at: u64,
    now: u64,
) -> RotateAuthSession {
    RotateAuthSession {
        scope: scope.clone(),
        expected,
        next,
        expires_at,
        now,
    }
}

/// The central harness verbatim, against a fresh repo and a real seeded
/// identity row.
#[tokio::test]
async fn memory_repo_passes_auth_session_contract() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = seeded_scope(&repo, "auth-session-ext-1", "dev").await;
    auth_session_repo_port_contract_tests(&repo, scope).await;
}

/// The brief's rotate → revoke → re-rotate ladder: once the rotated-to
/// version is revoked, replaying the same old rotate command is a lost CAS
/// (`Conflict`), never an error and never an unconditional rewrite.
#[tokio::test]
async fn revoked_session_replay_of_old_rotate_command_conflicts() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = seeded_scope(&repo, "auth-session-ext-2", "dev").await;

    const ROTATED_AT: u64 = NOW + 10;

    // Install session version A (fresh scope, so the install CAS expects 0).
    let version_a = version("auth-session-sid-2", 1, "hash-a-2");
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            version_a.clone(),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Applied,
        "install session version A"
    );

    // same_sid is A.session_id; B is the revision+1 same-sid next token.
    let same_sid = version_a.session_id.clone();
    let to_b = rotate_command(
        &scope,
        version_a,
        version(&same_sid, 2, "hash-b-2"),
        NOW + 7_200,
        ROTATED_AT,
    );

    assert_eq!(
        repo.rotate_session(to_b.clone()).await.unwrap(),
        AuthSessionWrite::Applied
    );
    assert_eq!(
        repo.revoke_session(&scope, &same_sid).await.unwrap(),
        AuthSessionRevoke::Revoked
    );
    assert!(
        repo.get_session_by_hash(&scope, &to_b.next.token_hash, to_b.now)
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(
        repo.rotate_session(to_b).await.unwrap(),
        AuthSessionWrite::Conflict
    );
}

/// Stale CAS preconditions are `Conflict`, not errors: an install that read a
/// superseded revision loses the race; a rotate naming the wrong hash or a
/// foreign session id never applies, and the stored session is untouched.
#[tokio::test]
async fn stale_cas_preconditions_conflict_without_touching_the_stored_session() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = seeded_scope(&repo, "auth-session-ext-7", "dev").await;

    let installed = version("auth-session-sid-7", 1, "hash-7-a");
    assert_eq!(
        repo.install_login_session(install_command(&scope, 0, installed.clone(), NOW + 3_600))
            .await
            .unwrap(),
        AuthSessionWrite::Applied
    );

    // A second login batch that read revision 0 before the first install
    // applied loses the CAS race.
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            version("auth-session-sid-7-lost", 1, "hash-7-lost"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "an install against a superseded expected revision is a lost race"
    );

    // A rotate whose expected hash no longer matches the stored version
    // conflicts, and so does a rotate that tries to change the session id.
    assert_eq!(
        repo.rotate_session(rotate_command(
            &scope,
            version("auth-session-sid-7", 1, "hash-7-wrong"),
            version("auth-session-sid-7", 2, "hash-7-next"),
            NOW + 3_600,
            NOW + 10,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "an expected version hashing a different token is a lost CAS"
    );
    assert_eq!(
        repo.rotate_session(rotate_command(
            &scope,
            version("auth-session-sid-7", 1, "hash-7-a"),
            version("auth-session-sid-7-other", 2, "hash-7-next"),
            NOW + 3_600,
            NOW + 10,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "a rotate changing the session id never applies"
    );

    // The stored session survives every rejected write untouched.
    let snapshot = repo
        .get_session_by_hash(&scope, "hash-7-a", NOW + 20)
        .await
        .unwrap()
        .expect("the current session still resolves");
    assert_eq!(snapshot.version, installed);

    // A session write scoped to a scope with NO identity row conflicts
    // rather than auto-creating the identity.
    let no_identity = AuthSessionScope {
        user_id: "no-such-user".to_string(),
        provider: "github".to_string(),
        env: "dev".to_string(),
    };
    assert_eq!(
        repo.install_login_session(install_command(
            &no_identity,
            0,
            version("auth-session-sid-7-ghost", 1, "hash-7-ghost"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "a session write never auto-creates the missing identity row"
    );
}

/// A brand-new login C and a late logout of the old session A are
/// independent: the stale logout names the OLD session id and must report
/// `NotCurrent` while C stays current; C's own logout then revokes, and a
/// fresh login batch restarts against the post-revoke revision.
#[tokio::test]
async fn new_login_survives_a_stale_logout_of_the_old_session() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = seeded_scope(&repo, "auth-session-ext-4", "dev").await;

    // Old browser session A.
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            version("auth-session-sid-4-a", 1, "hash-4-a"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Applied
    );

    // A new browser batch reads revision 1, pre-signs, and CAS-installs its
    // own fresh session C (new session id at revision 2).
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            1,
            version("auth-session-sid-4-c", 2, "hash-4-c"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Applied,
        "the new login installs after reading revision 1"
    );

    // The old browser's late logout names the OLD session id: it must not
    // revoke the freshly installed session.
    assert_eq!(
        repo.revoke_session(&scope, "auth-session-sid-4-a").await.unwrap(),
        AuthSessionRevoke::NotCurrent,
        "a stale logout of the replaced session id is NotCurrent"
    );
    let snapshot = repo
        .get_session_by_hash(&scope, "hash-4-c", NOW + 10)
        .await
        .unwrap()
        .expect("the new session C still resolves");
    assert_eq!(snapshot.version, version("auth-session-sid-4-c", 2, "hash-4-c"));
    // The snapshot joins the display fields of the same identity row.
    assert_eq!(snapshot.username, "Contract 用户");
    assert_eq!(snapshot.display_name.as_deref(), Some("Contract 用户"));
    assert_eq!(
        snapshot.avatar.as_deref(),
        Some("https://avatars.example/contract.png")
    );

    // The current session's own logout still revokes...
    assert_eq!(
        repo.revoke_session(&scope, "auth-session-sid-4-c").await.unwrap(),
        AuthSessionRevoke::Revoked
    );
    assert_eq!(
        repo.read_session_revision(&scope).await.unwrap(),
        3,
        "revoke bumps the revision (install 2 → revoke 3)"
    );

    // ...and a fresh login batch restarts against the post-revoke revision.
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            3,
            version("auth-session-sid-4-d", 4, "hash-4-d"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Applied,
        "a fresh login batch restarts against the post-revoke revision"
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-4-d", NOW + 20)
            .await
            .unwrap()
            .is_some()
    );
}

/// Two refreshes holding the SAME current token race via a Barrier: the
/// Barrier releases both tasks at the same instant so both CAS rotations
/// contend on the one lock-guarded compare-and-set. Exactly one wins
/// (`Applied`); the loser is `Conflict` — never a second `Applied`, never an
/// error promoted to success — and only the applied refresh's hash resolves.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn concurrent_double_refresh_applies_exactly_one() {
    let repo = Arc::new(MemoryUserIdentityRepo::new());
    let scope = seeded_scope(&repo, "auth-session-ext-3", "dev").await;

    let current = version("auth-session-sid-3", 1, "hash-dr-3");
    assert_eq!(
        repo.install_login_session(install_command(&scope, 0, current.clone(), NOW + 3_600))
            .await
            .unwrap(),
        AuthSessionWrite::Applied
    );

    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let mut tasks = Vec::new();
    for next_hash in ["hash-dr-3-left", "hash-dr-3-right"] {
        let repo = repo.clone();
        let barrier = barrier.clone();
        let scope = scope.clone();
        let expected = current.clone();
        tasks.push(tokio::spawn(async move {
            barrier.wait().await;
            repo.rotate_session(rotate_command(
                &scope,
                expected,
                version("auth-session-sid-3", 2, next_hash),
                NOW + 3_600,
                NOW + 10,
            ))
            .await
        }));
    }

    let mut applied = 0u32;
    let mut conflicted = 0u32;
    for task in tasks {
        let outcome = task
            .await
            .expect("the refresh task must not panic")
            .expect("a refresh race is never a persistence error");
        match outcome {
            AuthSessionWrite::Applied => applied += 1,
            AuthSessionWrite::Conflict => conflicted += 1,
        }
    }
    assert_eq!(applied, 1, "exactly one refresh applies");
    assert_eq!(conflicted, 1, "the losing refresh is a Conflict, not a second Applied and not an error");
    assert_eq!(
        u32::from(
            repo.get_session_by_hash(&scope, "hash-dr-3-left", NOW + 20)
                .await
                .unwrap()
                .is_some()
        ) + u32::from(
            repo.get_session_by_hash(&scope, "hash-dr-3-right", NOW + 20)
                .await
                .unwrap()
                .is_some()
        ),
        1,
        "exactly the applied refresh's hash still resolves"
    );
}

/// Expiry filtering: a session resolves strictly before its `expires_at`,
/// stops resolving at and after the boundary, cannot be rotated (expired CAS
/// is a lost race), yet can still be logged out — revoke clears the hash and
/// bumps the revision regardless of expiry.
#[tokio::test]
async fn expired_session_stops_resolving_and_rotating_but_still_revokes() {
    let repo = MemoryUserIdentityRepo::new();
    let scope = seeded_scope(&repo, "auth-session-ext-5", "dev").await;

    let expires_at = NOW + 100;
    assert_eq!(
        repo.install_login_session(install_command(
            &scope,
            0,
            version("auth-session-sid-5", 1, "hash-5"),
            expires_at,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Applied
    );

    // Strictly before expiry it resolves...
    assert!(
        repo.get_session_by_hash(&scope, "hash-5", NOW + 99)
            .await
            .unwrap()
            .is_some(),
        "the session resolves strictly before expires_at"
    );
    // ...at the boundary and after it does not (stored expires_at > now).
    assert!(
        repo.get_session_by_hash(&scope, "hash-5", expires_at)
            .await
            .unwrap()
            .is_none(),
        "the session does not resolve at the boundary"
    );
    assert!(
        repo.get_session_by_hash(&scope, "hash-5", NOW + 200)
            .await
            .unwrap()
            .is_none(),
        "the session does not resolve after expires_at"
    );

    // Rotating an expired session is a lost CAS, not an error.
    assert_eq!(
        repo.rotate_session(rotate_command(
            &scope,
            version("auth-session-sid-5", 1, "hash-5"),
            version("auth-session-sid-5", 2, "hash-5-next"),
            NOW + 3_600,
            NOW + 200,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "an expired session's rotate CAS fails the expiry condition"
    );

    // Expiry does not fence revocation: the logout still reports the exact
    // outcome, clears the hash, and bumps the revision.
    assert_eq!(
        repo.revoke_session(&scope, "auth-session-sid-5").await.unwrap(),
        AuthSessionRevoke::Revoked
    );
    assert_eq!(repo.read_session_revision(&scope).await.unwrap(), 2);
    assert!(
        repo.get_session_by_hash(&scope, "hash-5", NOW)
            .await
            .unwrap()
            .is_none(),
        "the revoked hash never resolves"
    );
}

/// Cross-env isolation: `env` is part of the scope address. The same external
/// identity seeded in dev and prod gets distinct rows with independent
/// sessions, and neither a read, a write, nor a revoke targeting a scope
/// under the wrong env touches the other environment's session.
#[tokio::test]
async fn cross_env_scopes_are_independent() {
    let repo = MemoryUserIdentityRepo::new();
    let dev_scope = seeded_scope(&repo, "auth-session-ext-6", "dev").await;
    let prod_scope = seeded_scope(&repo, "auth-session-ext-6", "prod").await;
    assert_ne!(
        dev_scope.user_id, prod_scope.user_id,
        "env partitions identity rows"
    );

    assert_eq!(
        repo.install_login_session(install_command(
            &dev_scope,
            0,
            version("auth-session-sid-6", 1, "hash-6-dev"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Applied
    );
    assert_eq!(
        repo.read_session_revision(&dev_scope).await.unwrap(),
        1,
        "the dev session is installed"
    );

    // The prod scope starts fresh and never sees the dev session.
    assert_eq!(
        repo.read_session_revision(&prod_scope).await.unwrap(),
        0,
        "prod has no session yet"
    );
    assert!(
        repo.get_session_by_hash(&prod_scope, "hash-6-dev", NOW)
            .await
            .unwrap()
            .is_none(),
        "a prod-scoped read cannot see the dev session hash"
    );

    // `env` participates in the scope ADDRESS: the dev identity's user_id
    // under env "prod" names a scope with no identity row. The install CAS
    // conflicts (no auto-created identity), reads stay absent, and the dev
    // session is untouched.
    let dev_identity_under_prod = AuthSessionScope {
        env: "prod".to_string(),
        ..dev_scope.clone()
    };
    assert_eq!(
        repo.install_login_session(install_command(
            &dev_identity_under_prod,
            0,
            version("auth-session-sid-6-p", 1, "hash-6-p"),
            NOW + 3_600,
        ))
        .await
        .unwrap(),
        AuthSessionWrite::Conflict,
        "wrong-env scope addressing cannot install against the dev identity row"
    );
    assert!(
        repo.get_session_by_hash(&dev_identity_under_prod, "hash-6-dev", NOW)
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(
        repo.revoke_session(&dev_identity_under_prod, "auth-session-sid-6")
            .await
            .unwrap(),
        AuthSessionRevoke::NotCurrent,
        "wrong-env revoke names no current session"
    );
    assert!(
        repo.get_session_by_hash(&dev_scope, "hash-6-dev", NOW)
            .await
            .unwrap()
            .is_some(),
        "the dev session is untouched by every wrong-env operation"
    );
    assert_eq!(
        repo.read_session_revision(&dev_scope).await.unwrap(),
        1,
        "the dev session revision is unchanged"
    );
}
