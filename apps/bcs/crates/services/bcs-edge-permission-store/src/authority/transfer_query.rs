//! Ownership transfer reads: the party-visible single receipt and the
//! inbox/outbox pages (plan Task 8, spec §11.1/§9.1).
//!
//! Read-only, no materialization: a physically-`pending` row whose
//! deadline already lapsed PROJECTS as `expired` with the fixed system
//! decider and `decided_at = expires_at` (§9.1) — the projection is
//! computed against the statement's own DB-clock read, never written.
//! The same effective-status rule is expressed INSIDE the list SQL
//! (filter before paging: a lapsed pending matches `expired` and stops
//! matching `pending`), and `total` shares one read transaction with the
//! returned page, so the two can never disagree across an expiry boundary.
//!
//! Visibility: only the two recorded parties see a row; anyone else gets
//! the concealment branch (`OwnershipTransferNotFound`, spec §11.2), so
//! transfers cannot be enumerated by id or by manager status. Receipts
//! carry the persisted `terminal_reason` (the status enum alone cannot
//! distinguish an `owner_changed` retry from a generic terminal state —
//! OT12 relies on this field being read).
//!
//! Budget (spec §13.5/OT22's read side): the single-receipt read is ONE
//! index-driven statement keyed by the unique `transfer_id`; the listing
//! is ONE read transaction of TWO statements (count + page) over the
//! spec §5.3 receiver/sender indexes — no per-row decoding queries, no
//! full-table history scans, regardless of how many other Bots' transfer
//! history exists.

use bcs_db_api::{DbRow, DbSqlFlavor, DbStatement, DbTransactionStep, DbValue};
use bcs_domain::TransferStatus;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::ownership_transfer::{
    ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage,
};
use bcs_service_api::types::{AuditActor, TerminalReason, TransferListDirection};
use bcs_service_api::{ServiceError, ServiceResult};

use crate::common::{parse_timestamp_epoch_ms, service_db_error};

/// The listing page ceiling (transport contracts 1..100 after its own
/// validation; the store clamps so an oversized limit never becomes an
/// unbounded page).
const TRANSFER_PAGE_LIMIT_CEILING: u64 = 100;

/// Fixed system decider recorded/projected for deadline-lapsed
/// materialization (the same marker the write lanes persist).
pub(super) const EXPIRED_SYSTEM_MARKER: &str = "ownership-deadline";

/// The full receipt column list of `bot_ownership_transfers`, shared by
/// every transfer read/decode site (this module's queries, the create
/// lane's idempotency lookup and the decide lane's receipt re-read).
pub(super) const TRANSFER_ROW_COLUMNS: &str = "transfer_id, env, bot_id, from_user_id, \
     to_user_id, expected_owner_version, client_request_id, status, expires_at, \
     decision_actor_kind, decided_by, decided_at, result_owner_version, \
     terminal_reason, bot_name_snapshot, gmt_create, gmt_modified";

/// The DB-clock projection column every receipt read appends
/// (SQLite `CURRENT_TIMESTAMP` / MySQL `NOW()`, aliased `db_now`).
pub(super) fn db_now_expr(flavor: &DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Sqlite => "CURRENT_TIMESTAMP AS db_now",
        DbSqlFlavor::Mysql => "NOW() AS db_now",
    }
}

/// The create lane's deadline expression: DB create-time + 7 days, fixed
/// inside the creating transaction (spec §9.1 uses the database UTC clock).
pub(super) fn expires_plus_7_days(flavor: &DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Sqlite => "datetime(CURRENT_TIMESTAMP, '+7 days')",
        DbSqlFlavor::Mysql => "(CURRENT_TIMESTAMP + INTERVAL 7 DAY)",
    }
}

/// Per-flavor `human_<user_id>` edge-id expression over one `?` bound to
/// the bare user id (SQLite `||`, MySQL `CONCAT`).
pub(super) fn human_expr(flavor: &DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Sqlite => "'human_' || ?",
        DbSqlFlavor::Mysql => "CONCAT('human_', ?)",
    }
}

/// Binary-identity comparison expression for one `edge_grants` identity
/// column (`from_id` / `to_id`, optionally qualified by an alias).
///
/// Live MySQL keeps the LEGACY `edge_grants` column collation: migration
/// 032 deliberately created the new authority tables with
/// `COLLATE utf8mb4_bin` but left `edge_grants` untouched, relying on the
/// store's per-flavor dialect encapsulation (spec §5.3, the same
/// treatment the older grant queries already apply — `CAST(col AS BINARY)`
/// on MySQL, `col COLLATE BINARY` on SQLite). Without the pin, MySQL's
/// default case-insensitive collation would fold `USER-A` onto
/// `user-a`'s approved owner/manager rows and the authority answers a
/// foreign identity. Callers wrap the COLUMN side of every authority-lane
/// identity predicate; bound parameters never need wrapping.
pub(super) fn binary_identity(flavor: &DbSqlFlavor, column: &str) -> String {
    match flavor {
        DbSqlFlavor::Mysql => format!("CAST({column} AS BINARY)"),
        DbSqlFlavor::Sqlite => format!("{column} COLLATE BINARY"),
    }
}

/// Per-flavor `FROM DUAL` tail for INSERT…SELECT guarded statements.
pub(super) fn from_dual(flavor: &DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Sqlite => "",
        DbSqlFlavor::Mysql => " FROM DUAL",
    }
}

/// The in-database effective-status filter of the listing lane (spec §9.1:
/// the rule runs in the SQL BEFORE paging — a lapsed pending no longer
/// matches `pending` and now matches `expired`).
pub(super) fn list_filter_predicate(flavor: &DbSqlFlavor, status: Option<TransferStatus>) -> String {
    let now = flavor.now();
    match status {
        None => "1 = 1".to_string(),
        Some(TransferStatus::Pending) => {
            format!("(status = 'pending' AND expires_at > {now})")
        }
        Some(TransferStatus::Expired) => {
            format!("(status = 'expired' OR (status = 'pending' AND expires_at <= {now}))")
        }
        Some(other) => {
            format!("(status = '{}')", transfer_status_text(other))
        }
    }
}

/// Strictly decode one receipt row with the effective-expiry PROJECTION
/// against the statement's own DB-clock column (`db_now`), so a physically
/// `pending` row whose deadline lapsed reads exactly as the later
/// materialization would persist it: status `expired`, system decider,
/// `decided_at = expires_at` (§9.1: 物化保持同一逻辑决定时间).
///
/// Every illegal shape — unknown status, undecodable timestamps or
/// decision columns, terminal rows missing their decision columns — is
/// the fail-closed `CorruptAuthority` branch, never an implicit empty
/// projection.
pub(super) fn decode_transfer_row(
    row: &DbRow,
    env: &str,
) -> ServiceResult<OwnershipTransfer> {
    let bot_id = required(row, "bot_id")?;
    let corrupt = |detail: String| {
        ServiceError::Authority(AuthorityError::CorruptAuthority {
            bot_id: bot_id.clone(),
            env: env.to_string(),
            detail,
        })
    };
    let row_env = required(row, "env")?;
    if row_env != env {
        return Err(corrupt(format!(
            "transfer row of env '{row_env}' leaked into the env '{env}' read"
        )));
    }
    let expires_ms =
        parse_timestamp_epoch_ms(&required(row, "expires_at")?).ok_or_else(|| {
            corrupt("undecodable expires_at text".to_string())
        })?;
    let stored_status = required(row, "status").and_then(|text| decode_status(&text, &corrupt))?;
    let terminal_reason = match row.get_string("terminal_reason").ok().flatten() {
        None => Ok(None),
        Some(text) => decode_terminal_reason(&text, &corrupt).map(Some),
    }?;

    // Decision columns decode with the schema's CHECK semantics.
    let kind = row
        .get_string("decision_actor_kind")
        .ok()
        .flatten();
    let decided_by = required_opt(row, "decided_by")?;
    let decision_actor = match (kind.as_deref(), decided_by.as_deref()) {
        (Some("human"), Some(user_id)) if !user_id.is_empty() => Ok(Some(AuditActor::Human {
            user_id: user_id.to_string(),
        })),
        (Some("system"), Some(name)) if !name.is_empty() => Ok(Some(AuditActor::System {
            name: name.to_string(),
        })),
        (None, None) => Ok(None),
        (Some("human"), None) | (Some("system"), None) => {
            Err(corrupt("decision kind without a decider".to_string()))
        }
        _ => Err(corrupt(format!(
            "undecodable decision actor columns ({:?}, {:?})",
            kind, decided_by
        ))),
    }?;
    let decided_at_ms = match decided_by.is_some() {
        true => Some(parse_timestamp_epoch_ms(&required(row, "decided_at")?).ok_or_else(
            || corrupt("undecodable decided_at text".to_string()),
        )?),
        false => None,
    };
    if stored_status != TransferStatus::Pending && decision_actor.is_none() {
        return Err(corrupt(
            "terminal transfer row without a decision actor".to_string(),
        ));
    }

    let mut status = stored_status;
    let mut effective_decision_actor = decision_actor;
    let mut effective_decided_at = decided_at_ms;
    let db_now_ms = parse_timestamp_epoch_ms(
        &row.get_string("db_now")
            .ok()
            .flatten()
            .ok_or_else(|| corrupt("missing db_now projection column".to_string()))?,
    )
    .ok_or_else(|| corrupt("undecodable db_now text".to_string()))?;
    if stored_status == TransferStatus::Pending && db_now_ms >= expires_ms {
        status = TransferStatus::Expired;
        effective_decision_actor = Some(AuditActor::System {
            name: EXPIRED_SYSTEM_MARKER.to_string(),
        });
        effective_decided_at = Some(expires_ms);
    }

    let gmt_create = parse_timestamp_epoch_ms(&required(row, "gmt_create")?)
        .ok_or_else(|| corrupt("undecodable gmt_create text".to_string()))?;
    let gmt_modified = parse_timestamp_epoch_ms(&required(row, "gmt_modified")?)
        .ok_or_else(|| corrupt("undecodable gmt_modified text".to_string()))?;

    Ok(OwnershipTransfer {
        transfer_id: required(row, "transfer_id")?,
        env: row_env,
        bot_id: bot_id.clone(),
        from_user_id: required(row, "from_user_id")?,
        to_user_id: required(row, "to_user_id")?,
        expected_owner_version: required_u64_column(row, "expected_owner_version")?,
        client_request_id: required(row, "client_request_id")?,
        status,
        expires_at: expires_ms,
        decision_actor: effective_decision_actor,
        decided_at: effective_decided_at,
        result_owner_version: match row.get_i64("result_owner_version").ok().flatten() {
            Some(value) if value >= 0 => Some(value as u64),
            None => None,
            Some(value) => {
                return Err(corrupt(format!(
                    "negative result_owner_version {value}"
                )))
            }
        },
        terminal_reason,
        bot_name_snapshot: required(row, "bot_name_snapshot")?,
        gmt_create,
        gmt_modified,
    })
}

fn transfer_status_text(status: TransferStatus) -> &'static str {
    match status {
        TransferStatus::Pending => "pending",
        TransferStatus::Accepted => "accepted",
        TransferStatus::Rejected => "rejected",
        TransferStatus::Cancelled => "cancelled",
        TransferStatus::Expired => "expired",
        TransferStatus::Invalidated => "invalidated",
    }
}

fn decode_status(
    text: &str,
    corrupt: &dyn Fn(String) -> ServiceError,
) -> ServiceResult<TransferStatus> {
    Ok(match text {
        "pending" => TransferStatus::Pending,
        "accepted" => TransferStatus::Accepted,
        "rejected" => TransferStatus::Rejected,
        "cancelled" => TransferStatus::Cancelled,
        "expired" => TransferStatus::Expired,
        "invalidated" => TransferStatus::Invalidated,
        other => return Err(corrupt(format!("unknown transfer status '{other}'"))),
    })
}

fn decode_terminal_reason(
    text: &str,
    corrupt: &dyn Fn(String) -> ServiceError,
) -> ServiceResult<TerminalReason> {
    match text {
        "bot_deleted" => Ok(TerminalReason::BotDeleted),
        "actor_unavailable" => Ok(TerminalReason::ActorUnavailable),
        "owner_changed" => Ok(TerminalReason::OwnerChanged),
        other => Err(corrupt(format!("unknown terminal reason '{other}'"))),
    }
}

fn required(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    match row.get_string(column) {
        Ok(Some(text)) if !text.is_empty() => Ok(text),
        Ok(Some(_)) | Ok(None) | Err(_) => Err(ServiceError::InternalError(format!(
            "authority transfer row: column '{column}' missing or empty"
        ))),
    }
}

fn required_opt(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    match row.get_string(column) {
        Ok(opt) => Ok(opt),
        Err(err) => Err(service_db_error(column, err)),
    }
}

fn required_u64_column(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    match row.get_i64(column) {
        Ok(Some(value)) if value >= 0 => Ok(value as u64),
        Ok(Some(value)) => Err(ServiceError::InternalError(format!(
            "authority transfer row: negative {column} {value}"
        ))),
        Ok(None) | Err(_) => Err(ServiceError::InternalError(format!(
            "authority transfer row: column '{column}' missing or NULL"
        ))),
    }
}

impl super::reads::DbBotAuthorityStore {
    // ------------------------------------------------------------------
    // get_transfer: ONE statement, party-visible, concealment otherwise
    // ------------------------------------------------------------------

    pub(super) async fn get_transfer_inner(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> ServiceResult<OwnershipTransfer> {
        if viewer_user_id.trim().is_empty() || transfer_id.trim().is_empty() {
            // Blank identities get the concealment branch, never a leak.
            return Err(not_visible(transfer_id));
        }
        let rows = self
            .db
            .query(DbStatement::with_params(
                &format!(
                    "SELECT {TRANSFER_ROW_COLUMNS}, {} FROM bot_ownership_transfers \
                     WHERE env = ? AND transfer_id = ? LIMIT 1",
                    db_now_expr(&self.flavor)
                ),
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(transfer_id),
                ],
            ))
            .await
            .map_err(|err| service_db_error("authority_transfer_get", err))?;
        let Some(row) = rows.into_iter().next() else {
            // A missing id and an unauthorized viewer share ONE branch, so
            // neither reveals which one it was (anti-enumerment, §11.2).
            return Err(not_visible(transfer_id));
        };
        let from_user_id = row
            .get_string("from_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let to_user_id = row
            .get_string("to_user_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        if from_user_id != viewer_user_id && to_user_id != viewer_user_id {
            return Err(not_visible(transfer_id));
        }
        decode_transfer_row(&row, &self.env)
    }

    // ------------------------------------------------------------------
    // list_transfers: ONE read transaction (count + page), effective-status
    // filter inside the SQL, one snapshot for both statements
    // ------------------------------------------------------------------

    pub(super) async fn list_transfers_inner(
        &self,
        query: ListOwnershipTransfers,
    ) -> ServiceResult<OwnershipTransferPage> {
        if query.viewer_user_id.trim().is_empty() {
            return Err(not_visible(""));
        }
        let side_column = match query.direction {
            TransferListDirection::Received => "to_user_id",
            TransferListDirection::Sent => "from_user_id",
        };
        let filter = list_filter_predicate(&self.flavor, query.status);
        let limit = query.limit.min(TRANSFER_PAGE_LIMIT_CEILING);
        let results = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(DbStatement::with_params(
                    &format!(
                        "SELECT COUNT(*) AS total FROM bot_ownership_transfers \
                         WHERE env = ? AND {side_column} = ? AND {filter}"
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(query.viewer_user_id.as_str()),
                    ],
                )),
                DbTransactionStep::Query(DbStatement::with_params(
                    &format!(
                        "SELECT {TRANSFER_ROW_COLUMNS}, {} FROM bot_ownership_transfers \
                         WHERE env = ? AND {side_column} = ? AND {filter} \
                         ORDER BY gmt_create DESC, transfer_id ASC LIMIT ? OFFSET ?",
                        db_now_expr(&self.flavor)
                    ),
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(query.viewer_user_id.as_str()),
                        DbValue::from(limit.min(i64::MAX as u64) as i64),
                        DbValue::from(query.offset.min(i64::MAX as u64) as i64),
                    ],
                )),
            ])
            .await
            .map_err(|err| service_db_error("authority_transfer_list", err))?;
        let total = match &results[0] {
            bcs_db_api::DbTransactionStepResult::Rows(rows) => rows
                .first()
                .and_then(|row| row.get_i64("total").ok().flatten())
                .map(|value| value.max(0) as u64)
                .unwrap_or(0),
            _ => {
                return Err(ServiceError::InternalError(
                    "authority_transfer_list: expected the count query".to_string(),
                ))
            }
        };
        let page_rows = match &results[1] {
            bcs_db_api::DbTransactionStepResult::Rows(rows) => rows.clone(),
            _ => {
                return Err(ServiceError::InternalError(
                    "authority_transfer_list: expected the page query".to_string(),
                ))
            }
        };
        let items = page_rows
            .iter()
            .map(|row| decode_transfer_row(row, &self.env))
            .collect::<ServiceResult<Vec<OwnershipTransfer>>>()?;
        Ok(OwnershipTransferPage { items, total })
    }
}

/// The concealment branch (404/`ownership_transfer_not_found` at the
/// application layer): one constructor so the store keeps a single shape.
pub(super) fn not_visible(transfer_id: &str) -> ServiceError {
    ServiceError::Authority(AuthorityError::OwnershipTransferNotFound {
        transfer_id: transfer_id.to_string(),
    })
}