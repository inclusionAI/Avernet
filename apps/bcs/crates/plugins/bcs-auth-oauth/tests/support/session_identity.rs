//! `TestSessionIdentity` — strict `AuthSessionIdentityPort` double for the
//! OAuth session-engine tests (V1 API auth plugin chain, Task 8).
//!
//! Mirrors the Task 3 memory driver's CAS semantics in miniature:
//! - ONE `Mutex` guards scope/version/hash/expiry state and the identity
//!   rows, so a compare-and-set can never read one state and write another;
//! - every port call is appended to an operation log so tests assert
//!   fail-fast ordering (e.g. an env mismatch never reaches storage) and
//!   no-retry discipline (exactly one CAS attempt);
//! - per-operation failures are injectable as closed
//!   [`SessionStoreError`] categories.
//!
//! This double stays inside the plugin crate's test tree so plugin tests do
//! not depend back on bootstrap; real store/bridge coverage is Task 13's.

// Shared fixture: each test binary compiles only the slice of this module it
// uses, so unused-helper warnings are expected by design.
#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;
use bcs_auth_api::session::{
    AuthSessionIdentityPort, InstallSession, RotateSession, SessionRevoke, SessionScope,
    SessionSnapshot, SessionStoreError, SessionVersion, SessionWrite,
};

/// Scope address: `(user_id, provider, env)`. `env` participates in the
/// address, so the same user/provider pair has independent sessions per
/// environment.
type ScopeKey = (String, String, String);

/// External identity key: `(provider, external_user_id, env)`.
type ExternalKey = (String, String, String);

/// The maximum representable session revision: SQL persists revisions in a
/// signed BIGINT column, so a bump past `i64::MAX` is corruption, never a
/// valid next version.
const MAX_REVISION: u64 = i64::MAX as u64;

struct SessionRecord {
    session_id: String,
    revision: u64,
    token_hash: String,
    expires_at: u64,
}

struct IdentityRow {
    user_id: String,
    name: Option<String>,
    avatar: Option<String>,
}

#[derive(Default)]
struct Inner {
    by_external: HashMap<ExternalKey, IdentityRow>,
    scope_index: HashMap<ScopeKey, ExternalKey>,
    sessions: HashMap<ScopeKey, SessionRecord>,
    ops: Vec<&'static str>,
    failures: HashMap<&'static str, SessionStoreError>,
    force_install_conflict: bool,
    force_rotate_conflict: bool,
    next_user_seq: u64,
}

/// Injectable strict identity-port double (see module docs).
pub struct TestSessionIdentity {
    inner: Mutex<Inner>,
}

impl TestSessionIdentity {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner::default()),
        }
    }

    /// Fail the next-and-subsequent calls of `op` with the injected
    /// category. Op names are the port method names, e.g.
    /// `"get_session_by_hash"`, `"rotate_session"`.
    pub fn fail_with(&self, op: &'static str, error: SessionStoreError) {
        let mut inner = self.inner.lock().expect("test double lock");
        inner.failures.insert(op, error);
    }

    /// Clear every injected failure and forced-CAS flag.
    pub fn clear_faults(&self) {
        let mut inner = self.inner.lock().expect("test double lock");
        inner.failures.clear();
        inner.force_install_conflict = false;
        inner.force_rotate_conflict = false;
    }

    /// Force every `install_login_session` CAS to report `Conflict` while
    /// `read_session_revision` still returns the stored revision — models a
    /// concurrent login batch winning between the read and the install.
    pub fn force_install_conflict(&self) {
        let mut inner = self.inner.lock().expect("test double lock");
        inner.force_install_conflict = true;
    }

    /// Force every `rotate_session` CAS to report `Conflict` while reads are
    /// unaffected — models a concurrent refresh winning the rotation race.
    pub fn force_rotate_conflict(&self) {
        let mut inner = self.inner.lock().expect("test double lock");
        inner.force_rotate_conflict = true;
    }

    /// Operation log (in call order): each port method name as it was
    /// invoked.
    pub fn operations(&self) -> Vec<&'static str> {
        self.inner.lock().expect("test double lock").ops.clone()
    }

    /// Whether `op` was called at least once.
    pub fn called(&self, op: &str) -> bool {
        self.operations().iter().any(|name| *name == op)
    }

    /// Test helper: seed an identity row plus the CURRENT session version
    /// for a scope, bypassing the port's own install path. The identity row
    /// is what a real `ensure_identity` would have materialized.
    #[allow(clippy::needless_pass_by_value, reason = "test helper ergonomics")]
    pub fn seed_identity_and_session(
        &self,
        scope: SessionScope,
        version: SessionVersion,
        expires_at: u64,
        name: Option<&str>,
    ) {
        let mut inner = self.inner.lock().expect("test double lock");
        let external_key = (
            scope.provider.clone(),
            format!("ext-{}", scope.user_id),
            scope.env.clone(),
        );
        inner.by_external.insert(
            external_key.clone(),
            IdentityRow {
                user_id: scope.user_id.clone(),
                name: name.map(str::to_string),
                avatar: None,
            },
        );
        inner.scope_index.insert(
            (scope.user_id.clone(), scope.provider.clone(), scope.env.clone()),
            external_key,
        );
        inner.sessions.insert(
            (scope.user_id, scope.provider, scope.env),
            SessionRecord {
                session_id: version.session_id,
                revision: version.revision,
                token_hash: version.token_hash,
                expires_at,
            },
        );
    }

    /// Test helper: return the current session id/revision/hash/expires_at
    /// for a scope, if a session record exists.
    #[allow(clippy::needless_pass_by_value, reason = "test helper ergonomics")]
    pub fn current_version(&self, scope: &SessionScope) -> Option<SessionVersion> {
        let inner = self.inner.lock().expect("test double lock");
        let key = (scope.user_id.clone(), scope.provider.clone(), scope.env.clone());
        inner.sessions.get(&key).map(|record| SessionVersion {
            session_id: record.session_id.clone(),
            revision: record.revision,
            token_hash: record.token_hash.clone(),
        })
    }

    fn scope_key(scope: &SessionScope) -> ScopeKey {
        (scope.user_id.clone(), scope.provider.clone(), scope.env.clone())
    }

    fn injected_failure(inner: &Inner, op: &'static str) -> Option<SessionStoreError> {
        inner.failures.get(&op).map(|error| match error {
            SessionStoreError::Unavailable => SessionStoreError::Unavailable,
            SessionStoreError::CorruptRecord => SessionStoreError::CorruptRecord,
        })
    }

    fn record(inner: &mut Inner, op: &'static str) {
        inner.ops.push(op);
    }

    fn snapshot(
        scope: &SessionScope,
        record: &SessionRecord,
        identity: &IdentityRow,
    ) -> SessionSnapshot {
        SessionSnapshot {
            scope: scope.clone(),
            version: SessionVersion {
                session_id: record.session_id.clone(),
                revision: record.revision,
                token_hash: record.token_hash.clone(),
            },
            expires_at: record.expires_at,
            // The joined display fields: username passes through the
            // identity row's trusted name, EMPTY for a nameless identity —
            // no internal-ID fallback here (plan §7.3 defers that to the
            // delivery projection, Tasks 11/12).
            username: identity.name.clone().unwrap_or_default(),
            display_name: identity.name.clone(),
            avatar: identity.avatar.clone(),
        }
    }
}

impl Default for TestSessionIdentity {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl AuthSessionIdentityPort for TestSessionIdentity {
    async fn ensure_identity(
        &self,
        provider: &str,
        external_user_id: &str,
        name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, SessionStoreError> {
        let mut inner = self.inner.lock().expect("test double lock");
        Self::record(&mut inner, "ensure_identity");
        if let Some(error) = Self::injected_failure(&inner, "ensure_identity") {
            return Err(error);
        }
        let key = (provider.to_string(), external_user_id.to_string(), env.to_string());
        if let Some(existing) = inner.by_external.get(&key) {
            return Ok(existing.user_id.clone());
        }
        inner.next_user_seq += 1;
        let user_id = format!("user-{}", inner.next_user_seq);
        inner.by_external.insert(
            key.clone(),
            IdentityRow {
                user_id: user_id.clone(),
                name: name.map(str::to_string),
                avatar: avatar.map(str::to_string),
            },
        );
        let external_key = key;
        inner
            .scope_index
            .entry((user_id.clone(), provider.to_string(), env.to_string()))
            .or_insert(external_key);
        Ok(user_id)
    }

    async fn read_session_revision(&self, scope: &SessionScope) -> Result<u64, SessionStoreError> {
        let mut inner = self.inner.lock().expect("test double lock");
        Self::record(&mut inner, "read_session_revision");
        if let Some(error) = Self::injected_failure(&inner, "read_session_revision") {
            return Err(error);
        }
        Ok(inner
            .sessions
            .get(&Self::scope_key(scope))
            .map(|record| record.revision)
            .unwrap_or(0))
    }

    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<SessionSnapshot>, SessionStoreError> {
        let mut inner = self.inner.lock().expect("test double lock");
        Self::record(&mut inner, "get_session_by_hash");
        if let Some(error) = Self::injected_failure(&inner, "get_session_by_hash") {
            return Err(error);
        }
        let Some(record) = inner.sessions.get(&Self::scope_key(scope)) else {
            return Ok(None);
        };
        if record.token_hash.is_empty() || record.token_hash != hash || record.expires_at <= now {
            return Ok(None);
        }
        let external_key = inner
            .scope_index
            .get(&Self::scope_key(scope))
            .cloned()
            .ok_or(SessionStoreError::CorruptRecord)?;
        let identity = inner
            .by_external
            .get(&external_key)
            .ok_or(SessionStoreError::CorruptRecord)?;
        Ok(Some(Self::snapshot(scope, record, identity)))
    }

    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        let mut inner = self.inner.lock().expect("test double lock");
        Self::record(&mut inner, "install_login_session");
        if let Some(error) = Self::injected_failure(&inner, "install_login_session") {
            return Err(error);
        }
        if inner.force_install_conflict {
            return Ok(SessionWrite::Conflict);
        }
        let key = Self::scope_key(&command.scope);
        if !inner.scope_index.contains_key(&key) {
            // A session write never auto-creates the identity row.
            return Ok(SessionWrite::Conflict);
        }
        let current_revision = inner
            .sessions
            .get(&key)
            .map(|record| record.revision)
            .unwrap_or(0);
        if current_revision != command.expected_revision {
            return Ok(SessionWrite::Conflict);
        }
        // The next revision must advance by exactly checked_add(1) within
        // BIGINT bounds — the same discipline the engine applies before
        // calling; a store-side breach is corruption, not a conflict.
        let Some(next_revision) = command.expected_revision.checked_add(1) else {
            return Err(SessionStoreError::CorruptRecord);
        };
        if next_revision > MAX_REVISION || next_revision != command.next.revision {
            return Err(SessionStoreError::CorruptRecord);
        }
        inner.sessions.insert(
            key,
            SessionRecord {
                session_id: command.next.session_id,
                revision: command.next.revision,
                token_hash: command.next.token_hash,
                expires_at: command.expires_at,
            },
        );
        Ok(SessionWrite::Applied)
    }

    async fn rotate_session(&self, command: RotateSession) -> Result<SessionWrite, SessionStoreError> {
        let mut inner = self.inner.lock().expect("test double lock");
        Self::record(&mut inner, "rotate_session");
        if let Some(error) = Self::injected_failure(&inner, "rotate_session") {
            return Err(error);
        }
        if inner.force_rotate_conflict {
            return Ok(SessionWrite::Conflict);
        }
        // A rotation stays inside one session id.
        if command.next.session_id != command.expected.session_id {
            return Ok(SessionWrite::Conflict);
        }
        let key = Self::scope_key(&command.scope);
        let matches_stored = match inner.sessions.get(&key) {
            None => false,
            Some(record) => {
                record.session_id == command.expected.session_id
                    && record.revision == command.expected.revision
                    && record.token_hash == command.expected.token_hash
                    && record.expires_at > command.now
            }
        };
        if !matches_stored {
            return Ok(SessionWrite::Conflict);
        }
        let Some(next_revision) = command.expected.revision.checked_add(1) else {
            return Err(SessionStoreError::CorruptRecord);
        };
        if next_revision > MAX_REVISION || next_revision != command.next.revision {
            return Err(SessionStoreError::CorruptRecord);
        }
        inner.sessions.insert(
            key,
            SessionRecord {
                session_id: command.next.session_id,
                revision: command.next.revision,
                token_hash: command.next.token_hash,
                expires_at: command.expires_at,
            },
        );
        Ok(SessionWrite::Applied)
    }

    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError> {
        let mut inner = self.inner.lock().expect("test double lock");
        Self::record(&mut inner, "revoke_session");
        if let Some(error) = Self::injected_failure(&inner, "revoke_session") {
            return Err(error);
        }
        let key = Self::scope_key(scope);
        let Some(record) = inner.sessions.get_mut(&key) else {
            return Ok(SessionRevoke::NotCurrent);
        };
        // By-sid revoke: does NOT require the old hash to still be live;
        // a rotated descendant (same sid, newer hash) is revoked with the
        // requested instance. An empty hash means already revoked.
        if record.session_id != session_id || record.token_hash.is_empty() {
            return Ok(SessionRevoke::NotCurrent);
        }
        let Some(next_revision) = record.revision.checked_add(1) else {
            return Err(SessionStoreError::CorruptRecord);
        };
        if next_revision > MAX_REVISION {
            return Err(SessionStoreError::CorruptRecord);
        }
        record.revision = next_revision;
        record.token_hash.clear();
        record.expires_at = 0;
        Ok(SessionRevoke::Revoked)
    }
}
