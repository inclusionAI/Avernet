//! In-memory `AuthSessionRepoPort` impl for [`MemoryUserIdentityRepo`].
//!
//! The trait is implemented against the SAME `by_external` map and the SAME
//! per-scope [`crate::memory::MemoryIdentityRecord`] as the legacy
//! [`UserIdentityRepoPort`](bcs_service_api::UserIdentityRepoPort). The
//! session-state fields ([`MemorySessionState`]) attach to that record; there
//! is no second user table and no parallel session map keyed by session-id.
//!
//! # Single-lock atomicity
//!
//! Every operation takes `self.by_external.write()` (or `.read()` for the
//! read-only `read_session_revision` / `get_session_by_hash`) exactly once,
//! holds it across the entire CAS critical section — find the record by
//! `scope`, check the preconditions, mutate the session-state half of that
//! record, drop the lock — and never takes a second lock. Two concurrent
//! `rotate_session` calls therefore serialize strictly through the tokio
//! `RwLock`; one observes the other's write and returns `Conflict`.
//!
//! # Read-revision → pre-sign → CAS protocol
//!
//! `install_login_session` MATCHES when the stored `session.revision` equals
//! `command.expected_revision`; on match it writes `command.next.{session_id,
//! token_hash}` and `command.expires_at` onto the record's `session` half and
//! bumps the stored revision to `command.expected_revision.checked_add(1)`.
//! `rotate_session` is similar but additionally requires the stored
//! `session_id`, `revision`, and `token_hash` to all match `command.expected`
//! AND requires `session.expires_at > command.now` (the live token must not
//! be expired). On match it writes `command.next` and bumps the stored
//! revision to `command.expected.revision.checked_add(1)`. A missing
//! identity row, an empty/`None` stored `token_hash`, a `session_id`/`revision`
//! mismatch, or an expired session all return `Ok(AuthSessionWrite::Conflict)`
//! (a success-typed enum, not an `AuthSessionStoreError`).
//!
//! # `next.revision` hardening
//!
//! The plan states the SQL target requires `next.revision` to equal
//! `expected.revision + 1`; the trait contract pins the same invariant on the
//! stored value (the stored revision MUST be `expected.checked_add(1)` after
//! success). This in-memory impl does NOT trust `command.next.revision` and
//! instead writes `command.expected_revision.checked_add(1)` (for install) or
//! `command.expected.revision.checked_add(1)` (for rotate) directly. A caller
//! that supplies a contradictory `next.revision` is silently hardened: the
//! stored counter observes `expected + 1` regardless. The companion test
//! `install_ignores_next_revision_enforces_expected_plus_one` asserts this
//! from the public API. `checked_add` overflow is surfaced as
//! [`AuthSessionStoreError::CorruptRecord`] (the persisted counter is
//! unusable), never wrapped to `Ok(Applied)` or `Ok(Conflict)`.
//!
//! # Revoke semantics
//!
//! `revoke_session` MATCHES when the stored `session_id` equals the given
//! `session_id` AND the stored `token_hash` is non-empty
//! (`Some(non-empty-string)`). On match it clears `token_hash` and
//! `expires_at` to `None` (the cleared hash MUST NOT be queryable by
//! `get_session_by_hash`; `Ok(None)` is returned for it) and bumps the
//! revision via `checked_add`. A previously-revoked session (hash already
//! `None`), a different `session_id`, or a missing identity row all return
//! `Ok(AuthSessionRevoke::NotCurrent)`. Revoke is unconditional on the
//! identity being live or expired: a not-yet-revoked session is cleared even
//! if its `expires_at` has already passed (the expiry check guards only
//! `get_session_by_hash` and the `rotate_session` precondition).
//!
//! # `username` / `display_name` / `avatar` snapshot fields
//!
//! `get_session_by_hash` returns an [`AuthSessionSnapshot`] with `username`
//! populated from the identity record's `external_user_id` (the external login
//! handle in the OAuth sense), `display_name` from `external_user_name`
//! (optional; the provider's display name), and `avatar` from `avatar`
//! (optional). These fields carry no security semantics (per the trait docs)
//! and the harness does not assert on them.

use std::collections::HashMap;

use async_trait::async_trait;

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionSnapshot,
    AuthSessionStoreError, AuthSessionVersion, AuthSessionWrite, InstallAuthSession,
    RotateAuthSession,
};

use crate::memory::{ExternalKey, MemoryIdentityRecord, MemoryUserIdentityRepo};

/// Find the identity record whose scope triple matches `(user_id, provider,
/// env)`. `user_id` is globally unique inside the repo (the `user_ids`
/// HashSet in `MemoryUserIdentityRepo` enforces it), so the scan returns at
/// most one record.
fn find_record<'a>(
    map: &'a HashMap<ExternalKey, MemoryIdentityRecord>,
    scope: &AuthSessionScope,
) -> Option<&'a MemoryIdentityRecord> {
    map.values().find(|rec| {
        rec.identity.user_id == scope.user_id
            && rec.identity.auth_source == scope.provider
            && rec.identity.env == scope.env
    })
}

/// Mutable counterpart of [`find_record`].
fn find_record_mut<'a>(
    map: &'a mut HashMap<ExternalKey, MemoryIdentityRecord>,
    scope: &AuthSessionScope,
) -> Option<&'a mut MemoryIdentityRecord> {
    map.values_mut().find(|rec| {
        rec.identity.user_id == scope.user_id
            && rec.identity.auth_source == scope.provider
            && rec.identity.env == scope.env
    })
}

#[async_trait]
impl AuthSessionRepoPort for MemoryUserIdentityRepo {
    async fn read_session_revision(
        &self,
        scope: &AuthSessionScope,
    ) -> Result<u64, AuthSessionStoreError> {
        let map = self.by_external.read().await;
        Ok(match find_record(&map, scope) {
            Some(rec) => rec.session.revision,
            None => 0,
        })
    }

    async fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError> {
        let map = self.by_external.read().await;
        let Some(rec) = find_record(&map, scope) else {
            return Ok(None);
        };
        // The stored hash must equal `hash` and be non-empty. `None`/empty
        // means revoked-or-never-installed → Ok(None).
        let Some(stored_hash) = rec.session.token_hash.as_deref() else {
            return Ok(None);
        };
        if stored_hash.is_empty() || stored_hash != hash {
            return Ok(None);
        }
        // Stored expiry must be strict-greater-than `now`.
        let Some(expires_at) = rec.session.expires_at else {
            return Ok(None);
        };
        if expires_at <= now {
            return Ok(None);
        }
        let Some(session_id) = rec.session.session_id.as_deref() else {
            return Ok(None);
        };
        Ok(Some(AuthSessionSnapshot {
            scope: scope.clone(),
            version: AuthSessionVersion {
                session_id: session_id.to_string(),
                revision: rec.session.revision,
                token_hash: stored_hash.to_string(),
            },
            expires_at,
            username: rec.identity.external_user_id.clone(),
            display_name: rec.identity.external_user_name.clone(),
            avatar: rec.identity.avatar.clone(),
        }))
    }

    async fn install_login_session(
        &self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        let mut map = self.by_external.write().await;
        let Some(rec) = find_record_mut(&mut map, &command.scope) else {
            // A scope whose identity row does not exist cannot host a session;
            // the contract's row-creation path is the legacy
            // `ensure_identity`, which the test fixture calls first.
            return Ok(AuthSessionWrite::Conflict);
        };
        if rec.session.revision != command.expected_revision {
            return Ok(AuthSessionWrite::Conflict);
        }
        // Hardening: ignore `command.next.revision` and write
        // `expected.checked_add(1)`; the contract pins the stored counter to
        // `expected + 1`. Overflow (u64 + 1) surfaces as CorruptRecord.
        let new_revision = command
            .expected_revision
            .checked_add(1)
            .ok_or(AuthSessionStoreError::CorruptRecord)?;
        rec.session.revision = new_revision;
        rec.session.session_id = Some(command.next.session_id);
        rec.session.expires_at = Some(command.expires_at);
        // Mirror the installed credential into the identity row's legacy
        // `token` columns — the SQL store writes the same column from the
        // strict path, and the read-only legacy lookup (`get_by_token`) is
        // an accepted downgrade-boundary consumer of it. Keeping the mirror
        // here preserves store parity so the memory dev/test path behaves
        // like the SQLite/MySQL path.
        let token_hash = command.next.token_hash;
        rec.session.token_hash = Some(token_hash.clone());
        rec.identity.token = Some(token_hash);
        rec.identity.token_expire_at = Some(command.expires_at);
        Ok(AuthSessionWrite::Applied)
    }

    async fn rotate_session(
        &self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        let mut map = self.by_external.write().await;
        let Some(rec) = find_record_mut(&mut map, &command.scope) else {
            return Ok(AuthSessionWrite::Conflict);
        };
        // CAS: stored scope matches plus session_id, revision, token_hash must
        // all match `expected`, and the stored session must not be expired
        // (`expires_at > now`) nor revoked (hash non-empty). Any mismatch
        // returns `Conflict` (a success-typed enum).
        if rec.session.revision != command.expected.revision {
            return Ok(AuthSessionWrite::Conflict);
        }
        if rec.session.session_id.as_deref() != Some(command.expected.session_id.as_str()) {
            return Ok(AuthSessionWrite::Conflict);
        }
        let Some(stored_hash) = rec.session.token_hash.as_deref() else {
            return Ok(AuthSessionWrite::Conflict);
        };
        if stored_hash.is_empty() || stored_hash != command.expected.token_hash {
            return Ok(AuthSessionWrite::Conflict);
        }
        let Some(stored_expires_at) = rec.session.expires_at else {
            return Ok(AuthSessionWrite::Conflict);
        };
        if stored_expires_at <= command.now {
            return Ok(AuthSessionWrite::Conflict);
        }
        // CAS matched. Hardened-revision write: ignore `command.next.revision`
        // and bump the stored counter from `command.expected.revision` by 1.
        let new_revision = command
            .expected
            .revision
            .checked_add(1)
            .ok_or(AuthSessionStoreError::CorruptRecord)?;
        rec.session.revision = new_revision;
        rec.session.session_id = Some(command.next.session_id);
        rec.session.expires_at = Some(command.expires_at);
        // Mirror the installed credential into the identity row's legacy
        // `token` columns — the SQL store writes the same column from the
        // strict path, and the read-only legacy lookup (`get_by_token`) is
        // an accepted downgrade-boundary consumer of it. Keeping the mirror
        // here preserves store parity so the memory dev/test path behaves
        // like the SQLite/MySQL path.
        let token_hash = command.next.token_hash;
        rec.session.token_hash = Some(token_hash.clone());
        rec.identity.token = Some(token_hash);
        rec.identity.token_expire_at = Some(command.expires_at);
        Ok(AuthSessionWrite::Applied)
    }

    async fn revoke_session(
        &self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
        let mut map = self.by_external.write().await;
        let Some(rec) = find_record_mut(&mut map, scope) else {
            return Ok(AuthSessionRevoke::NotCurrent);
        };
        // A previously-revoked session (hash cleared) is `NotCurrent`, as is a
        // different `session_id`. The hash-clearing rule applies AFTER matching
        // the session_id.
        if rec.session.token_hash.is_none() {
            return Ok(AuthSessionRevoke::NotCurrent);
        }
        if rec.session.session_id.as_deref() != Some(session_id) {
            return Ok(AuthSessionRevoke::NotCurrent);
        }
        // Revoke: bump revision (overflow → CorruptRecord), clear hash and
        // expiry. The cleared hash MUST NOT be queryable by `get_session_by_hash`.
        let new_revision = rec
            .session
            .revision
            .checked_add(1)
            .ok_or(AuthSessionStoreError::CorruptRecord)?;
        rec.session.revision = new_revision;
        rec.session.token_hash = None;
        rec.session.expires_at = None;
        // Mirror the revocation into the legacy `token` column (see the
        // install/rotate comment).
        rec.identity.token = None;
        rec.identity.token_expire_at = None;
        Ok(AuthSessionRevoke::Revoked)
    }
}
