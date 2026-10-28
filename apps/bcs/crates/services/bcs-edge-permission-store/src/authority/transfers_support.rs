//! Movable seam of the transfers module (file-size split,
//! BCS-AUTHORITY-SIZE-2026-10-10): the transfer write paths'
//! shared typestate, transaction builder, SQL predicate helpers
//! and pre-read classifiers, kept byte-for-byte. Items are
//! module-internal to `authority`'s transfer commands;
//! `transfers.rs` re-imports this seam wholesale so call sites
//! stay byte-identical.

use bcs_db_api::{
    DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep, DbRow,
    DbValue,
};
use bcs_service_api::types::bot_authority::{
    AuthorityActorKind, AuthorityConflictCode, TransferAction, TransferStatus,
};
use bcs_service_api::{ServiceError, ServiceResult};

use super::audit::{
    ACTION_TRANSFER_EXPIRE, ACTION_TRANSFER_INVALIDATE, REASON_OWNER_CHANGED,
    REASON_TRANSFER_EXPIRED,
};
use super::reads::parse_required_epoch;
use super::DbBotAuthorityStore;

/// Byte-exact predicates for the binary-collated
/// transfer-table actor columns (spec §3.2.6; the
/// SQLite fixture is BINARY by default).
pub(super) fn target_id_eq() -> &'static str {
    "target_owner_actor_id = ?"
}

pub(super) fn target_id_neq() -> &'static str {
    "target_owner_actor_id <> ?"
}

/// The verified operator of one transfer command.
pub(super) struct Operator {
    pub(super) kind: AuthorityActorKind,
    /// Composed actor id (`human_<user_id>` /
    /// `app_<id>`): the audit and decision-record
    /// identity.
    pub(super) actor_id: String,
}

impl Operator {
    pub(super) fn is_human(&self) -> bool {
        self.kind == AuthorityActorKind::Human
    }
}

/// Lazy-closure kinds of a dead pending.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum ClosureKind {
    /// Pending past its expiry.
    Expire,
    /// Pending whose owner/version snapshot no longer
    /// matches (what the ABA rule protects).
    Invalidate,
}

impl ClosureKind {
    pub(super) fn status(self) -> &'static str {
        match self {
            ClosureKind::Expire => "expired",
            ClosureKind::Invalidate => "invalidated",
        }
    }

    pub(super) fn audit_action(self) -> &'static str {
        match self {
            ClosureKind::Expire => ACTION_TRANSFER_EXPIRE,
            ClosureKind::Invalidate => ACTION_TRANSFER_INVALIDATE,
        }
    }

    pub(super) fn reason(self) -> &'static str {
        match self {
            ClosureKind::Expire => REASON_TRANSFER_EXPIRED,
            ClosureKind::Invalidate => REASON_OWNER_CHANGED,
        }
    }

    pub(super) fn code(self) -> AuthorityConflictCode {
        match self {
            ClosureKind::Expire => AuthorityConflictCode::TransferExpired,
            ClosureKind::Invalidate => AuthorityConflictCode::OwnerChanged,
        }
    }
}

/// The parsed pre-read row (the fields the write
/// paths need).
pub(super) struct StoredRow {
    pub(super) from_owner: String,
    pub(super) expected_version: u64,
    pub(super) status: String,
    /// Effective-now status (pending past expiry
    /// projects expired; the Task-1 lens).
    pub(super) effective_status: TransferStatus,
}

/// One pre-read `pending` row of ANOTHER target
/// (the raw row backs the conflict view; the
/// parsed state classifies it).
pub(super) struct OtherPendingRow {
    pub(super) row: DbRow,
    pub(super) target: String,
    pub(super) stored: StoredRow,
}

/// One DEAD other-target pending the create
/// transaction closes before it inserts the
/// new-target row (`uk_transfer_pending` holds
/// the per-bot pending slot; a dead pending must
/// be closed IN the same transaction, spec §6.2
/// 过期或 owner/version 已不匹配的 pending：先在
/// 本事务中关闭，再执行本次发起).
pub(super) struct OtherPendingClosure {
    pub(super) target: String,
    pub(super) kind: ClosureKind,
    pub(super) from_owner: String,
    pub(super) expected_version: u64,
}

/// Decision classification against the pre-read.
pub(super) enum DecisionPlan {
    /// Guard the missing row, then deny.
    Miss,
    /// Static eligibility denial (no writes, no tx).
    Forbidden,
    /// Guard the terminal row, then Unchanged.
    RepeatSame,
    /// Guard the terminal row, then no-write conflict.
    ConflictOnTerminal(AuthorityConflictCode),
    /// Close the dead pending (audited), then return
    /// the committed conflict.
    CloseAndConflict(ClosureKind),
    /// Apply the decision (accept/reject/cancel).
    Apply,
}

/// Classification of the initiation pre-read
/// (spec §6.2).
pub(super) enum RequestExecution {
    /// Valid same-snapshot pending: refresh.
    Refresh,
    /// Create the next round (fresh insert or
    /// terminal re-use), optionally closing a dead
    /// pending first.
    Create {
        over_row: bool,
        closure: Option<ClosureKind>,
    },
}

/// One transfer audit row of a mutation.
pub(super) struct TransferAuditDraft {
    pub(super) sequence: u64,
    pub(super) action: &'static str,
    pub(super) actor_kind: String,
    pub(super) actor_id: String,
    pub(super) subject: String,
    pub(super) before_state: Option<String>,
    pub(super) after_state: Option<String>,
}

/// Transaction-internal builder sharing the Task-2
/// guard/consumption discipline: every guard is a
/// query whose first-row first-column is consumed by
/// the trailing `SELECT ? AS t0, ...` step, so a
/// failed guard (0 rows) fails the whole
/// transaction at binding resolution.
pub(super) struct TransferTxBuilder {
    pub(super) steps: Vec<DbTransactionStep>,
    pub(super) guard_tokens: Vec<DbTransactionParam>,
    pub(super) bot_step: usize,
    pub(super) owner_step: Option<usize>,
}

impl TransferTxBuilder {
    /// Step 0 = Bot row lock read (version/name read
    /// under the same lock); step 1 (optional) =
    /// exactly-one-approved-owner guard exposing
    /// `owner_id`.
    pub(super) fn new(store: &DbBotAuthorityStore, bot_id: &str, with_owner_guard: bool) -> Self {
        let lock = lock_suffix(store);
        let mut builder = Self {
            steps: vec![DbTransactionStep::Query(
                DbStatement::with_params(
                    format!(
                        "SELECT bot_uuid, ownership_version, name FROM bcs_bots \
                         WHERE {} AND env = ? \
                           AND COALESCE(is_deleted, 0) = 0 \
                           AND ownership_version > 0{lock}",
                        store.bot_id_eq()
                    )
                    .as_str(),
                    vec![DbValue::from(bot_id), store.env_param()],
                ),
            )],
            guard_tokens: Vec::new(),
            bot_step: 0,
            owner_step: None,
        };
        if with_owner_guard {
            let owner_query = DbStatement::with_transaction_params(
                format!(
                    "SELECT t.owner_id, t.owner_n FROM \
                       (SELECT MIN(from_id) AS owner_id, COUNT(*) AS owner_n \
                        FROM edge_grants \
                        WHERE env = ? AND {} \
                          AND grant_kind = 'owner' AND grant_ref_id = 0 \
                          AND rules IS NULL AND status = 'approved') t \
                     WHERE t.owner_n = 1{lock}",
                    store.to_id_eq()
                )
                .as_str(),
                vec![
                    DbTransactionParam::value(store.env_param()),
                    builder.bot_ref(),
                ],
            );
            builder.steps.push(DbTransactionStep::Query(owner_query));
            builder.owner_step = Some(builder.steps.len() - 1);
            builder.guard_tokens.push(DbTransactionParam::query_result(
                builder.owner_step.expect("owner guard just added"),
                0,
                "owner_id",
            ));
        }
        builder
    }

    pub(super) fn bot_ref(&self) -> DbTransactionParam {
        DbTransactionParam::query_result(self.bot_step, 0, "bot_uuid")
    }

    pub(super) fn version_ref(&self) -> DbTransactionParam {
        DbTransactionParam::query_result(
            self.bot_step,
            0,
            "ownership_version",
        )
    }

    pub(super) fn name_ref(&self) -> DbTransactionParam {
        DbTransactionParam::query_result(self.bot_step, 0, "name")
    }

    pub(super) fn owner_ref(&self) -> DbTransactionParam {
        DbTransactionParam::query_result(
            self.owner_step.expect("owner guard required"),
            0,
            "owner_id",
        )
    }

    /// Query whose first row/column is consumed by the
    /// trailing step.
    pub(super) fn push_query(
        &mut self,
        sql: String,
        params: Vec<DbTransactionParam>,
    ) {
        self.steps.push(DbTransactionStep::Query(
            DbStatement::with_transaction_params(sql.as_str(), params),
        ));
        let index = self.steps.len() - 1;
        self.guard_tokens.push(DbTransactionParam::query_result(
            index, 0, "ok",
        ));
    }

    /// Count guard: the inner count must return
    /// exactly `expected`.
    pub(super) fn push_count_guard(
        &mut self,
        inner_sql: String,
        mut inner_params: Vec<DbTransactionParam>,
        expected: u64,
    ) {
        inner_params.push(DbTransactionParam::value(DbValue::from(expected)));
        self.push_query(
            format!("SELECT f.c AS ok FROM ({inner_sql}) f WHERE f.c = ?"),
            inner_params,
        );
    }

    /// Equality guard: each param pair (2k, 2k+1)
    /// must be equal.
    pub(super) fn push_eq_guard(&mut self, params: Vec<DbTransactionParam>) {
        let pairs = params.len() / 2;
        let select = (0..pairs * 2)
            .map(|index| format!("? AS p{index}"))
            .collect::<Vec<_>>()
            .join(", ");
        let mut where_terms = Vec::with_capacity(pairs);
        for index in 0..pairs {
            where_terms.push(format!("w.p{} = w.p{}", index * 2, index * 2 + 1));
        }
        self.push_query(
            format!(
                "SELECT w.p0 AS ok FROM (SELECT {select}) w WHERE {} AND w.p0 IS NOT NULL",
                where_terms.join(" AND ")
            ),
            params,
        );
    }

    /// One genuinely-changing checked write.
    pub(super) fn add_checked_write(&mut self, sql: String, params: Vec<DbTransactionParam>) {
        self.steps.push(DbTransactionStep::ExecuteChecked {
            statement: DbStatement::with_transaction_params(
                sql.as_str(),
                params,
            ),
            expected_affected_rows: 1,
        });
    }

    pub(super) fn finish(mut self) -> Vec<DbTransactionStep> {
        let references: Vec<DbTransactionParam> =
            std::mem::take(&mut self.guard_tokens);
        if !references.is_empty() {
            let placeholders = references
                .iter()
                .enumerate()
                .map(|(index, _)| format!("? AS t{index}"))
                .collect::<Vec<_>>()
                .join(", ");
            self.steps.push(DbTransactionStep::Query(
                DbStatement::with_transaction_params(
                    format!("SELECT {placeholders}"),
                    references,
                ),
            ));
        }
        self.steps
    }
}

pub(super) fn lock_suffix(store: &DbBotAuthorityStore) -> &'static str {
    match store.flavor() {
        DbSqlFlavor::Mysql => " FOR UPDATE",
        DbSqlFlavor::Sqlite => "",
    }
}

pub(super) fn gmt_modified_now(store: &DbBotAuthorityStore) -> &'static str {
    store.flavor().now()
}

pub(super) fn expires_now_plus_seven_days(store: &DbBotAuthorityStore) -> &'static str {
    match store.flavor() {
        DbSqlFlavor::Mysql => "DATE_ADD(CURRENT_TIMESTAMP, INTERVAL 7 DAY)",
        DbSqlFlavor::Sqlite => "datetime('now', '+7 days')",
    }
}

/// Deterministic local rollbacks a fresh pre-read may
/// resolve (same rule set as the manager paths).
pub(super) fn is_retryable(err: &bcs_db_api::DbError) -> bool {
    matches!(
        err,
        bcs_db_api::DbError::ConditionFailed { .. }
            | bcs_db_api::DbError::InvalidInput(_)
    ) || err.is_duplicate_key()
}

/// Decisions on a missing row / initiation targeting
/// the current owner: a clean state denial (Task 5
/// maps the pinned code to 409).
pub(super) fn transfer_state_denial(detail: String) -> ServiceError {
    ServiceError::Conflict(format!(
        "{}: {detail}",
        AuthorityConflictCode::TransferStateConflict.as_str()
    ))
}


/// Pre-read row parser shared with the estimator
/// below (one captured DB time).
pub(super) fn parse_stored_row(row: &DbRow, now_ms: u64) -> ServiceResult<StoredRow> {
    let status = crate::required_string(row, "status")?;
    let expires_at_ms = parse_required_epoch(
        &crate::required_string(row, "expires_at")?,
        "expires_at",
    )?;
    let effective_status = match status.as_str() {
        "pending" if now_ms >= expires_at_ms => TransferStatus::Expired,
        "pending" => TransferStatus::Pending,
        "accepted" => TransferStatus::Accepted,
        "rejected" => TransferStatus::Rejected,
        "cancelled" => TransferStatus::Cancelled,
        "expired" => TransferStatus::Expired,
        "invalidated" => TransferStatus::Invalidated,
        other => {
            return Err(ServiceError::InternalError(format!(
                "unknown transfer status: {other}"
            )));
        }
    };
    Ok(StoredRow {
        from_owner: crate::required_string(row, "from_owner_actor_id")?,
        expected_version: crate::required_u64(
            row,
            "expected_ownership_version",
        )?,
        status,
        effective_status,
    })
}

/// Initiation classification (spec §6.2).
pub(super) fn classify_request(
    owner: &str,
    version: u64,
    row: Option<&StoredRow>,
) -> RequestExecution {
    match row {
        None => RequestExecution::Create { over_row: false, closure: None },
        Some(row) => match row.effective_status {
            TransferStatus::Pending => {
                if row.from_owner == owner && row.expected_version == version {
                    RequestExecution::Refresh
                } else {
                    // Owner/version mismatched pending:
                    // close invalidated, then initiate.
                    RequestExecution::Create {
                        over_row: true,
                        closure: Some(ClosureKind::Invalidate),
                    }
                }
            }
            TransferStatus::Expired if row.status == "pending" => {
                // Stored pending past expiry: close
                // expired, then initiate.
                RequestExecution::Create {
                    over_row: true,
                    closure: Some(ClosureKind::Expire),
                }
            }
            _ => {
                // Terminal: same-target re-initiation
                // reuses the row.
                RequestExecution::Create {
                    over_row: true,
                    closure: None,
                }
            }
        },
    }
}

/// Decision classification (spec §6.4; authorization
/// precedence: identity gates before state handling).
pub(super) fn classify_decision(
    action: TransferAction,
    operator: &Operator,
    owner: &str,
    version: u64,
    row: Option<&StoredRow>,
) -> DecisionPlan {
    let Some(row) = row else {
        return DecisionPlan::Miss;
    };
    if action == TransferAction::Cancel {
        // 重复取消只核验本行发起 Human；操作 pending 的取消
        // 还必须核验当前 owner (spec §6.4).
        let is_initiator =
            operator.is_human() && operator.actor_id == row.from_owner;
        let still_owner = operator.is_human() && operator.actor_id == owner;
        let on_live_pending = row.status == "pending"
            && row.effective_status == TransferStatus::Pending;
        if !is_initiator || (on_live_pending && !still_owner) {
            return DecisionPlan::Forbidden;
        }
    }
    match row.effective_status {
        TransferStatus::Pending => {
            if row.from_owner != owner || row.expected_version != version {
                return DecisionPlan::CloseAndConflict(
                    ClosureKind::Invalidate,
                );
            }
            DecisionPlan::Apply
        }
        TransferStatus::Expired if row.status == "pending" => {
            DecisionPlan::CloseAndConflict(ClosureKind::Expire)
        }
        TransferStatus::Expired => DecisionPlan::ConflictOnTerminal(
            AuthorityConflictCode::TransferExpired,
        ),
        TransferStatus::Invalidated => DecisionPlan::ConflictOnTerminal(
            AuthorityConflictCode::OwnerChanged,
        ),
        TransferStatus::Accepted
        | TransferStatus::Rejected
        | TransferStatus::Cancelled => {
            if row.terminal_repeat_allowed(action) {
                DecisionPlan::RepeatSame
            } else {
                DecisionPlan::ConflictOnTerminal(
                    AuthorityConflictCode::TransferStateConflict,
                )
            }
        }
    }
}

impl StoredRow {
    pub(super) fn terminal_repeat_allowed(&self, action: TransferAction) -> bool {
        matches!(
            (self.status.as_str(), action),
            ("accepted", TransferAction::Accept)
                | ("rejected", TransferAction::Reject)
                | ("cancelled", TransferAction::Cancel)
        )
    }
}
