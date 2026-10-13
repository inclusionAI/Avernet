//! Permission-scope audit (`bot_authority_changes`)) shapes for
//! the manager write paths (spec §4.4).
//!
//! Values-list audits are deliberately chunked at 99 rows per
//! statement: one audit row binds 10 parameters and SQLite caps
//! host variables at 999 (Task-0 finding F1, probe-proven).
//! `INSERT ... SELECT` audits (the revoke side) are one statement
//! per 100-subject chunk and are not bound by the VALUES limit.

use bcs_db_api::{DbSqlFlavor, DbStatement, DbValue};

/// One audit row to write: subject + action + before/after
/// state + its sorted-diff sequence.
pub(super) struct AuditRowDraft {
    pub sequence: u64,
    pub subject_id: String,
    pub action: &'static str,
    pub before_state: Option<&'static str>,
    pub after_state: &'static str,
}

/// Max audit rows per VALUES statement (F1: 10 params/row × 99 =
/// 990 ≤ 999 SQLite host variables).
pub(super) const AUDIT_VALUES_ROWS: usize = 99;

/// Audit actions written by the manager paths.
pub(super) const ACTION_MANAGER_GRANT: &str = "manager_grant";
pub(super) const ACTION_MANAGER_RESTORE: &str = "manager_restore";
pub(super) const ACTION_MANAGER_REVOKE: &str = "manager_revoke";

/// Audit actions written by the transfer write
/// paths (spec §4.4: 转交发起/续期/决议 + 自动关闭).
/// System closures carry a fixed System actor
/// (`system_transfer_close`) and a fixed reason on the
/// transfer row; lifecycle verbs (initialize/retire)
/// remain Tasks 4.
pub(super) const ACTION_TRANSFER_REQUEST: &str = "transfer_request";
pub(super) const ACTION_TRANSFER_REFRESH: &str = "transfer_refresh";
pub(super) const ACTION_TRANSFER_ACCEPT: &str = "transfer_accept";
pub(super) const ACTION_TRANSFER_REJECT: &str = "transfer_reject";
pub(super) const ACTION_TRANSFER_CANCEL: &str = "transfer_cancel";
pub(super) const ACTION_TRANSFER_EXPIRE: &str = "transfer_expire";
pub(super) const ACTION_TRANSFER_INVALIDATE: &str = "transfer_invalidate";

/// Fixed system identity for in-transaction lazy
/// closures of dead pendings (spec §4.4: 自动关闭的决议
/// 使用固定 system 原因).
pub(super) const CLOSING_SYSTEM_ACTOR: &str = "system_transfer_close";

/// Fixed system closure reasons (the transfer row's
/// `reason`), also the pinned conflict codes.
pub(super) const REASON_TRANSFER_EXPIRED: &str = "transfer_expired";
pub(super) const REASON_OWNER_CHANGED: &str = "owner_changed";

const AUDIT_INSERT_SQL: &str = "INSERT INTO bot_authority_changes \
     (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, \
      subject_id, before_state, after_state) VALUES ";

/// Build one VALUES audit statement for up to
/// [`AUDIT_VALUES_ROWS`] rows (caller chunks longer
/// inputs; sequences are part of each row so blocks may
/// be any subset of the sorted diff).
pub(super) fn audit_values_statement(
    rows: &[AuditRowDraft],
    env: &str,
    bot_id: &str,
    mutation_id: &str,
    actor_kind: &str,
    actor_id: &str,
) -> DbStatement {
    assert!(
        !rows.is_empty() && rows.len() <= AUDIT_VALUES_ROWS,
        "audit VALUES block bounds"
    );
    let mut sql = String::with_capacity(AUDIT_INSERT_SQL.len() + rows.len() * 72);
    sql.push_str(AUDIT_INSERT_SQL);
    let mut params = Vec::with_capacity(rows.len() * 10);
    for (offset, row) in rows.iter().enumerate() {
        if offset > 0 {
            sql.push_str(", ");
        }
        sql.push_str("(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
        params.push(DbValue::from(env));
        params.push(DbValue::from(bot_id));
        params.push(DbValue::from(mutation_id));
        params.push(DbValue::from(row.sequence));
        params.push(DbValue::from(row.action));
        params.push(DbValue::from(actor_kind));
        params.push(DbValue::from(actor_id));
        params.push(DbValue::from(row.subject_id.as_str()));
        params.push(DbValue::from(row.before_state));
        params.push(DbValue::from(row.after_state));
    }
    DbStatement::with_params(&sql, params)
}

/// `INSERT ... SELECT` audit for one revoke chunk: audits the
/// pre-revoke state of every matching approved manager row with
/// ROW_NUMBER-determined sequences (stable by `from_id` order).
/// The caller binds the in-transaction owner id for the
/// owner-exclusion predicate (same predicate as the pre-read,
/// count/chunk guards, ruling: one owner-exclusion predicate).
pub(super) fn revoke_audit_insert_select(
    flavor: DbSqlFlavor,
    chunk_size: usize,
) -> String {
    let owner_neq = match flavor {
        DbSqlFlavor::Mysql => "CAST(e.from_id AS BINARY) <> ?",
        DbSqlFlavor::Sqlite => "e.from_id <> ?",
    };
    let placeholders = std::iter::repeat("?")
        .take(chunk_size)
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "INSERT INTO bot_authority_changes \
         (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, \
          subject_id, before_state, after_state) \
         SELECT ?, ?, ?, ? + ROW_NUMBER() OVER (ORDER BY e.from_id) - 1, \
                ?, ?, ?, e.from_id, 'approved', 'revoked' \
         FROM edge_grants e \
         WHERE e.env = ? AND e.to_id = ? \
           AND e.grant_kind = 'manager' AND e.grant_ref_id = 0 \
           AND e.rules IS NULL AND e.status = 'approved' \
           AND {owner_neq} \
           AND e.from_id IN ({placeholders})"
    )
}
