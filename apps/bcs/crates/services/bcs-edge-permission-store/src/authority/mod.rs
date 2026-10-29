//! SQL authority Store — strict owner/manager/transfer reads
//! plus the Task-2 manager write paths.
//!
//! Implements `BotAuthorityRepoPort` as one store bound to the
//! env injected at construction time: every statement is scoped to
//! that stored env, and user parameters cannot widen the scope.
//!
//! This module (`mod`) owns the store type, query plumbing and
//! statement shapes shared across the authority files; [`reads`]
//! implements the strict reads, [`managers`] the single-item and
//! full-set manager commands, and [`audit`] the shared audit
//! row shapes. SQL stays here — `bcs-edge-permission`'s
//! authority Core never builds statements.
//!
//! Write semantics follow the frozen Task-0 transaction sketch in
//! `docs/superpowers/plans/2026-10-10-bot-authority-feasibility.md`
//! (§2 guard chain with `query_result` token consumption, §4 F1:
//! audit VALUES ≤99 rows per statement, ≤100 principals per
//! chunk), with one Task-0-review adaptation: the sink write of
//! the sketch is replaced by a final guard-consumption query
//! (`SELECT ? AS t0, ...`), so no correctness depends on MySQL
//! affected-rows semantics (ruling T0-2) and every
//! `ExecuteChecked` write remains genuinely changing. Transfers
//! and the ownership-transfer write commands implement the
//! spec §6 protocol in [`transfers`].

mod audit;
/// Pure SQL step builders for the Task-4 registration composites.
/// No plugin access here; `bcs-bot-store` composes them into its own
/// single transaction (one-way dependency, per plan §2).
pub mod lifecycle;
mod managers;
mod ownership_sync;
mod reads;
mod transfers;
mod transfers_support;

use std::sync::Arc;

use bcs_db_api::{DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

/// Authority read/write granularity: at most this many ids may be
/// bound into one collection statement.
pub(super) const CHUNK: usize = 128;

/// DB-backed authority Store (bot-authority plan Task 1).
pub struct DbBotAuthorityStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
    /// Env bound at construction; every statement is scoped to it.
    env: String,
    /// Legacy `created_by` read fallback for the ownership
    /// migration window (bootstrap config
    /// `bot_authority.legacy_created_by_fallback`).
    legacy_created_by_fallback: bool,
}

impl DbBotAuthorityStore {
    /// Bind the DB plugin, SQL flavor and the bootstrap-injected env.
    pub fn new(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, env: String) -> Self {
        Self { db, flavor, env, legacy_created_by_fallback: false }
    }

    /// Enable/disable the legacy `created_by` read fallback
    /// (migration-window shim): when enabled, a version-0 Bot whose
    /// legacy creator fact names the reading Human resolves Owner
    /// reads the way the pre-authority build did. Default OFF at
    /// the store; the bootstrap config flips it (default ON) so
    /// pre-migration deployments can ship the code first.
    pub fn with_legacy_created_by_fallback(mut self, enabled: bool) -> Self {
        self.legacy_created_by_fallback = enabled;
        self
    }

    /// MySQL-backed constructor (CI/ops wire-up).
    pub fn mysql(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self::new(db, DbSqlFlavor::Mysql, env)
    }

    /// SQLite-backed constructor (local dev/tests).
    pub fn sqlite(db: Arc<dyn DbPlugin>, env: String) -> Self {
        Self::new(db, DbSqlFlavor::Sqlite, env)
    }

    pub fn flavor(&self) -> DbSqlFlavor {
        self.flavor
    }

    pub fn env(&self) -> &str {
        &self.env
    }

    pub(super) async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|err| {
            warn!(operation, env = %self.env, error = %err, "db_bot_authority: query failed");
            authority_db_error(operation, err)
        })
    }

    /// Case-exact identity predicate for `edge_grants.from_id`.
    /// MySQL's default database collation would case-fold actor ids
    /// and merge distinct principals (spec §3.2.6), so MySQL compares
    /// the byte image (same pattern as the friend-list reader in
    /// `lib.rs`); SQLite is BINARY already. Index-sargability of the
    /// CAST form is a CI MySQL gate item (U4).
    pub(super) fn from_id_eq(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(from_id AS BINARY) = ?",
            DbSqlFlavor::Sqlite => "from_id = ?",
        }
    }

    /// Case-exact identity predicate for `bcs_bots.bot_uuid`.
    /// MySQL's default collation would case-merge bot
    /// principals while the edge-side comparisons are byte-
    /// exact, contradicting the store's own strictness; the
    /// sargability cost of the CAST is the CI MySQL gate
    /// item (U4), same as the edge predicates.
    pub(super) fn bot_id_eq(&self) -> String {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(bot_uuid AS BINARY) = ?".to_string(),
            DbSqlFlavor::Sqlite => "bot_uuid = ?".to_string(),
        }
    }

    /// Case-exact IN list for `bcs_bots.bot_uuid`.
    pub(super) fn bot_id_in(&self, count: usize) -> String {
        let placeholders = self.id_placeholders(count);
        match self.flavor {
            DbSqlFlavor::Mysql => {
                format!("CAST(bot_uuid AS BINARY) IN ({placeholders})")
            }
            DbSqlFlavor::Sqlite => format!("bot_uuid IN ({placeholders})"),
        }
    }

    /// Case-exact identity predicate for `edge_grants.to_id`.
    pub(super) fn to_id_eq(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(to_id AS BINARY) = ?",
            DbSqlFlavor::Sqlite => "to_id = ?",
        }
    }

    /// Byte-exact order for actor-id pages: "ordered by ID" per the
    /// plan must not follow a case-insensitive collation.
    pub(super) fn from_id_order(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(from_id AS BINARY)",
            DbSqlFlavor::Sqlite => "from_id",
        }
    }

    pub(super) fn bot_id_order(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(bot_id AS BINARY)",
            DbSqlFlavor::Sqlite => "bot_id",
        }
    }

    pub(super) fn target_id_order(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(target_owner_actor_id AS BINARY)",
            DbSqlFlavor::Sqlite => "target_owner_actor_id",
        }
    }

    /// Now-expression in the same textual timestamp form the
    /// transfer rows store (`requested_at`/`expires_at` are DB-time
    /// fields).
    pub(super) fn db_now_utc(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CURRENT_TIMESTAMP",
            DbSqlFlavor::Sqlite => "datetime('now')",
        }
    }

    /// Unified approved management-edge predicate (spec §4.1:
    /// `grant_ref_id = 0`, `rules` NULL, `approved` only).
    pub(super) const MANAGEMENT_EDGE_PREDICATE: &'static str =
        "grant_kind IN ('owner', 'manager') AND grant_ref_id = 0 \
         AND rules IS NULL AND status = 'approved'";

    pub(super) fn env_param(&self) -> DbValue {
        DbValue::from(self.env.clone())
    }

    /// `?, ?, ...` for an IN list of `count` ids.
    pub(super) fn id_placeholders(&self, count: usize) -> String {
        std::iter::repeat("?")
            .take(count)
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Single-row scalar query result.
    pub(super) async fn query_u64(
        &self,
        operation: &'static str,
        sql: &str,
        params: Vec<DbValue>,
    ) -> ServiceResult<u64> {
        let rows = self.query(operation, DbStatement::with_params(sql, params)).await?;
        rows.first()
            .map(|row| crate::required_u64(row, "total"))
            .transpose()
            .map(|value| value.unwrap_or(0))
    }
}

/// Integrity error (owner missing/duplicated on a version>0 bot):
/// surfacing as a distinct public code keeps the 500 path diagnosable
/// without leaking SQL.
pub(super) fn owner_integrity_error(detail: String) -> ServiceError {
    ServiceError::InternalError(format!(
        "{}: {}",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_OWNER_INTEGRITY,
        detail
    ))
}

/// Uninitialized (`ownership_version = 0`) conflict: not a deny that
/// can be swallowed; callers map it to 409 (spec §7).
pub(super) fn not_initialized_error(bot_id: &str) -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: bot {} not initialized in this environment",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_AUTHORITY_NOT_INITIALIZED,
        bot_id
    ))
}

/// `bot_authority_human_unknown` InvalidOperation (400-ish).
pub(super) fn human_unknown_error(detail: String) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!(
            "{}: {detail}",
            bcs_service_api::application::v1::bot_authority::ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN
        ),
        request_id: None,
    }
}

/// `bot_authority_operator_forbidden` Forbidden (403).
pub(super) fn operator_forbidden_error(detail: String) -> ServiceError {
    ServiceError::Forbidden(format!(
        "{}: {detail}",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN
    ))
}

/// `bot_manager_input_limit` InvalidOperation (400-ish).
pub(super) fn input_limit_error() -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!(
            "{}: a synced manager set exceeds the 1,000 deduped-user input cap",
            bcs_service_api::application::v1::bot_authority::ERROR_BOT_MANAGER_INPUT_LIMIT
        ),
        request_id: None,
    }
}

/// `bot_manager_stock_limit` Conflict (409): the current manager
/// set exceeds the frozen 10,000-stock bound; the whole request
/// fails and is never truncated (feasibility doc §5.2).
pub(super) fn stock_limit_error(count: u64) -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: bot's current manager set has {count} rows, beyond the \
         frozen 10,000 stock bound; the whole request is rejected",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_MANAGER_STOCK_LIMIT
    ))
}

/// `bot_authority_tx_outcome_uncertain`: a plugin `DbError::Timeout`
/// (Task-0 方案 A). The transaction rolled back by contract, but the
/// caller must treat the command as terminal — this mapping is
/// NEVER retried, and no partial state leaked.
pub(super) fn tx_outcome_uncertain() -> ServiceError {
    ServiceError::InternalError(format!(
        "{}: the manager change transaction exceeded the plugin deadline; \
         per contract it rolled back and the caller must not auto-retry",
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_TX_OUTCOME_UNCERTAIN
    ))
}

/// Map a manager write transaction error. `Timeout` maps to the
/// uncertain-outcome error (never retried); everything else is a
/// deterministic-rollback error surface.
pub(super) fn map_tx_error(op: &'static str, err: DbError) -> ServiceError {
    match err {
        DbError::Timeout => tx_outcome_uncertain(),
        other => authority_db_error(op, other),
    }
}

pub(super) fn authority_db_error(operation: &'static str, err: DbError) -> ServiceError {
    ServiceError::InternalError(format!("bot authority db {}: {}", operation, err))
}
