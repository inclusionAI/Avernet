//! `AuthSessionIdentityPort` conformance harness (V1 API auth plugin chain).
//!
//! Concrete bridges (e.g. bootstrap's `RepoAuthSessionIdentityPort`) call
//! [`auth_session_identity_port_contract_tests`] from a
//! `tests/conformance_auth_session_identity.rs` driver, passing a fresh
//! persistence repo (`AuthSessionRepoPort`), the matching identity repo
//! (`UserIdentityRepoPort`), and a factory that wires the two into the port
//! under test. The harness owns the two error-injection decorators so the
//! classification asserts stay central.
//!
//! The harness pins the strict semantics across the bridge:
//! - `ensure_identity` creates/confirms the NEW-LOGIN identity and is
//!   idempotent (same internal `user_id` on repeat);
//! - a repo-injected store failure surfaces per-call as
//!   `SessionStoreError::Unavailable` — never swallowed into a
//!   `Conflict`/`NotCurrent` outcome or an `Ok(None)`;
//! - an identity-injected failure surfaces from `ensure_identity` as
//!   `SessionStoreError::Unavailable`, never a silent miss;
//! - cross-env scopes never see or touch another environment's session;
//! - CAS preconditions are exact: a wrong session id or revision conflicts,
//!   a foreign-environment install conflicts;
//! - unknown and revoked hashes resolve to `Ok(None)`.
//!
//! `session_id`/`token_hash` values here are short placeholders; real
//! deployments sign SHA-256 hex hashes (64 chars) and keep session ids
//! within 64 chars (see the strict port docs).

use std::sync::Arc;

use async_trait::async_trait;

use bcs_auth_api::session::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionStoreError, SessionVersion, SessionWrite,
};
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke as RepoSessionRevoke, AuthSessionRepoPort, AuthSessionScope,
    AuthSessionSnapshot, AuthSessionStoreError as RepoSessionStoreError,
    AuthSessionWrite as RepoSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentityRepoPort;

/// Error-injecting `AuthSessionRepoPort` decorator: every method fails, as
/// if the DB layer were down. The decorator's failure surfaces as
/// `Unavailable`; the port under test must relay that category unchanged.
struct FailingAuthSessionRepo;

#[async_trait]
impl AuthSessionRepoPort for FailingAuthSessionRepo {
    async fn read_session_revision(
        &self,
        _scope: &AuthSessionScope,
    ) -> Result<u64, RepoSessionStoreError> {
        Err(RepoSessionStoreError::Unavailable)
    }

    async fn get_session_by_hash(
        &self,
        _scope: &AuthSessionScope,
        _hash: &str,
        _now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, RepoSessionStoreError> {
        Err(RepoSessionStoreError::Unavailable)
    }

    async fn install_login_session(
        &self,
        _command: InstallAuthSession,
    ) -> Result<RepoSessionWrite, RepoSessionStoreError> {
        Err(RepoSessionStoreError::Unavailable)
    }

    async fn rotate_session(
        &self,
        _command: RotateAuthSession,
    ) -> Result<RepoSessionWrite, RepoSessionStoreError> {
        Err(RepoSessionStoreError::Unavailable)
    }

    async fn revoke_session(
        &self,
        _scope: &AuthSessionScope,
        _session_id: &str,
    ) -> Result<RepoSessionRevoke, RepoSessionStoreError> {
        Err(RepoSessionStoreError::Unavailable)
    }
}

/// Error-injecting `UserIdentityRepoPort` decorator: every method fails so
/// `ensure_identity` failures must propagate, never be swallowed into an
/// Option miss or passed off as success.
struct FailingUserIdentityRepo;

#[async_trait]
impl UserIdentityRepoPort for FailingUserIdentityRepo {
    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, String> {
        Err("identity store unavailable".to_string())
    }

    async fn lookup_user_id(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _env: &str,
    ) -> Option<String> {
        None
    }

    async fn lookup_by_user_id(&self, _user_id: &str, _auth_source: &str) -> Option<String> {
        None
    }

    async fn get_by_token(&self, _token: &str) -> Option<bcs_service_api::UserIdentity> {
        None
    }

    async fn get_by_user_id_display(&self, _user_id: &str) -> Option<bcs_service_api::UserIdentity> {
        None
    }
}

/// Assert the exact `Unavailable` category for one injected-failure call.
fn assert_unavailable(error: SessionStoreError, call: &str) {
    assert!(
        matches!(error, SessionStoreError::Unavailable),
        "{call} must surface SessionStoreError::Unavailable, got {error:?}"
    );
}

/// Strict session-identity conformance suite.
///
/// Drives the pinned NEW-LOGIN → inspect → refresh → logout sequence
/// through the port under test, plus the injected-failure classification
/// ladder. `make_port` wires concrete repos into the port (the bootstrap
/// `RepoAuthSessionIdentityPort` translation is the first consumer).
#[allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn auth_session_identity_port_contract_tests<F>(
    repo: Arc<dyn AuthSessionRepoPort>,
    identities: Arc<dyn UserIdentityRepoPort>,
    make_port: F,
) where
    F: Fn(
        Arc<dyn AuthSessionRepoPort>,
        Arc<dyn UserIdentityRepoPort>,
    ) -> Arc<dyn AuthSessionIdentityPort>,
{
    const NOW: u64 = 1_800_000_000;
    const EXPIRES_AT: u64 = NOW + 3_600;
    const ROTATED_AT: u64 = NOW + 10;
    const PROVIDER: &str = "github";
    const EXT_USER: &str = "auth-session-identity-ext-1";
    const ENV: &str = "dev";
    const DISPLAY_NAME: &str = "Contract 用户";
    const SID: &str = "auth-session-identity-sid";
    const HASH_A: &str = "auth-session-identity-hash-a";
    const HASH_B: &str = "auth-session-identity-hash-b";
    const FOREIGN_SID: &str = "auth-session-identity-foreign-sid";

    let port = make_port(repo.clone(), identities.clone());

    // 1. NEW LOGIN identity: created and confirmed via the strict port.
    let user_id = port
        .ensure_identity(
            PROVIDER,
            EXT_USER,
            Some(DISPLAY_NAME),
            Some("https://avatars.example/contract.png"),
            ENV,
        )
        .await
        .expect("ensure_identity creates the login identity");
    assert!(
        !user_id.is_empty(),
        "ensure_identity must return the internal user id"
    );
    let repeat = port
        .ensure_identity(PROVIDER, EXT_USER, Some(DISPLAY_NAME), None, ENV)
        .await
        .expect("repeat ensure_identity must succeed");
    assert_eq!(
        repeat, user_id,
        "ensure_identity is idempotent per (provider, external id, env)"
    );

    // The session scope is built from trusted values (the returned internal
    // id plus configured provider/env), never from unverified claims.
    let scope = SessionScope {
        user_id: user_id.clone(),
        provider: PROVIDER.to_string(),
        env: ENV.to_string(),
    };

    // 2. Fresh revision 0 and an unknown hash resolve to Ok(0)/Ok(None).
    assert_eq!(
        port.read_session_revision(&scope)
            .await
            .expect("read the fresh revision"),
        0,
        "a fresh scope starts at revision 0"
    );
    assert!(
        port.get_session_by_hash(&scope, "unknown-session-hash", NOW)
            .await
            .expect("an unknown hash is a query, not an error")
            .is_none(),
        "an unknown token hash resolves to None"
    );

    // 3. Install session version A against the freshly read revision.
    let version_a = SessionVersion {
        session_id: SID.to_string(),
        revision: 1,
        token_hash: HASH_A.to_string(),
    };
    assert_eq!(
        port.install_login_session(InstallSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: version_a.clone(),
            expires_at: EXPIRES_AT,
        })
        .await
        .expect("install session version A"),
        SessionWrite::Applied,
        "installing against the freshly read revision applies"
    );

    // 4. Version A resolves by hash and joins the identity display fields.
    let snapshot = port
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

    // 5. Cross-env mismatch: env participates in the scope address, so the
    // same user_id under a foreign env neither reads nor installs.
    let wrong_env_scope = SessionScope {
        env: "prod".to_string(),
        ..scope.clone()
    };
    assert!(
        port.get_session_by_hash(&wrong_env_scope, &version_a.token_hash, NOW)
            .await
            .expect("cross-env read is a query, not an error")
            .is_none(),
        "a wrong-env scope cannot see the dev session hash"
    );
    assert_eq!(
        port.install_login_session(InstallSession {
            scope: wrong_env_scope,
            expected_revision: 0,
            next: SessionVersion {
                session_id: "auth-session-identity-sid-p".to_string(),
                revision: 2,
                token_hash: "auth-session-identity-hash-p".to_string(),
            },
            expires_at: EXPIRES_AT,
        })
        .await
        .expect("cross-env install is a CAS outcome, not an error"),
        SessionWrite::Conflict,
        "a wrong-env scope install never touches the dev identity row/session"
    );

    // 6. sid/revision CAS preconditions are exact: a rotate whose expected
    // version names the wrong session id or the wrong revision conflicts.
    let rotate_next_b = || SessionVersion {
        session_id: SID.to_string(),
        revision: version_a.revision + 1,
        token_hash: HASH_B.to_string(),
    };
    assert_eq!(
        port.rotate_session(RotateSession {
            scope: scope.clone(),
            expected: SessionVersion {
                session_id: FOREIGN_SID.to_string(),
                revision: version_a.revision,
                token_hash: version_a.token_hash.clone(),
            },
            next: rotate_next_b(),
            expires_at: EXPIRES_AT,
            now: ROTATED_AT,
        })
        .await
        .expect("a CAS mismatch is a Conflict outcome, not an error"),
        SessionWrite::Conflict,
        "a rotate naming a foreign session id never applies"
    );
    assert_eq!(
        port.rotate_session(RotateSession {
            scope: scope.clone(),
            expected: SessionVersion {
                session_id: SID.to_string(),
                revision: version_a.revision + 9,
                token_hash: version_a.token_hash.clone(),
            },
            next: rotate_next_b(),
            expires_at: EXPIRES_AT,
            now: ROTATED_AT,
        })
        .await
        .expect("a CAS mismatch is a Conflict outcome, not an error"),
        SessionWrite::Conflict,
        "a rotate naming a stale revision never applies"
    );

    // 7. The exact expected version rotates A -> B; only B resolves after.
    let version_b = rotate_next_b();
    assert_eq!(
        port.rotate_session(RotateSession {
            scope: scope.clone(),
            expected: version_a.clone(),
            next: version_b.clone(),
            expires_at: EXPIRES_AT,
            now: ROTATED_AT,
        })
        .await
        .expect("rotate session version A to B"),
        SessionWrite::Applied,
        "rotating from the current version applies"
    );
    assert!(
        port.get_session_by_hash(&scope, &version_a.token_hash, ROTATED_AT)
            .await
            .expect("rotated-out hash read")
            .is_none(),
        "the rotated-out hash no longer resolves"
    );

    // 8. Revoke mismatches: a foreign session id is NotCurrent, the current
    // id revokes, and the revoked hash stops resolving.
    assert_eq!(
        port.revoke_session(&scope, FOREIGN_SID)
            .await
            .expect("a revoke of a foreign sid is an outcome, not an error"),
        SessionRevoke::NotCurrent,
        "a revoke naming a foreign session id is NotCurrent"
    );
    assert_eq!(
        port.revoke_session(&scope, &version_b.session_id)
            .await
            .expect("revoke the current session"),
        SessionRevoke::Revoked,
        "revoking the current session revokes"
    );
    assert!(
        port.get_session_by_hash(&scope, &version_b.token_hash, NOW)
            .await
            .expect("revoked hash read")
            .is_none(),
        "the revoked hash no longer resolves"
    );
    assert_eq!(
        port.read_session_revision(&scope)
            .await
            .expect("read the post-revoke revision"),
        3,
        "revoke bumps the revision (install 1 → rotate 2 → revoke 3)"
    );

    // 9. Error classification ladder: a repo-injected store failure must
    // surface per-call as SessionStoreError::Unavailable — never as a
    // Conflict/NotCurrent outcome, an Ok(None), or a panic.
    let failing_repo = make_port(Arc::new(FailingAuthSessionRepo), identities.clone());
    assert_unavailable(
        failing_repo
            .read_session_revision(&scope)
            .await
            .expect_err("an injected store failure must surface"),
        "read_session_revision",
    );
    assert_unavailable(
        failing_repo
            .get_session_by_hash(&scope, &version_b.token_hash, NOW)
            .await
            .expect_err("an injected store failure must surface"),
        "get_session_by_hash",
    );
    assert_unavailable(
        failing_repo
            .install_login_session(InstallSession {
                scope: scope.clone(),
                expected_revision: 0,
                next: version_b.clone(),
                expires_at: EXPIRES_AT,
            })
            .await
            .expect_err("an injected store failure must surface"),
        "install_login_session",
    );
    assert_unavailable(
        failing_repo
            .rotate_session(RotateSession {
                scope: scope.clone(),
                expected: version_b.clone(),
                next: version_b.clone(),
                expires_at: EXPIRES_AT,
                now: ROTATED_AT,
            })
            .await
            .expect_err("an injected store failure must surface"),
        "rotate_session",
    );
    assert_unavailable(
        failing_repo
            .revoke_session(&scope, &version_b.session_id)
            .await
            .expect_err("an injected store failure must surface"),
        "revoke_session",
    );

    // 10. ensure_identity error propagation: an identity-store failure is a
    // strict error, never a silent miss or a fabricated user id.
    let failing_identity = make_port(repo.clone(), Arc::new(FailingUserIdentityRepo));
    assert_unavailable(
        failing_identity
            .ensure_identity(PROVIDER, EXT_USER, Some(DISPLAY_NAME), None, ENV)
            .await
            .expect_err("an identity-store failure must surface"),
        "ensure_identity",
    );
}
