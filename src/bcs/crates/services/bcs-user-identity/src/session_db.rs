//! SQL-backed `AuthSessionRepoPort` impl for `DbUserIdentityStore`.
//!
//! This is the strict CAS port overlay on the existing `bcs_user_identities`
//! row keyed by `(user_id, auth_source, env)`. The legacy
//! `UserIdentityRepoPort` port still uses the same row via the same plugin for
//! `ensure_identity` / `lookup_*` / `get_by_token`; the strict CAS surface lives
//! in three extra columns installed by migration `028_auth_session_version.sql`
//! (SQLite) / `027_auth_session_version.sql` (MySQL/OceanBase):
//!
//! - `session_id` — nullable; `NULL` before the first install and after
//!   revoke.
//! - `session_revision` — `NOT NULL DEFAULT 0`; the CAS counter. 0 = no
//!   session installed yet (the contract's missing-install state; matches
//!   `read_session_revision -> Ok(0)`).
//! - `session_expires_at` — `NOT NULL DEFAULT 0`; Unix seconds of token
//!   expiry; 0 means "no active session" so `session_expires_at > now`
//!   (used by `get_session_by_hash` and `rotate_session`) is naturally false
//!   for any positive `now`.
//!
//! The hash string (a SHA-256 of the JWT computed by callers; never the raw
//! JWT) is stored in the legacy `token` column. `token_expire_at` is kept as
//! the legacy flavor-specific timestamp and written from the same `expires_at`
//! Unix-second value via `FROM_UNIXTIME(?)` (MySQL) or
//! `datetime(?,'unixepoch')` (SQLite) — never via string concatenation.
//!
//! # Conditional writes — affected-rows only
//!
//! Every write method is a **single** SQL statement. CAS semantics are
//! enforced by writing the post-state into the `SET` and the pre-state into
//! the `WHERE`, then classifying the result by `affected_rows`:
//!
//! - `0 affected_rows` → `Ok(AuthSessionWrite::Conflict)` (install/rotate) or
//!   `Ok(AuthSessionRevoke::NotCurrent)`. Never an error, never a success.
//! - `>= 1 affected_rows` → `Ok(Applied)` / `Ok(Revoked)`.
//! - `Err(DbError::Backend(_))` from `execute` →
//!   `Err(AuthSessionStoreError::Unavailable)` (storage failure).
//! - `Err(DbError::Conversion(_))` returned by the plugin at row-read time, or
//!   any decode failure observed after a successful query, →
//!   `Err(AuthSessionStoreError::CorruptRecord)`. A structurally invalid
//!   row is never silently repaired through this trait.
//!
//! The `> 0` affected rows is the only signal that the CAS precondition
//! matched; there is no private DB interface beyond `DbPlugin::query` /
//! `DbPlugin::execute`, and the impl never opens a transaction (each method
//! is a single-statement write, so single-statement atomicity suffices).
//!
//! # Revised-counter hardening
//!
//! The contract pins the stored revision to `expected.checked_add(1)` after
//! success. This impl writes `session_revision = session_revision + 1` in
//! the `SET` clause; the CAS `WHERE` clause has already verified the stored
//! value equals `expected` / `expected_revision` at the time of the update.
//! The caller's `next.revision` is therefore ignored — the SQL expression
//! is necessarily `expected + 1`, never the caller-supplied value. Overflow
//! beyond the column's `i64` range is impossible in practice (CAS would have
//! no pre-state to match) and would surface from `i64::try_from` as
//! `CorruptRecord` before reaching the DB. The same hardening applies to
//! install (`session_revision + 1` where the CAS clause pinned
//! `session_revision = expected_revision`).

use async_trait::async_trait;
use bcs_db_api::{DbError, DbSqlFlavor, DbStatement, DbValue, db_get_column};
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionSnapshot,
    AuthSessionStoreError, AuthSessionVersion, AuthSessionWrite, InstallAuthSession,
    RotateAuthSession,
};

use crate::DbUserIdentityStore;

/// Map a `DbError` to an `AuthSessionStoreError`.
///
/// `Conversion` errors (failed cell decoding, both from the plugin and from
/// `DbRow` getters in our code) become `CorruptRecord`: a structurally valid
/// persisted row whose fields are unusable. Every other DB-level failure
/// (`Backend`, `InvalidInput`, `Unsupported`, `ConditionFailed`) becomes
/// `Unavailable`, since the caller cannot conclude the row state after the
/// storage layer broke.
fn classify_db_error(err: DbError) -> AuthSessionStoreError {
    match err {
        DbError::Conversion(_) => AuthSessionStoreError::CorruptRecord,
        _ => AuthSessionStoreError::Unavailable,
    }
}

/// Decode a `u64` parameter from a `u64` value while staying within the `i64`
/// range that SQL INTEGER/BIGINT can hold. Out-of-range values surface as
/// `CorruptRecord`, consistent with the trait's `checked_add` overflow path
/// in the in-memory reference (`session_memory`).
fn to_i64_param(value: u64) -> Result<i64, AuthSessionStoreError> {
    i64::try_from(value).map_err(|_| AuthSessionStoreError::CorruptRecord)
}

/// Promote a stored `i64` revision/expiry to the contract's `u64` surface.
/// Negative stored values are impossible under the contract and indicate a
/// torn write or a corrupted column → `CorruptRecord`.
fn from_i64_column(value: i64) -> Result<u64, AuthSessionStoreError> {
    u64::try_from(value).map_err(|_| AuthSessionStoreError::CorruptRecord)
}

impl DbUserIdentityStore {
    /// Token-expiry flavor expression for the `token_expire_at` legacy column.
    ///
    /// MySQL/OceanBase uses `FROM_UNIXTIME(?)` and the column is `TIMESTAMP`;
    /// SQLite uses `datetime(?,'unixepoch')` and the column is `TEXT`. The `?`
    /// is the same `expires_at` Unix seconds that `session_expires_at`
    /// receives directly as an `INTEGER`. Bind parameters are used in both
    /// flavors; no string concatenation of caller input.
    fn token_expire_at_set_expr(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "FROM_UNIXTIME(?)",
            DbSqlFlavor::Sqlite => "datetime(?, 'unixepoch')",
        }
    }

    /// SQL for `install_login_session`. The CAS clause pins `session_revision
    /// = expected_revision`; the `SET` always bumps the stored counter by 1
    /// (so the post-state is `expected_revision + 1`, regardless of the
    /// caller's `next.revision`) and overwrites `session_id`, `token`, and
    /// both expiry columns with the freshly signed values.
    fn install_sql(&self) -> String {
        format!(
            "UPDATE bcs_user_identities \
             SET token = ?, \
                 token_expire_at = {expiry_expr}, \
                 session_expires_at = ?, \
                 session_id = ?, \
                 session_revision = session_revision + 1 \
             WHERE user_id = ? AND auth_source = ? AND env = ? \
               AND session_revision = ?",
            expiry_expr = self.token_expire_at_set_expr(),
        )
    }

    /// SQL for `rotate_session`. The CAS clause pins `scope` plus the live
    /// `session_id`, `session_revision`, and `token` (hash) — and requires
    /// the stored session not to be expired (`session_expires_at > now`). The
    /// `SET` overwrites all four session fields and bumps the counter, so the
    /// post-state has `session_id = next.session_id`,
    /// `session_revision = expected.revision + 1`, `token = next.token_hash`,
    /// `session_expires_at = expires_at`. A caller-supplied
    /// `next.session_id` that differs from `expected.session_id` is honored
    /// (the contract's harness rotates A→B and revokes by B's sid
    /// afterwards); the SQL template explicitly writes `session_id = ?`.
    fn rotate_sql(&self) -> String {
        format!(
            "UPDATE bcs_user_identities \
             SET token = ?, \
                 token_expire_at = {expiry_expr}, \
                 session_expires_at = ?, \
                 session_id = ?, \
                 session_revision = session_revision + 1 \
             WHERE user_id = ? AND auth_source = ? AND env = ? \
               AND session_id = ? \
               AND session_revision = ? \
               AND token = ? \
               AND session_expires_at > ?",
            expiry_expr = self.token_expire_at_set_expr(),
        )
    }

    /// SQL for `revoke_session`. The CAS clause pins `scope`, `session_id`,
    /// and a non-empty stored `token` (`token IS NOT NULL AND token <> ''`).
    /// The `SET` clears `token`, `token_expire_at`, `session_expires_at`, and
    /// bumps the stored counter as the contract requires. `session_id` is
    /// intentionally NOT cleared: a subsequent revoke with the same sid sees
    /// `token IS NULL` and returns `NotCurrent`; a subsequent rotate with the
    /// stale expected `token` does not match NULL either. The cleared
    /// `token`/`session_expires_at` are not queryable by `get_session_by_hash`
    /// (the WHERE clause requires a non-empty `token` and a positive
    /// `session_expires_at`).
    fn revoke_sql(&self) -> &'static str {
        "UPDATE bcs_user_identities \
         SET token = NULL, \
             token_expire_at = NULL, \
             session_expires_at = 0, \
             session_revision = session_revision + 1 \
         WHERE user_id = ? AND auth_source = ? AND env = ? \
           AND session_id = ? \
           AND token IS NOT NULL AND token <> ''"
    }

    /// SQL for `read_session_revision`. A scope with no row returns zero
    /// rows — the impl maps that to `Ok(0)` per the contract's missing-row
    /// signal.
    fn read_revision_sql(&self) -> &'static str {
        "SELECT session_revision FROM bcs_user_identities \
         WHERE user_id = ? AND auth_source = ? AND env = ? LIMIT 1"
    }

    /// SQL for `get_session_by_hash`. The WHERE clause filters by `scope`,
    /// requires `token = ?` (so a cleared/NULL `token` never matches), and
    /// requires `session_expires_at > ?` (strictly greater-than `now`, so
    /// expiry boundaries match the in-memory reference). Zero rows →
    /// `Ok(None)`.
    fn get_session_by_hash_sql(&self) -> &'static str {
        "SELECT user_id, auth_source, env, session_id, session_revision, token, \
                session_expires_at, external_user_id, external_user_name, avatar \
         FROM bcs_user_identities \
         WHERE user_id = ? AND auth_source = ? AND env = ? \
           AND token = ? \
           AND session_expires_at > ? \
         LIMIT 1"
    }

    /// Bind a `String` param via `DbValue::from(value.as_str())`; helper for
    /// call sites feeding `String` into `DbStatement::with_params`.
    fn str_param(value: &str) -> DbValue {
        DbValue::from(value)
    }
}

#[async_trait]
impl AuthSessionRepoPort for DbUserIdentityStore {
    async fn read_session_revision(
        &self,
        scope: &AuthSessionScope,
    ) -> Result<u64, AuthSessionStoreError> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.read_revision_sql(),
                vec![
                    Self::str_param(&scope.user_id),
                    Self::str_param(&scope.provider),
                    Self::str_param(&scope.env),
                ],
            ))
            .await
            .map_err(classify_db_error)?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(0);
        };
        let stored: i64 = db_get_column(&row, "session_revision").map_err(classify_db_error)?;
        from_i64_column(stored)
    }

    async fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError> {
        let now_i = to_i64_param(now)?;
        let rows = self
            .db
            .query(DbStatement::with_params(
                self.get_session_by_hash_sql(),
                vec![
                    Self::str_param(&scope.user_id),
                    Self::str_param(&scope.provider),
                    Self::str_param(&scope.env),
                    Self::str_param(hash),
                    DbValue::from(now_i),
                ],
            ))
            .await
            .map_err(classify_db_error)?;
        let Some(row) = rows.into_iter().next() else {
            return Ok(None);
        };
        let user_id: String = db_get_column(&row, "user_id").map_err(classify_db_error)?;
        let auth_source: String = db_get_column(&row, "auth_source").map_err(classify_db_error)?;
        let env: String = db_get_column(&row, "env").map_err(classify_db_error)?;
        let session_id: String = db_get_column(&row, "session_id").map_err(classify_db_error)?;
        let revision_i: i64 =
            db_get_column(&row, "session_revision").map_err(classify_db_error)?;
        let token: String = db_get_column(&row, "token").map_err(classify_db_error)?;
        let expires_i: i64 =
            db_get_column(&row, "session_expires_at").map_err(classify_db_error)?;
        let username: String =
            db_get_column(&row, "external_user_id").map_err(classify_db_error)?;
        // Optional display fields — `None` on missing/NULL is the contract.
        let display_name = row
            .get_string("external_user_name")
            .map_err(classify_db_error)?;
        let avatar = row.get_string("avatar").map_err(classify_db_error)?;

        let revision = from_i64_column(revision_i)?;
        let expires_at = from_i64_column(expires_i)?;

        Ok(Some(AuthSessionSnapshot {
            scope: AuthSessionScope {
                user_id,
                provider: auth_source,
                env,
            },
            version: AuthSessionVersion {
                session_id,
                revision,
                token_hash: token,
            },
            expires_at,
            username,
            display_name,
            avatar,
        }))
    }

    async fn install_login_session(
        &self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        let expected_revision_i = to_i64_param(command.expected_revision)?;
        let expires_at_i = to_i64_param(command.expires_at)?;
        let result = self
            .db
            .execute(DbStatement::with_params(
                &self.install_sql(),
                vec![
                    Self::str_param(&command.next.token_hash),
                    DbValue::from(expires_at_i),
                    DbValue::from(expires_at_i),
                    Self::str_param(&command.next.session_id),
                    Self::str_param(&command.scope.user_id),
                    Self::str_param(&command.scope.provider),
                    Self::str_param(&command.scope.env),
                    DbValue::from(expected_revision_i),
                ],
            ))
            .await
            .map_err(classify_db_error)?;
        if result.affected_rows == 0 {
            Ok(AuthSessionWrite::Conflict)
        } else {
            Ok(AuthSessionWrite::Applied)
        }
    }

    async fn rotate_session(
        &self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        let expected_revision_i = to_i64_param(command.expected.revision)?;
        let now_i = to_i64_param(command.now)?;
        let expires_at_i = to_i64_param(command.expires_at)?;
        let result = self
            .db
            .execute(DbStatement::with_params(
                &self.rotate_sql(),
                vec![
                    Self::str_param(&command.next.token_hash),
                    DbValue::from(expires_at_i),
                    DbValue::from(expires_at_i),
                    Self::str_param(&command.next.session_id),
                    Self::str_param(&command.scope.user_id),
                    Self::str_param(&command.scope.provider),
                    Self::str_param(&command.scope.env),
                    Self::str_param(&command.expected.session_id),
                    DbValue::from(expected_revision_i),
                    Self::str_param(&command.expected.token_hash),
                    DbValue::from(now_i),
                ],
            ))
            .await
            .map_err(classify_db_error)?;
        if result.affected_rows == 0 {
            Ok(AuthSessionWrite::Conflict)
        } else {
            Ok(AuthSessionWrite::Applied)
        }
    }

    async fn revoke_session(
        &self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
        let result = self
            .db
            .execute(DbStatement::with_params(
                self.revoke_sql(),
                vec![
                    Self::str_param(&scope.user_id),
                    Self::str_param(&scope.provider),
                    Self::str_param(&scope.env),
                    Self::str_param(session_id),
                ],
            ))
            .await
            .map_err(classify_db_error)?;
        if result.affected_rows == 0 {
            Ok(AuthSessionRevoke::NotCurrent)
        } else {
            Ok(AuthSessionRevoke::Revoked)
        }
    }
}
