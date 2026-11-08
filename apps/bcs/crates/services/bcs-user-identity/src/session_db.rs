//! SQL-backed strict atomic auth sessions (V1 API auth plugin chain).
//!
//! `bcs_user_identities` is the single shared identity + session row. After
//! migration 042 the row doubles as a compare-and-swap cell under one
//! `(user_id, auth_source, env)` scope: `token` holds the current session
//! token hash, `session_id` / `session_revision` name the current version, and
//! the `session_expires_at` Unix-seconds column keeps expiry comparisons
//! unambiguous across dialects. The legacy `token_expire_at` datetime column
//! is written in lockstep only so legacy tooling keeps rendering expiry.
//!
//! Every operation is exactly one statement against the existing
//! `uk_user_id` unique index — one SELECT for a strict resolve, one UPDATE
//! for each CAS write; no transaction, no extra index, no cache. Zero
//! affected rows is a lost race (`Conflict`/`NotCurrent`, never success,
//! never a store failure); statement failures are `Unavailable`; a stored
//! value that cannot be read back as its declared type is `CorruptRecord`.

use async_trait::async_trait;
use bcs_db_api::{DbSqlFlavor, DbStatement, DbValue, db_get_column};
use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionRepoPort, AuthSessionScope, AuthSessionSnapshot,
    AuthSessionStoreError, AuthSessionVersion, AuthSessionWrite, InstallAuthSession,
    RotateAuthSession,
};

use super::DbUserIdentityStore;

/// The maximum representable session revision: SQL persists the revision
/// in a signed BIGINT column, so a bump that would exceed `i64::MAX` can
/// never be persisted and falls in the `CorruptRecord` bucket.
const MAX_REVISION: u64 = i64::MAX as u64;

/// The migration adds the strict-session columns with these defaults, so a
/// scope whose session has never been installed reads revision 0 / expiry 0
/// and never resolves by hash.
const READ_SESSION_REVISION_SQL: &str = "\
SELECT session_revision FROM bcs_user_identities \
WHERE user_id = ? AND auth_source = ? AND env = ?";

/// Resolve the current session by token hash and join the identity display
/// fields in the same single row read. `token = ?` never matches a revoked
/// row (hash cleared to NULL) and `session_expires_at > ?` enforces strict
/// expiry; the `COALESCE` keeps the username projection identical to the
/// in-memory store (internal `user_name` first, `external_user_name` as
/// fallback, empty string last).
const GET_SESSION_BY_HASH_SQL: &str = "\
SELECT session_id, session_revision, session_expires_at, \
COALESCE(user_name, external_user_name, '') AS username, \
external_user_name AS display_name, avatar \
FROM bcs_user_identities \
WHERE user_id = ? AND auth_source = ? AND env = ? AND token = ? \
AND session_expires_at > ?";

/// The revoke predicate: scope + session id + non-empty hash ONLY. It never
/// matches an old revision or hash, and a revoked row (hash cleared, expiry
/// zeroed) stops matching, so repeating a revoke reports `NotCurrent`. The
/// revision bump and the hash/expiry clear happen in the same single UPDATE.
const REVOKE_SESSION_SQL: &str = "\
UPDATE bcs_user_identities \
SET token = NULL, token_expire_at = NULL, session_expires_at = 0, \
session_revision = session_revision + 1 \
WHERE user_id = ? AND auth_source = ? AND env = ? \
AND session_id = ? AND token IS NOT NULL AND token <> ''";

/// The pinned refresh-rotation statement. The predicate is the interface
/// contract's compare-and-swap, verbatim: the stored row must still be the
/// expected, unexpired version. `token_expire_at` keeps the legacy datetime
/// column in lockstep through the flavor-appropriate parameterized
/// expression (`FROM_UNIXTIME(?)` / `datetime(?,'unixepoch')`) — never a
/// string-interpolated timestamp.
fn rotate_session_sql(flavor: DbSqlFlavor) -> String {
    format!(
        "UPDATE bcs_user_identities \
         SET token = ?, token_expire_at = {expiry}, session_expires_at = ?, \
         session_revision = session_revision + 1 \
         WHERE user_id = ? AND auth_source = ? AND env = ? \
         AND session_id = ? AND session_revision = ? AND token = ? \
         AND session_expires_at > ?",
        expiry = flavor.from_unix_param()
    )
}

/// The install compare-and-swap: the row is matched by scope + expected
/// revision and rewritten with the fresh session id, its hash, and the new
/// expiry. A matched row always changes (the version advances), so the
/// affected-row count reflects the CAS outcome faithfully on both backends.
fn install_session_sql(flavor: DbSqlFlavor) -> String {
    format!(
        "UPDATE bcs_user_identities \
         SET token = ?, token_expire_at = {expiry}, session_id = ?, \
         session_expires_at = ?, session_revision = ? \
         WHERE user_id = ? AND auth_source = ? AND env = ? AND session_revision = ?",
        expiry = flavor.from_unix_param()
    )
}

/// A statement-level failure is store unavailability, except a value that
/// failed to convert into its declared type: that row cannot be a valid
/// session and is persisted corruption.
fn statement_error(error: bcs_db_api::DbError) -> AuthSessionStoreError {
    match error {
        bcs_db_api::DbError::Conversion(_) => AuthSessionStoreError::CorruptRecord,
        _ => AuthSessionStoreError::Unavailable,
    }
}

/// Read a forced `now` / `expires_at` Unix-seconds argument as the SQL
/// signed-integer parameter. A caller-supplied moment beyond BIGINT cannot
/// be stored in the column and classifies as corruption, not unavailability.
fn unix_seconds(value: u64) -> Result<DbValue, AuthSessionStoreError> {
    let seconds = i64::try_from(value).map_err(|_| AuthSessionStoreError::CorruptRecord)?;
    Ok(DbValue::from(seconds))
}

/// Read a revision argument as the SQL signed-integer parameter. Bounds are
/// checked by the callers before this runs; the conversion still refuses to
/// invent a value that cannot live in the BIGINT column.
fn revision_param(value: u64) -> Result<DbValue, AuthSessionStoreError> {
    let revision = i64::try_from(value).map_err(|_| AuthSessionStoreError::CorruptRecord)?;
    Ok(DbValue::from(revision))
}

/// Advance a revision by exactly `checked_add(1)` within SQL signed BIGINT
/// bounds. Overflow — u64 or BIGINT — is a `CorruptRecord`: a wrapped or
/// negative revision must never reach the store.
fn bump_revision(current: u64) -> Result<u64, AuthSessionStoreError> {
    let next = current
        .checked_add(1)
        .ok_or(AuthSessionStoreError::CorruptRecord)?;
    if next > MAX_REVISION {
        return Err(AuthSessionStoreError::CorruptRecord);
    }
    Ok(next)
}

/// Translate a CAS UPDATE's affected-row count. Zero rows is a lost race —
/// `Conflict`, never success and never a store failure. More than one row
/// violates the `uk_user_id` uniqueness every scope address relies on and is
/// persisted corruption.
fn cas_outcome(affected_rows: u64) -> Result<AuthSessionWrite, AuthSessionStoreError> {
    match affected_rows {
        0 => Ok(AuthSessionWrite::Conflict),
        1 => Ok(AuthSessionWrite::Applied),
        _ => Err(AuthSessionStoreError::CorruptRecord),
    }
}

/// Translate a revoke UPDATE's affected-row count. Zero rows means the named
/// session id is not the current session (`NotCurrent`); more than one row
/// would violate the `uk_user_id` uniqueness.
fn revoke_outcome(affected_rows: u64) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
    match affected_rows {
        0 => Ok(AuthSessionRevoke::NotCurrent),
        1 => Ok(AuthSessionRevoke::Revoked),
        _ => Err(AuthSessionStoreError::CorruptRecord),
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
                READ_SESSION_REVISION_SQL,
                vec![
                    DbValue::from(scope.user_id.as_str()),
                    DbValue::from(scope.provider.as_str()),
                    DbValue::from(scope.env.as_str()),
                ],
            ))
            .await
            .map_err(|_| AuthSessionStoreError::Unavailable)?;
        let Some(row) = rows.first() else {
            // A scope without an identity row has no session installed yet.
            return Ok(0);
        };
        let revision: i64 =
            db_get_column(row, "session_revision").map_err(statement_error)?;
        u64::try_from(revision).map_err(|_| AuthSessionStoreError::CorruptRecord)
    }

    async fn get_session_by_hash(
        &self,
        scope: &AuthSessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<AuthSessionSnapshot>, AuthSessionStoreError> {
        // An empty hash never names a current session — a revoked or
        // never-installed session must not be addressable through it.
        if hash.is_empty() {
            return Ok(None);
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                GET_SESSION_BY_HASH_SQL,
                vec![
                    DbValue::from(scope.user_id.as_str()),
                    DbValue::from(scope.provider.as_str()),
                    DbValue::from(scope.env.as_str()),
                    DbValue::from(hash),
                    unix_seconds(now)?,
                ],
            ))
            .await
            .map_err(|_| AuthSessionStoreError::Unavailable)?;
        let Some(row) = rows.first() else {
            // Query success with zero rows: the hash does not name this
            // scope's current session (unknown, rotated out, revoked, or
            // expired) and that is plain absence.
            return Ok(None);
        };
        let version = AuthSessionVersion {
            session_id: db_get_column(row, "session_id").map_err(statement_error)?,
            revision: u64::try_from(
                db_get_column::<i64>(row, "session_revision").map_err(statement_error)?,
            )
            .map_err(|_| AuthSessionStoreError::CorruptRecord)?,
            token_hash: hash.to_string(),
        };
        if version.session_id.is_empty() {
            // A session version always names a session id; anything else
            // stored there cannot be a valid current session.
            return Err(AuthSessionStoreError::CorruptRecord);
        }
        Ok(Some(AuthSessionSnapshot {
            scope: scope.clone(),
            version,
            expires_at: u64::try_from(
                db_get_column::<i64>(row, "session_expires_at").map_err(statement_error)?,
            )
            .map_err(|_| AuthSessionStoreError::CorruptRecord)?,
            username: db_get_column(row, "username").map_err(statement_error)?,
            display_name: row.get_string("display_name").map_err(statement_error)?,
            avatar: row.get_string("avatar").map_err(statement_error)?,
        }))
    }

    async fn install_login_session(
        &self,
        command: InstallAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        // The version arithmetic mirrors the in-memory store's bump: the
        // expected revision advances by checked `+1` within BIGINT bounds and
        // `next` must be exactly that bumped version. Revisions beyond BIGINT
        // cannot live in the column, so they are corruption, not races.
        let bumped = bump_revision(command.expected_revision)?;
        if command.next.revision != bumped {
            return Ok(AuthSessionWrite::Conflict);
        }
        let result = self
            .db
            .execute(DbStatement::with_params(
                install_session_sql(self.flavor),
                vec![
                    DbValue::from(command.next.token_hash.as_str()),
                    unix_seconds(command.expires_at)?,
                    DbValue::from(command.next.session_id.as_str()),
                    unix_seconds(command.expires_at)?,
                    revision_param(command.next.revision)?,
                    DbValue::from(command.scope.user_id.as_str()),
                    DbValue::from(command.scope.provider.as_str()),
                    DbValue::from(command.scope.env.as_str()),
                    revision_param(command.expected_revision)?,
                ],
            ))
            .await
            .map_err(|_| AuthSessionStoreError::Unavailable)?;
        cas_outcome(result.affected_rows)
    }

    async fn rotate_session(
        &self,
        command: RotateAuthSession,
    ) -> Result<AuthSessionWrite, AuthSessionStoreError> {
        // A rotation stays inside one session id.
        if command.next.session_id != command.expected.session_id {
            return Ok(AuthSessionWrite::Conflict);
        }
        let bumped = bump_revision(command.expected.revision)?;
        if command.next.revision != bumped {
            return Ok(AuthSessionWrite::Conflict);
        }
        let result = self
            .db
            .execute(DbStatement::with_params(
                rotate_session_sql(self.flavor),
                vec![
                    DbValue::from(command.next.token_hash.as_str()),
                    unix_seconds(command.expires_at)?,
                    unix_seconds(command.expires_at)?,
                    DbValue::from(command.scope.user_id.as_str()),
                    DbValue::from(command.scope.provider.as_str()),
                    DbValue::from(command.scope.env.as_str()),
                    DbValue::from(command.expected.session_id.as_str()),
                    revision_param(command.expected.revision)?,
                    DbValue::from(command.expected.token_hash.as_str()),
                    unix_seconds(command.now)?,
                ],
            ))
            .await
            .map_err(|_| AuthSessionStoreError::Unavailable)?;
        cas_outcome(result.affected_rows)
    }

    async fn revoke_session(
        &self,
        scope: &AuthSessionScope,
        session_id: &str,
    ) -> Result<AuthSessionRevoke, AuthSessionStoreError> {
        let result = self
            .db
            .execute(DbStatement::with_params(
                REVOKE_SESSION_SQL,
                vec![
                    DbValue::from(scope.user_id.as_str()),
                    DbValue::from(scope.provider.as_str()),
                    DbValue::from(scope.env.as_str()),
                    DbValue::from(session_id),
                ],
            ))
            .await
            .map_err(|_| AuthSessionStoreError::Unavailable)?;
        revoke_outcome(result.affected_rows)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The rotate statement keeps the interface contract's CAS predicate
    /// verbatim and both flavors' expiry expression parameterized.
    #[test]
    fn rotate_sql_pins_the_cas_predicate_and_flavor_expiry_expression() {
        for (flavor, expression) in [
            (DbSqlFlavor::Mysql, "FROM_UNIXTIME(?)"),
            (DbSqlFlavor::Sqlite, "datetime(?,'unixepoch')"),
        ] {
            let sql = rotate_session_sql(flavor);
            assert!(
                sql.contains(&format!("token_expire_at = {expression}")),
                "the rotate SQL must use the {flavor:?} flavor expression, got: {sql}"
            );
            for clause in [
                "WHERE user_id = ? AND auth_source = ? AND env = ?",
                "AND session_id = ? AND session_revision = ? AND token = ?",
                "AND session_expires_at > ?",
                "session_revision = session_revision + 1",
            ] {
                assert!(sql.contains(clause), "missing pinned clause {clause} in: {sql}");
            }
        }
    }

    /// The install statement refreshes token/expiry/scope revision only
    /// through bound parameters, with the flavor expression parameterized.
    #[test]
    fn install_sql_pins_scope_revision_cas_with_parameterized_expiry() {
        for (flavor, expression) in [
            (DbSqlFlavor::Mysql, "FROM_UNIXTIME(?)"),
            (DbSqlFlavor::Sqlite, "datetime(?,'unixepoch')"),
        ] {
            let sql = install_session_sql(flavor);
            assert!(
                sql.contains(&format!("token_expire_at = {expression}")),
                "the install SQL must use the {flavor:?} flavor expression, got: {sql}"
            );
            assert!(sql.contains("WHERE user_id = ? AND auth_source = ? AND env = ? AND session_revision = ?"));
        }
    }

    /// Revision overflow within SQL signed BIGINT classifies as corruption,
    /// never a wrapped or negative revision reaching the column.
    #[test]
    fn revision_bumps_stay_within_signed_bigint() {
        assert_eq!(bump_revision(0).unwrap(), 1);
        assert_eq!(bump_revision(41).unwrap(), 42);
        assert!(matches!(
            bump_revision(i64::MAX as u64),
            Err(AuthSessionStoreError::CorruptRecord)
        ));
        assert!(matches!(
            bump_revision(u64::MAX),
            Err(AuthSessionStoreError::CorruptRecord)
        ));
    }

    /// Zero affected rows is a lost race; one is the applied write; more than
    /// one violates the unique index the CAS relies on.
    #[test]
    fn affected_rows_map_to_outcomes_exhaustively() {
        assert_eq!(cas_outcome(0).unwrap(), AuthSessionWrite::Conflict);
        assert_eq!(cas_outcome(1).unwrap(), AuthSessionWrite::Applied);
        assert!(matches!(
            cas_outcome(2),
            Err(AuthSessionStoreError::CorruptRecord)
        ));
        assert_eq!(revoke_outcome(0).unwrap(), AuthSessionRevoke::NotCurrent);
        assert_eq!(revoke_outcome(1).unwrap(), AuthSessionRevoke::Revoked);
        assert!(matches!(
            revoke_outcome(2),
            Err(AuthSessionStoreError::CorruptRecord)
        ));
    }
}
