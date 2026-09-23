//! `TestSessionIdentity` — faithful in-memory `AuthSessionIdentityPort`
//! double for the strict OAuth session engine tests.
//!
//! Holds per-scope state under one `Mutex` so the same CAS-vs-revision
//! invariants the real stores enforce (see
//! `services/bcs-user-identity/src/session_memory.rs`) are reproduced here.
//! Used only by `tests/strict_session.rs` and `tests/session_lifecycle.rs`;
//! the plugin tests MUST NOT depend on bootstrap. Conformance coverage of
//! real store/bridge combinations lives in `bootstrap` (Task 13).
//!
//! # Identity-row requirement
//!
//! `install_login_session` returns `Ok(SessionWrite::Conflict)` when the
//! scope was never registered via `ensure_identity` — matching the real
//! store (`session_memory.rs` returns Conflict when `find_record_mut` is
//! `None`). So tests MUST call `ensure_identity(provider, external_id, env)`
//! first, then build the [`SessionScope`] with the returned internal
//! `user_id`. The engine itself never calls `ensure_identity`; identity
//! creation belongs to the caller (the Task 10/11 facade).
//!
//! # Fault & race injection
//!
//! `inject_error(method)` toggles a per-method [`SessionStoreError::Unavailable`]
//! injection: the next call to that method returns `Err(Unavailable)`
//! regardless of state. `bump_revision_on_next_install_once` simulates a
//! concurrent installer landing between the engine's
//! `read_session_revision` and its `install_login_session`: the next
//! `install_login_session` call bumps the stored revision BEFORE the CAS
//! check, so a CAS that would otherwise match fails with `Conflict`. The
//! toggle clears itself after one invocation so subsequent installs observe
//! the bumped revision.
//!
//! # Operation recording
//!
//! Every port method increments a per-method counter so tests can assert
//! exact call sequences (e.g. `verify` calls `get_session_by_hash` exactly
//! once, `refresh` calls `get_session_by_hash` then `rotate_session`, etc.).
//! Counters DO increment on the injected-Unavailable path too — the call
//! was attempted and observed.

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;

use bcs_auth_api::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionSnapshot, SessionStoreError, SessionVersion, SessionWrite,
};

/// Methods a test can inject `Err(Unavailable)` on.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub enum PortMethod {
    ReadSessionRevision,
    GetSessionByHash,
    InstallLoginSession,
    RotateSession,
    RevokeSession,
    EnsureIdentity,
}

/// Per-scope live state. Fields mirror `MemorySessionState` from
/// `bcs-user-identity/src/session_memory.rs`:
/// - `revision == 0` means no session installed yet (per contract: missing
///   row is signaled by revision 0).
/// - `token_hash == None` means revoked-or-never-installed → `get_session_by_hash`
///   returns `Ok(None)`.
/// - `display_name` / `avatar` are write-through snapshot fields from
///   `ensure_identity`; the engine does not set them, but they surface on
///   the snapshot returned by `get_session_by_hash`.
struct ScopeState {
    revision: u64,
    session_id: Option<String>,
    token_hash: Option<String>,
    expires_at: Option<u64>,
    external_user_id: Option<String>,
    display_name: Option<String>,
    avatar: Option<String>,
}

impl ScopeState {
    fn fresh(revision: u64) -> Self {
        Self {
            revision,
            session_id: None,
            token_hash: None,
            expires_at: None,
            external_user_id: None,
            display_name: None,
            avatar: None,
        }
    }
}

/// Identity record indexed by `(provider, external_user_id, env)`. Holds the
/// internal `user_id` allocated by `ensure_identity` and the latest display
/// fields. Used for the write-through refresh on a second `ensure_identity`
/// call.
struct IdentityRecord {
    user_id: String,
}

/// Faithful `AuthSessionIdentityPort` double for the OAuth session engine
/// tests. See the module docs for the CAS semantics, fault injection, and
/// race-injection knobs.
pub struct TestSessionIdentity {
    inner: Mutex<Inner>,
}

struct Inner {
    /// Identity records keyed by external identity `(provider, external_user_id, env)`.
    identities: HashMap<(String, String, String), IdentityRecord>,
    /// Session/scope state keyed by `(user_id, provider, env)`.
    scopes: HashMap<(String, String, String), ScopeState>,
    /// Per-method call counters.
    counts: HashMap<PortMethod, u64>,
    /// Per-method Unavailable injection toggles (one-shot until cleared).
    inject_unavailable: HashMap<PortMethod, bool>,
    /// When set, the NEXT `install_login_session` call bumps the stored
    /// revision BEFORE the CAS check so the CAS fails with `Conflict`.
    /// One-shot.
    bump_revision_on_next_install: bool,
    /// When set, the NEXT `rotate_session` call bumps the stored revision
    /// BEFORE the CAS check so the CAS fails with `Conflict`. One-shot.
    bump_revision_on_next_rotate: bool,
    /// Tests can seed a non-zero starting revision for new scopes (e.g. to
    /// drive `revision = u64::MAX` and trigger the engine's overflow →
    /// `Internal` path).
    initial_revision_for_new_scope: Option<u64>,
}

#[allow(dead_code)]
impl TestSessionIdentity {
    /// Construct an empty identity store. Tests seed identity rows via
    /// `ensure_identity` before driving the engine.
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner {
                identities: HashMap::new(),
                scopes: HashMap::new(),
                counts: HashMap::new(),
                inject_unavailable: HashMap::new(),
                bump_revision_on_next_install: false,
                bump_revision_on_next_rotate: false,
                initial_revision_for_new_scope: None,
            }),
        }
    }

    /// Set the per-method `Unavailable` injection. The next call to that
    /// method returns `Err(SessionStoreError::Unavailable)`; the toggle
    /// clears itself after one faulted invocation.
    pub fn inject_error(&self, method: PortMethod, on: bool) {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        g.inject_unavailable.insert(method, on);
    }

    /// Toggle the "next `install_login_session` call observes the stored
    /// revision bumped by 1 BEFORE the CAS check" race-injection knob.
    /// Cleared after one invocation.
    pub fn bump_revision_on_next_install_once(&self, on: bool) {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        g.bump_revision_on_next_install = on;
    }

    /// Toggle the "next `rotate_session` call observes the stored revision
    /// bumped by 1 BEFORE the CAS check" race-injection knob. Cleared after
    /// one invocation.
    pub fn bump_revision_on_next_rotate_once(&self, on: bool) {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        g.bump_revision_on_next_rotate = on;
    }

    /// Set the revision an `ensure_identity`-seeded scope starts at. Used by
    /// the overflow test to seed `u64::MAX` so the engine's
    /// `expected.checked_add(1)` overflow hits `Internal`.
    pub fn set_initial_revision_for_new_scope(&self, rev: u64) {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        g.initial_revision_for_new_scope = Some(rev);
    }

    /// Read the per-method call count for assertions.
    pub fn call_count(&self, method: PortMethod) -> u64 {
        let g = self.inner.lock().expect("inner mutex poisoned");
        *g.counts.get(&method).unwrap_or(&0)
    }

    /// Direct (test-only) access to the live revision for a scope.
    pub fn current_revision(&self, scope: &SessionScope) -> u64 {
        let g = self.inner.lock().expect("inner mutex poisoned");
        g.scopes
            .get(&Self::scope_key(scope))
            .map(|s| s.revision)
            .unwrap_or(0)
    }

    /// Direct (test-only) access to the live token_hash for a scope.
    pub fn current_token_hash(&self, scope: &SessionScope) -> Option<String> {
        let g = self.inner.lock().expect("inner mutex poisoned");
        g.scopes
            .get(&Self::scope_key(scope))
            .and_then(|s| s.token_hash.clone())
    }

    fn scope_key(scope: &SessionScope) -> (String, String, String) {
        (scope.user_id.clone(), scope.provider.clone(), scope.env.clone())
    }

    fn bump_count(inner: &mut Inner, m: PortMethod) {
        *inner.counts.entry(m).or_insert(0) += 1;
    }

    fn take_inject_flag(inner: &mut Inner, m: PortMethod) -> bool {
        inner.inject_unavailable.remove(&m).unwrap_or(false)
    }

    /// Allocate a 12-char base62 internal id via uuid v4 — mirrors
    /// `bcs-user-identity::generate_user_id`. Tests do not need cryptographic
    /// uniqueness; uuid v4 is convenient and already in the dep graph.
    fn allocate_user_id() -> String {
        const ALPHABET: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
        let bytes = uuid::Uuid::new_v4().into_bytes();
        let mut s = String::with_capacity(12);
        for b in bytes.iter().take(12) {
            s.push(ALPHABET[(*b as usize) % ALPHABET.len()] as char);
        }
        s
    }
}

impl Default for TestSessionIdentity {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl AuthSessionIdentityPort for TestSessionIdentity {
    async fn read_session_revision(
        &self,
        scope: &SessionScope,
    ) -> Result<u64, SessionStoreError> {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        Self::bump_count(&mut g, PortMethod::ReadSessionRevision);
        if Self::take_inject_flag(&mut g, PortMethod::ReadSessionRevision) {
            return Err(SessionStoreError::Unavailable);
        }
        let key = Self::scope_key(scope);
        match g.scopes.get(&key) {
            Some(s) => Ok(s.revision),
            None => Ok(g.initial_revision_for_new_scope.unwrap_or(0)),
        }
    }

    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<SessionSnapshot>, SessionStoreError> {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        Self::bump_count(&mut g, PortMethod::GetSessionByHash);
        if Self::take_inject_flag(&mut g, PortMethod::GetSessionByHash) {
            return Err(SessionStoreError::Unavailable);
        }
        let key = Self::scope_key(scope);
        let Some(state) = g.scopes.get(&key) else {
            return Ok(None);
        };
        let Some(stored_hash) = state.token_hash.as_deref() else {
            return Ok(None);
        };
        if stored_hash.is_empty() || stored_hash != hash {
            return Ok(None);
        }
        let Some(expires_at) = state.expires_at else {
            return Ok(None);
        };
        if expires_at <= now {
            return Ok(None);
        }
        let Some(session_id) = state.session_id.as_deref() else {
            return Ok(None);
        };
        Ok(Some(SessionSnapshot {
            scope: scope.clone(),
            version: SessionVersion {
                session_id: session_id.to_string(),
                revision: state.revision,
                token_hash: stored_hash.to_string(),
            },
            expires_at,
            username: state.external_user_id.clone().unwrap_or_default(),
            display_name: state.display_name.clone(),
            avatar: state.avatar.clone(),
        }))
    }

    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        Self::bump_count(&mut g, PortMethod::InstallLoginSession);
        if Self::take_inject_flag(&mut g, PortMethod::InstallLoginSession) {
            return Err(SessionStoreError::Unavailable);
        }
        let key = Self::scope_key(&command.scope);
        // Race injection: bump stored revision BEFORE the CAS check.
        // Cleared after one invocation. Bumps happen on a registered scope;
        // if the scope is absent, this branch is a no-op for the bump (there
        // is nothing to bump) and the subsequent CAS check returns Conflict.
        if g.bump_revision_on_next_install {
            g.bump_revision_on_next_install = false;
            let state = g.scopes.entry(key.clone()).or_insert_with(|| ScopeState::fresh(0));
            state.revision = state.revision.saturating_add(1);
        }
        let Some(state) = g.scopes.get_mut(&key) else {
            // Identity row missing — store rejects, returns Conflict (matches
            // memory impl's `find_record_mut` None branch).
            return Ok(SessionWrite::Conflict);
        };
        if state.revision != command.expected_revision {
            return Ok(SessionWrite::Conflict);
        }
        let new_revision = command
            .expected_revision
            .checked_add(1)
            .ok_or(SessionStoreError::CorruptRecord)?;
        state.revision = new_revision;
        state.session_id = Some(command.next.session_id);
        state.token_hash = Some(command.next.token_hash);
        state.expires_at = Some(command.expires_at);
        Ok(SessionWrite::Applied)
    }

    async fn rotate_session(
        &self,
        command: RotateSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        Self::bump_count(&mut g, PortMethod::RotateSession);
        if Self::take_inject_flag(&mut g, PortMethod::RotateSession) {
            return Err(SessionStoreError::Unavailable);
        }
        let key = Self::scope_key(&command.scope);
        // Race injection: bump stored revision BEFORE the CAS check. One-shot.
        if g.bump_revision_on_next_rotate {
            g.bump_revision_on_next_rotate = false;
            if let Some(state) = g.scopes.get_mut(&key) {
                state.revision = state.revision.saturating_add(1);
            }
        }
        let Some(state) = g.scopes.get_mut(&key) else {
            return Ok(SessionWrite::Conflict);
        };
        if state.revision != command.expected.revision {
            return Ok(SessionWrite::Conflict);
        }
        if state.session_id.as_deref() != Some(command.expected.session_id.as_str()) {
            return Ok(SessionWrite::Conflict);
        }
        let Some(stored_hash) = state.token_hash.as_deref() else {
            return Ok(SessionWrite::Conflict);
        };
        if stored_hash.is_empty() || stored_hash != command.expected.token_hash {
            return Ok(SessionWrite::Conflict);
        }
        let Some(stored_expires_at) = state.expires_at else {
            return Ok(SessionWrite::Conflict);
        };
        if stored_expires_at <= command.now {
            return Ok(SessionWrite::Conflict);
        }
        let new_revision = command
            .expected
            .revision
            .checked_add(1)
            .ok_or(SessionStoreError::CorruptRecord)?;
        state.revision = new_revision;
        state.session_id = Some(command.next.session_id);
        state.token_hash = Some(command.next.token_hash);
        state.expires_at = Some(command.expires_at);
        Ok(SessionWrite::Applied)
    }

    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError> {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        Self::bump_count(&mut g, PortMethod::RevokeSession);
        if Self::take_inject_flag(&mut g, PortMethod::RevokeSession) {
            return Err(SessionStoreError::Unavailable);
        }
        let key = Self::scope_key(scope);
        let Some(state) = g.scopes.get_mut(&key) else {
            return Ok(SessionRevoke::NotCurrent);
        };
        if state.token_hash.is_none() {
            return Ok(SessionRevoke::NotCurrent);
        }
        if state.session_id.as_deref() != Some(session_id) {
            return Ok(SessionRevoke::NotCurrent);
        }
        let new_revision = state
            .revision
            .checked_add(1)
            .ok_or(SessionStoreError::CorruptRecord)?;
        state.revision = new_revision;
        state.token_hash = None;
        state.expires_at = None;
        Ok(SessionRevoke::Revoked)
    }

    async fn ensure_identity(
        &self,
        provider: &str,
        external_user_id: &str,
        name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, SessionStoreError> {
        let mut g = self.inner.lock().expect("inner mutex poisoned");
        Self::bump_count(&mut g, PortMethod::EnsureIdentity);
        if Self::take_inject_flag(&mut g, PortMethod::EnsureIdentity) {
            return Err(SessionStoreError::Unavailable);
        }
        let identity_key = (provider.to_string(), external_user_id.to_string(), env.to_string());
        let user_id = match g.identities.get(&identity_key) {
            Some(rec) => rec.user_id.clone(),
            None => {
                let uid = Self::allocate_user_id();
                g.identities.insert(identity_key.clone(), IdentityRecord { user_id: uid.clone() });
                // Seed the scope's session state so the engine can install
                // against a fresh `(user_id, provider, env)` row.
                let scope_key = (uid.clone(), provider.to_string(), env.to_string());
                let init_rev = g.initial_revision_for_new_scope.unwrap_or(0);
                g.scopes
                    .entry(scope_key)
                    .or_insert_with(|| ScopeState::fresh(init_rev));
                uid
            }
        };
        // Write-through the display fields onto the scope's state so the
        // snapshot returned by `get_session_by_hash` carries them. By the
        // time of a `refresh` call the scope exists, so this just refreshes.
        let scope_key = (user_id.clone(), provider.to_string(), env.to_string());
        if let Some(state) = g.scopes.get_mut(&scope_key) {
            state.external_user_id = Some(external_user_id.to_string());
            if let Some(n) = name {
                state.display_name = Some(n.to_string());
            }
            if let Some(a) = avatar {
                state.avatar = Some(a.to_string());
            }
        }
        Ok(user_id)
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod internal_tests {
    //! Sanity tests on the fake itself so the engine tests can rely on it.
    use super::*;

    fn scope(uid: &str, prov: &str, env: &str) -> SessionScope {
        SessionScope {
            user_id: uid.to_string(),
            provider: prov.to_string(),
            env: env.to_string(),
        }
    }

    #[tokio::test]
    async fn fresh_scope_reports_revision_zero() {
        let fake = TestSessionIdentity::new();
        let s = scope("u", "github", "local");
        assert_eq!(fake.read_session_revision(&s).await.unwrap(), 0);
        assert_eq!(fake.call_count(PortMethod::ReadSessionRevision), 1);
    }

    #[tokio::test]
    async fn install_on_missing_identity_row_yields_conflict() {
        let fake = TestSessionIdentity::new();
        let s = scope("u-not-registered", "github", "local");
        let out = fake
            .install_login_session(InstallSession {
                scope: s,
                expected_revision: 0,
                next: SessionVersion {
                    session_id: "sid-a".into(),
                    revision: 1,
                    token_hash: "h-a".into(),
                },
                expires_at: 5_000_000_000,
            })
            .await
            .unwrap();
        assert_eq!(out, SessionWrite::Conflict);
    }

    #[tokio::test]
    async fn ensure_identity_then_install_then_rotate_then_revoke() {
        let fake = TestSessionIdentity::new();
        let uid = fake
            .ensure_identity("github", "ext-1", None, None, "local")
            .await
            .unwrap();
        let s = scope(&uid, "github", "local");
        assert_eq!(fake.read_session_revision(&s).await.unwrap(), 0);
        let out = fake
            .install_login_session(InstallSession {
                scope: s.clone(),
                expected_revision: 0,
                next: SessionVersion {
                    session_id: "sid-a".into(),
                    revision: 1,
                    token_hash: "h-a".into(),
                },
                expires_at: 5_000_000_000,
            })
            .await
            .unwrap();
        assert_eq!(out, SessionWrite::Applied);
        let snap = fake
            .get_session_by_hash(&s, "h-a", 1_000_000_000)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(snap.version.revision, 1);
        let rot = fake
            .rotate_session(RotateSession {
                scope: s.clone(),
                expected: SessionVersion {
                    session_id: "sid-a".into(),
                    revision: 1,
                    token_hash: "h-a".into(),
                },
                next: SessionVersion {
                    session_id: "sid-b".into(),
                    revision: 2,
                    token_hash: "h-b".into(),
                },
                expires_at: 6_000_000_000,
                now: 2_000_000_000,
            })
            .await
            .unwrap();
        assert_eq!(rot, SessionWrite::Applied);
        assert!(fake.get_session_by_hash(&s, "h-a", 2_000_000_001).await.unwrap().is_none());
        let rev = fake.revoke_session(&s, "sid-b").await.unwrap();
        assert_eq!(rev, SessionRevoke::Revoked);
        assert!(fake.get_session_by_hash(&s, "h-b", 2_000_000_002).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn bump_revision_injection_yields_conflict() {
        let fake = TestSessionIdentity::new();
        let uid = fake
            .ensure_identity("github", "ext-1", None, None, "local")
            .await
            .unwrap();
        let s = scope(&uid, "github", "local");
        fake.install_login_session(InstallSession {
            scope: s.clone(),
            expected_revision: 0,
            next: SessionVersion {
                session_id: "sid-a".into(),
                revision: 1,
                token_hash: "h-a".into(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .unwrap();
        fake.bump_revision_on_next_install_once(true);
        let out = fake
            .install_login_session(InstallSession {
                scope: s.clone(),
                expected_revision: 1,
                next: SessionVersion {
                    session_id: "sid-b".into(),
                    revision: 2,
                    token_hash: "h-b".into(),
                },
                expires_at: 5_000_000_000,
            })
            .await
            .unwrap();
        assert_eq!(out, SessionWrite::Conflict);
        assert_eq!(fake.current_revision(&s), 2);
        // No overwrite: the conflict path did not store sid-b/h-b.
        assert_eq!(fake.current_token_hash(&s).as_deref(), Some("h-a"));
        // The injection is one-shot — a fresh CAS at expected=2 succeeds.
        let out2 = fake
            .install_login_session(InstallSession {
                scope: s,
                expected_revision: 2,
                next: SessionVersion {
                    session_id: "sid-c".into(),
                    revision: 3,
                    token_hash: "h-c".into(),
                },
                expires_at: 5_000_000_000,
            })
            .await
            .unwrap();
        assert_eq!(out2, SessionWrite::Applied);
    }

    #[tokio::test]
    async fn bump_revision_on_next_rotate_yields_conflict() {
        let fake = TestSessionIdentity::new();
        let uid = fake
            .ensure_identity("github", "ext-1", None, None, "local")
            .await
            .unwrap();
        let s = scope(&uid, "github", "local");
        fake.install_login_session(InstallSession {
            scope: s.clone(),
            expected_revision: 0,
            next: SessionVersion {
                session_id: "sid-a".into(),
                revision: 1,
                token_hash: "h-a".into(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .unwrap();
        fake.bump_revision_on_next_rotate_once(true);
        let out = fake
            .rotate_session(RotateSession {
                scope: s.clone(),
                expected: SessionVersion {
                    session_id: "sid-a".into(),
                    revision: 1,
                    token_hash: "h-a".into(),
                },
                next: SessionVersion {
                    session_id: "sid-b".into(),
                    revision: 2,
                    token_hash: "h-b".into(),
                },
                expires_at: 6_000_000_000,
                now: 1_000_000_000,
            })
            .await
            .unwrap();
        assert_eq!(out, SessionWrite::Conflict);
        assert_eq!(fake.current_revision(&s), 2);
        // No overwrite.
        assert_eq!(fake.current_token_hash(&s).as_deref(), Some("h-a"));
    }

    #[tokio::test]
    async fn unavailable_injection_round_trips() {
        let fake = TestSessionIdentity::new();
        let s = scope("u", "github", "local");
        fake.inject_error(PortMethod::ReadSessionRevision, true);
        let err = fake.read_session_revision(&s).await.unwrap_err();
        assert!(matches!(err, SessionStoreError::Unavailable));
        // Toggle cleared after one invocation.
        let _ = fake.read_session_revision(&s).await.unwrap();
    }

    #[tokio::test]
    async fn ensure_identity_idempotent_returns_same_uid() {
        let fake = TestSessionIdentity::new();
        let uid1 = fake
            .ensure_identity("github", "ext-1", Some("N"), None, "local")
            .await
            .unwrap();
        let uid2 = fake
            .ensure_identity("github", "ext-1", Some("N2"), None, "local")
            .await
            .unwrap();
        assert_eq!(uid1, uid2);
    }
}
