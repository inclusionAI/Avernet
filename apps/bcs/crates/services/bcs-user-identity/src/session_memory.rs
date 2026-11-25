//! Strict atomic in-memory auth-session state and operations (V1 API auth
//! plugin chain).
//!
//! [`SessionIndex`] maps one scope key — `(user_id, provider, env)` — to the
//! location of the single shared identity record plus that scope's current
//! session record. This mirrors the `session_id` / `session_revision` /
//! `session_expires_at` columns on `bcs_user_identities`: there is no second
//! user table, and snapshots join `username` / `display_name` / `avatar`
//! straight from the identity row at read time.
//!
//! Every operation here is synchronous; the caller in [`super::memory`] holds
//! the ONE repository lock across the whole compare-and-set, so a CAS can
//! never read one state and write against another. Strict queries resolve
//! scope→identity through this index — they never scan the identity map.

use std::collections::HashMap;

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionScope, AuthSessionSnapshot, AuthSessionStoreError,
    AuthSessionVersion, AuthSessionWrite, InstallAuthSession, RotateAuthSession,
};
use bcs_service_api::UserIdentity;

use super::memory::ExternalKey;

/// The maximum representable session revision: SQL persists the revision in a
/// signed BIGINT column, so a bump that would exceed `i64::MAX` is persisted
/// corruption, never a valid next version.
const MAX_REVISION: u64 = i64::MAX as u64;

/// Scope address of one session: `(user_id, provider, env)`. `env`
/// participates in the address, so the same user/provider pair has
/// independent sessions per environment.
pub(crate) type SessionScopeKey = (String, String, String);

/// The current session record for one scope. `token_hash` is empty exactly
/// when the session is revoked: the session id and the bumped revision
/// survive the revoke, but the hash stops resolving.
pub(crate) struct SessionRecord {
    pub(crate) session_id: String,
    pub(crate) revision: u64,
    pub(crate) token_hash: String,
    pub(crate) expires_at: u64,
}

/// One scope's index entry: where its single shared identity record lives in
/// the identity map, plus the current session record (`None` until the first
/// login installs one).
struct ScopeEntry {
    identity_key: ExternalKey,
    session: Option<SessionRecord>,
}

/// Advance a revision by exactly `checked_add(1)` within SQL signed BIGINT
/// bounds. Overflow — u64 or BIGINT — is mapped to `CorruptRecord`: a wrapped
/// or negative revision must never reach the store.
fn bump_revision(current: u64) -> Result<u64, AuthSessionStoreError> {
    let next = current
        .checked_add(1)
        .ok_or(AuthSessionStoreError::CorruptRecord)?;
    if next > MAX_REVISION {
        return Err(AuthSessionStoreError::CorruptRecord);
    }
    Ok(next)
}

/// Scope-keyed session index. Maintained in the same lock domain as the
/// identity map so identity writes and session compare-and-sets are atomic
/// against one shared state.
#[derive(Default)]
pub(crate) struct SessionIndex {
    by_scope: HashMap<SessionScopeKey, ScopeEntry>,
}

impl SessionIndex {
    fn scope_key(scope: &AuthSessionScope) -> SessionScopeKey {
        (scope.user_id.clone(), scope.provider.clone(), scope.env.clone())
    }

    /// Record where a newly created identity row lives, keyed by its scope
    /// address `(user_id, provider, env)`. Session operations resolve the
    /// identity through this index instead of scanning the identity map.
    pub(crate) fn register_identity(&mut self, scope: SessionScopeKey, identity_key: ExternalKey) {
        self.by_scope
            .entry(scope)
            .or_insert_with(move || ScopeEntry {
                identity_key,
                session: None,
            });
    }

    /// Current revision for one scope; `0` when no session is installed yet.
    pub(crate) fn current_revision(&self, scope: &AuthSessionScope) -> u64 {
        self.by_scope
            .get(&Self::scope_key(scope))
            .and_then(|entry| entry.session.as_ref())
            .map(|session| session.revision)
            .unwrap_or(0)
    }

    /// Resolve the current session snapshot by token hash under its scope.
    /// Unknown, rotated-out, revoked, and expired hashes are absence
    /// (`Ok(None)`); a dangling identity pointer is `CorruptRecord`.
    pub(crate) fn get_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
        by_external: &HashMap<ExternalKey, UserIdentity>,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError> {
        let Some(entry) = self.by_scope.get(&Self::scope_key(scope)) else {
            return Ok(None);
        };
        let Some(session) = entry.session.as_ref() else {
            return Ok(None);
        };
        if session.token_hash.is_empty()
            || session.token_hash != hash
            || session.expires_at <= now
        {
            return Ok(None);
        }
        let identity = by_external
            .get(&entry.identity_key)
            .ok_or(AuthSessionStoreError::CorruptRecord)?;
        Ok(Some(Self::snapshot(scope, session, identity)))
    }

    /// Compare-and-swap install of a new login session. The stored revision
    /// must still equal `expected_revision` and the next version's revision
    /// must advance by exactly `checked_add(1)` within BIGINT bounds.
    /// Anything else — a missing identity row included — is a `Conflict`;
    /// a session write never auto-creates the identity row.
    pub(crate) fn install(
        &mut self,
        command: InstallAuthSession,
        by_external: &HashMap<ExternalKey, UserIdentity>,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        let key = Self::scope_key(&command.scope);
        let Some(entry) = self.by_scope.get_mut(&key) else {
            return Ok(AuthSessionWrite::Conflict);
        };
        if !by_external.contains_key(&entry.identity_key) {
            return Err(AuthSessionStoreError::CorruptRecord);
        }
        let current_revision = entry
            .session
            .as_ref()
            .map(|session| session.revision)
            .unwrap_or(0);
        if current_revision != command.expected_revision {
            return Ok(AuthSessionWrite::Conflict);
        }
        let next_revision = bump_revision(command.expected_revision)?;
        if next_revision != command.next.revision {
            return Ok(AuthSessionWrite::Conflict);
        }
        entry.session = Some(SessionRecord {
            session_id: command.next.session_id,
            revision: command.next.revision,
            token_hash: command.next.token_hash,
            expires_at: command.expires_at,
        });
        Ok(AuthSessionWrite::Applied)
    }

    /// Compare-and-swap refresh rotation: `next` replaces `expected` only when
    /// `expected` is still the stored, unexpired version and `next` keeps the
    /// same session id at `checked_add(1)` revision. A lost race is
    /// `Conflict` — never an error, never retried into an unconditional
    /// write.
    pub(crate) fn rotate(
        &mut self,
        command: RotateAuthSession,
        by_external: &HashMap<ExternalKey, UserIdentity>,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        // A rotation stays inside one session id.
        if command.next.session_id != command.expected.session_id {
            return Ok(AuthSessionWrite::Conflict);
        }
        let key = Self::scope_key(&command.scope);
        let Some(entry) = self.by_scope.get_mut(&key) else {
            return Ok(AuthSessionWrite::Conflict);
        };
        if !by_external.contains_key(&entry.identity_key) {
            return Err(AuthSessionStoreError::CorruptRecord);
        }
        let matches_stored = match entry.session.as_ref() {
            None => false,
            Some(session) => {
                session.session_id == command.expected.session_id
                    && session.revision == command.expected.revision
                    && session.token_hash == command.expected.token_hash
                    && session.expires_at > command.now
            }
        };
        if !matches_stored {
            return Ok(AuthSessionWrite::Conflict);
        }
        // The CAS won against the current stored version; the next revision
        // advances by exactly `checked_add(1)` within BIGINT bounds.
        let next_revision = bump_revision(command.expected.revision)?;
        if next_revision != command.next.revision {
            return Ok(AuthSessionWrite::Conflict);
        }
        entry.session = Some(SessionRecord {
            session_id: command.next.session_id,
            revision: command.next.revision,
            token_hash: command.next.token_hash,
            expires_at: command.expires_at,
        });
        Ok(AuthSessionWrite::Applied)
    }

    /// Revoke the current session by id: bump the revision
    /// (`checked_add(1)`, BIGINT-bounded) and clear the hash and expiry so
    /// the session stops resolving. A revoke naming an unknown, rotated-out,
    /// or already-revoked session is `NotCurrent`, not an error.
    pub(crate) fn revoke(
        &mut self,
        scope: &AuthSessionScope,
        session_id: &str,
        by_external: &HashMap<ExternalKey, UserIdentity>,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
        let key = Self::scope_key(scope);
        let Some(entry) = self.by_scope.get_mut(&key) else {
            return Ok(AuthSessionRevoke::NotCurrent);
        };
        if !by_external.contains_key(&entry.identity_key) {
            return Err(AuthSessionStoreError::CorruptRecord);
        }
        let Some(session) = entry.session.as_mut() else {
            return Ok(AuthSessionRevoke::NotCurrent);
        };
        if session.session_id != session_id || session.token_hash.is_empty() {
            return Ok(AuthSessionRevoke::NotCurrent);
        }
        session.revision = bump_revision(session.revision)?;
        session.token_hash.clear();
        session.expires_at = 0;
        Ok(AuthSessionRevoke::Revoked)
    }

    /// Join the identity row's display fields into the read model: the
    /// internal `user_name` (initialized from the first login's external
    /// name) is the primary username, the provider's latest
    /// `external_user_name` is `display_name`.
    fn snapshot(
        scope: &AuthSessionScope,
        session: &SessionRecord,
        identity: &UserIdentity,
    ) -> AuthSessionSnapshot {
        AuthSessionSnapshot {
            scope: scope.clone(),
            version: AuthSessionVersion {
                session_id: session.session_id.clone(),
                revision: session.revision,
                token_hash: session.token_hash.clone(),
            },
            expires_at: session.expires_at,
            username: identity
                .user_name
                .clone()
                .or_else(|| identity.external_user_name.clone())
                .unwrap_or_default(),
            display_name: identity.external_user_name.clone(),
            avatar: identity.avatar.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn revision_bump_uses_checked_add_within_bigint() {
        use bcs_service_api::port::repo::auth_session::AuthSessionStoreError;
        assert_eq!(bump_revision(0).unwrap(), 1);
        assert_eq!(bump_revision(41).unwrap(), 42);
        // A revision at the signed BIGINT ceiling — or past any representable
        // u64 — is persisted corruption mapped to CorruptRecord, never a
        // wrapped, negative, or otherwise silent revision.
        assert!(matches!(
            bump_revision(i64::MAX as u64),
            Err(AuthSessionStoreError::CorruptRecord)
        ));
        assert!(matches!(
            bump_revision(u64::MAX),
            Err(AuthSessionStoreError::CorruptRecord)
        ));
    }
}
