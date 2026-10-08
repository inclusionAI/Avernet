//! Ownership transfer creation (plan Task 8, spec §10.1).
//!
//! `create_transfer` is the ONE-Bot-transaction create lane:
//! 1. a validated READ transaction (fast path, never an authority source)
//!    resolves the durable idempotency key `(env, bot_id, from_user_id,
//!    client_request_id)` — a same-payload replay returns the ORIGINAL
//!    committed receipt with `created = false` WITHOUT re-requiring the
//!    caller's current ownership and without re-executing anything (the
//!    branch also serves terminal historical receipts, spec §10.1 step 1);
//!    a different payload under the same key is `idempotency_conflict`.
//!    The same read validates the fresh-pending preconditions: live
//!    physical Bot (`BotNotFound`), initialized ownership with the unique
//!    approved owner slot (`OwnershipNotInitialized`/`CorruptAuthority`),
//!    actor-is-current-owner (`Forbidden`), live same-env recipient that is
//!    not the actor (`InvalidSubject`), the matching `expected_owner_version`
//!    and the classification of the Bot's current pending (valid → pending
//!    conflict; lapsed / owner-version-mismatched → the write phase will
//!    clean them).
//! 2. ONE WRITE transaction on the serialized Bot boundary (lock →
//!    guarded cleanups → guarded INSERT): the lapsed pendings materialize
//!    `expired` (`decided_at = expires_at`, system decider) and the
//!    mismatched pendings materialize `invalidated(owner_changed)`
//!    (decider `ownership-validation`), releasing the unique pending slot;
//!    the guarded INSERT then re-proves EVERY validated predicate against
//!    CURRENT rows as conditions of the changing statement itself, so a
//!    drifted attempt rolls the WHOLE transaction back (`ExecuteChecked`
//!    pin — a same-key racer collapses into the durable idempotency unique
//!    key and settles as a replay or an idempotency conflict on the
//!    bounded retry).
//!
//! A stale `expected_owner_version` (everything else legal) is decided in
//! the READ phase: `Conflict` (`ownership_changed` semantics documented on
//! the port), NO row persisted, NO cleanup (the client re-reads ownership
//! and retries with the fresh version — spec §10.1 step 3 ordering).
//!
//! `expires_at` is fixed by the DATABASE clock inside the same
//! transaction: create-time + 7 days (spec §9.1).
//!
//! Statement budget (spec §13.5, asserted by the Task 8 tests): the read
//! phase is ONE read transaction of index-driven queries (idempotency
//! key, one Bot aggregate incl. the bot name snapshot, the unique-slot
//! owner edge, the recipient row and the Bot's single pending row); the
//! write phase is the Bot lock + TWO Bot-scoped cleanup UPDATEs + ONE
//! guarded INSERT; the committed receipt re-read adds ONE statement. All
//! statements are index-driven (idempotency key, pending slot,
//! env+bot/actor indexes) — never a scan over other Bots' history.

use bcs_db_api::{
    DbRow, DbTransactionStep, DbTransactionStepResult, DbValue,
};
use bcs_service_api::port::repo::bot_authority::{human_actor_id, user_id_from_actor};
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::ownership_transfer::{CreateOwnershipTransfer, CreateTransferResult};
use bcs_service_api::{ServiceError, ServiceResult};
use tracing::warn;

use super::transfer_query::{
    db_now_expr, decode_transfer_row, expires_plus_7_days, from_dual, TRANSFER_ROW_COLUMNS,
};
use crate::common::{parse_timestamp_epoch_ms, service_db_error};

/// Optimistic-write attempts before surfacing a retryable conflict: each
/// attempt re-validates, so genuine state settles into its business branch
/// long before this budget is spent.
const MAX_CREATE_ATTEMPTS: usize = 3;

enum CreateAttempt {
    /// The committed (or replayed) result.
    Done(CreateTransferResult),
    /// The optimistic window drifted; the caller re-validates.
    Drift(bcs_db_api::DbError),
    /// A business or infrastructure branch — surface it unchanged.
    Service(ServiceError),
}

impl From<ServiceError> for CreateAttempt {
    fn from(err: ServiceError) -> Self {
        CreateAttempt::Service(err)
    }
}

impl super::reads::DbBotAuthorityStore {
    pub(super) async fn create_transfer_inner(
        &self,
        command: CreateOwnershipTransfer,
    ) -> ServiceResult<CreateTransferResult> {
        fail_closed_create(&command)?;
        let mut last_drift = None;
        for _ in 0..MAX_CREATE_ATTEMPTS {
            match self.create_transfer_once(&command).await {
                CreateAttempt::Done(result) => return Ok(result),
                CreateAttempt::Drift(err) => {
                    warn!(
                        bot_id = %command.bot_id,
                        "ownership transfer create lost its optimistic window, \
                         re-validating: {err}"
                    );
                    last_drift = Some(err);
                }
                CreateAttempt::Service(err) => return Err(err),
            }
        }
        Err(ServiceError::Authority(AuthorityError::Conflict(format!(
            "concurrent ownership transfer create on bot '{}'; retry: {}",
            command.bot_id,
            last_drift
                .map(|err| err.to_string())
                .unwrap_or_else(|| "optimistic window lost repeatedly".to_string())
        ))))
    }

    /// One validated-read → guarded-commit attempt.
    async fn create_transfer_once(
        &self,
        command: &CreateOwnershipTransfer,
    ) -> CreateAttempt {
        // -- Phase A: the validated read transaction (fast path) --------
        let validated = match self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.idempotency_row_statement(command)),
                DbTransactionStep::Query(self.bot_aggregate_statement(command)),
                DbTransactionStep::Query(
                    self.owner_slot_statement(&command.bot_id),
                ),
                DbTransactionStep::Query(
                    self.pending_row_statement(&command.bot_id),
                ),
            ])
            .await
        {
            Ok(steps) => steps,
            Err(err) => {
                return CreateAttempt::Service(service_db_error(
                    "authority_transfer_create_read",
                    err,
                ))
            }
        };

        // -- Idempotency replay / conflict (never re-executes, never
        //    re-requires current ownership). ----------------------------
        if let Some(row) = step_rows(&validated, 0, "the idempotency query").ok().and_then(|rows| {
            rows.first().cloned()
        }) {
            let to_user_id = row
                .get_string("to_user_id")
                .ok()
                .flatten()
                .unwrap_or_default();
            let expected_owner_version = row
                .get_i64("expected_owner_version")
                .ok()
                .flatten()
                .unwrap_or(-1);
            if to_user_id != command.to_user_id
                || expected_owner_version != command.expected_owner_version as i64
            {
                return CreateAttempt::Service(ServiceError::Authority(
                    AuthorityError::Conflict(format!(
                        "idempotency conflict: key '{}' on bot '{}' was already used with \
                         a different payload (to_user_id/expected_owner_version)",
                        command.client_request_id, command.bot_id
                    )),
                ));
            }
            let receipt = match decode_transfer_row(&row, &self.env) {
                Ok(receipt) => receipt,
                Err(err) => return CreateAttempt::Service(err),
            };
            return CreateAttempt::Done(CreateTransferResult {
                receipt,
                created: false,
            });
        }

        // -- Fresh-pending validation at the read snapshot. -------------
        let aggregate_rows = match step_rows(&validated, 1, "the bot aggregate query") {
            Ok(rows) => rows,
            Err(err) => return CreateAttempt::Service(err),
        };
        let Some(aggregate) = aggregate_rows.into_iter().next() else {
            return CreateAttempt::Service(ServiceError::BotNotFound(
                command.bot_id.clone(),
            ));
        };
        let ownership_version = match read_u64(&aggregate, "ownership_version") {
            Ok(value) => value,
            Err(err) => return CreateAttempt::Service(err),
        };
        if ownership_version
            == bcs_domain::UNINITIALIZED_OWNERSHIP_VERSION
        {
            return CreateAttempt::Service(ServiceError::Authority(
                AuthorityError::OwnershipNotInitialized {
                    bot_id: command.bot_id.clone(),
                    env: self.env.clone(),
                },
            ));
        }
        // Recipient liveness is folded into the aggregate (NULL-safe).
        let recipient_live = match read_u64(&aggregate, "recipient_live") {
            Ok(value) => value,
            Err(err) => return CreateAttempt::Service(err),
        };
        let bot_name_snapshot = match aggregate
            .get_string("bot_name")
            .ok()
            .flatten() {
            Some(name) => name,
            None => command.bot_id.clone(),
        };

        // -- The unique approved owner slot decodes STRICTLY; a
        //    non-human owner from_id is corruption, never an implicit
        //    deny. ------------------------------------------------------
        let owner_rows = match step_rows(&validated, 2, "the owner-slot query") {
            Ok(rows) => rows,
            Err(err) => return CreateAttempt::Service(err),
        };
        match owner_rows.len() {
            1 => {}
            count => {
                return CreateAttempt::Service(self.corrupt(
                    &command.bot_id,
                    format!("initialized bot has {count} approved owner edges (transfer create denied)"),
                ))
            }
        }
        let owner_from_id = owner_rows[0]
            .get_string("from_id")
            .ok()
            .flatten()
            .unwrap_or_default();
        let Some(owner_user_id) = user_id_from_actor(&owner_from_id) else {
            return CreateAttempt::Service(self.corrupt(
                &command.bot_id,
                format!("owner edge from non-human actor id '{owner_from_id}'"),
            ));
        };
        if owner_user_id != command.actor_user_id {
            return CreateAttempt::Service(ServiceError::Authority(
                AuthorityError::Forbidden(format!(
                    "user '{}' is not the owner of bot '{}'; only the current owner may \
                     initiate an ownership transfer",
                    command.actor_user_id, command.bot_id
                )),
            ));
        }
        if command.to_user_id == command.actor_user_id {
            return CreateAttempt::Service(ServiceError::Authority(
                AuthorityError::InvalidSubject(
                    "the owner cannot transfer a bot to themselves".to_string(),
                ),
            ));
        }
        if recipient_live == 0 {
            return CreateAttempt::Service(ServiceError::Authority(
                AuthorityError::InvalidSubject(format!(
                    "recipient '{}' is not a live human actor in env '{}'",
                    command.to_user_id, self.env
                )),
            ));
        }
        if command.expected_owner_version != ownership_version {
            // Stale version snapshot: reject with the `ownership_changed`
            // conflict semantics and persist NOTHING, clean NOTHING — the
            // client re-reads ownership and retries with the fresh version
            // (spec §10.1 step 3; HTTP mapping is Task 14's).
            return CreateAttempt::Service(ServiceError::Authority(
                AuthorityError::Conflict(format!(
                    "ownership changed on bot '{}': expected version {} but the current \
                     version is {}; re-read ownership and retry with a fresh snapshot \
                     (ownership_changed)",
                    command.bot_id, command.expected_owner_version, ownership_version
                )),
            ));
        }

        // -- Classify the Bot's current pending (at most one by the
        //    partial unique slot). --------------------------------------
        let pending_rows = match step_rows(&validated, 3, "the pending-row query") {
            Ok(rows) => rows,
            Err(err) => return CreateAttempt::Service(err),
        };
        if let Some(pending) = pending_rows.first() {
            let from_user_id = pending
                .get_string("from_user_id")
                .ok()
                .flatten()
                .unwrap_or_default();
            let expected = pending
                .get_i64("expected_owner_version")
                .ok()
                .flatten()
                .unwrap_or(-1) as u64;
            let expires_at = pending
                .get_string("expires_at")
                .ok()
                .flatten()
                .unwrap_or_default();
            let db_now = pending
                .get_string("db_now")
                .ok()
                .flatten()
                .unwrap_or_default();
            let lapsed_row = lapsed(expires_at.as_str(), db_now.as_str());
            let mismatched = from_user_id != owner_user_id || expected != ownership_version;
            if !lapsed_row && !mismatched {
                // A still-valid pending of any key blocks the new request
                // (the cleanups below only release lapsed/mismatched rows).
                return CreateAttempt::Service(ServiceError::Authority(
                    AuthorityError::Conflict(format!(
                        "bot '{}' already has a valid pending ownership transfer \
                         (ownership_transfer_pending)",
                        command.bot_id
                    )),
                ));
            }
            // lapsed / mismatched: the WRITE phase cleans them, then the
            // guarded INSERT proceeds — the current owner must be able to
            // unblock a new creation without waiting for the dead
            // request's recipient (spec §10.1 step 4).
        }

        // -- Phase B: the guarded Bot write transaction ------------------
        let transfer_id = uuid::Uuid::new_v4().to_string();
        let write = self
            .db
            .transaction(vec![
                DbTransactionStep::Query(self.bot_lock_statement(&command.bot_id)),
                // Cleanup 1: lapsed pendings materialize `expired`
                // (system decider, decided_at = the logical expiry time).
                DbTransactionStep::Execute(self.cleanup_expired_statement(&command.bot_id)),
                // Cleanup 2: owner/version-mismatched pendings materialize
                // `invalidated(owner_changed)`.
                DbTransactionStep::Execute(
                    self.cleanup_mismatched_statement(&command.bot_id),
                ),
                // The guarded pending INSERT: every validated predicate is
                // re-proved against CURRENT rows as the statement's own
                // conditions (unique idempotency key as the final defense).
                DbTransactionStep::ExecuteChecked {
                    statement: self.pending_insert_statement(
                        &transfer_id,
                        command,
                        &bot_name_snapshot,
                    ),
                    expected_affected_rows: 1,
                },
            ])
            .await;
        match write {
            Ok(_) => {}
            Err(err) => return self.map_create_write_error(err),
        }

        // -- Committed: the durable receipt re-read (ONE statement). ----
        let rows = match self
            .db
            .query(self.receipt_statement(&transfer_id))
            .await
        {
            Ok(rows) => rows,
            Err(err) => {
                return CreateAttempt::Service(service_db_error(
                    "authority_transfer_create_receipt",
                    err,
                ))
            }
        };
        let Some(row) = rows.into_iter().next() else {
            return CreateAttempt::Service(ServiceError::InternalError(format!(
                "ownership transfer create: committed receipt '{transfer_id}' is unreadable"
            )));
        };
        match decode_transfer_row(&row, &self.env) {
            Ok(receipt) => CreateAttempt::Done(CreateTransferResult {
                receipt,
                created: true,
            }),
            Err(err) => CreateAttempt::Service(err),
        }
    }

    /// Drift classification of the write phase: a pinned mismatch or a
    /// durable unique-key loss re-validates; everything else surfaces.
    fn map_create_write_error(&self, err: bcs_db_api::DbError) -> CreateAttempt {
        if err.is_duplicate_key() {
            // A same-key racer committed first: the retry replays their
            // receipt (or surfaces the conflicting payload).
            return CreateAttempt::Drift(err);
        }
        match err {
            bcs_db_api::DbError::ConditionFailed { .. } => CreateAttempt::Drift(err),
            other => CreateAttempt::Service(service_db_error(
                "authority_transfer_create_write",
                other,
            )),
        }
    }

    // ------------------------------------------------------------------
    // Statements
    // ------------------------------------------------------------------

    /// The durable idempotency key lookup: `(env, bot_id, from_user_id,
    /// client_request_id)` (spec §5.3), riding the receipt-column decode.
    fn idempotency_row_statement(
        &self,
        command: &CreateOwnershipTransfer,
    ) -> bcs_db_api::DbStatement {
        bcs_db_api::DbStatement::with_params(
            &format!(
                "SELECT {TRANSFER_ROW_COLUMNS}, {} FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND from_user_id = ? AND client_request_id = ? \
                 LIMIT 1",
                db_now_expr(&self.flavor)
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(command.bot_id.as_str()),
                DbValue::from(command.actor_user_id.as_str()),
                DbValue::from(command.client_request_id.as_str()),
            ],
        )
    }

    /// One-row aggregate of the fresh-create validation inputs: the live
    /// Bot row (ownership_version, name), the DB clock for expiry
    /// classification, and the recipient's live-human count. Zero rows
    /// means the Bot does not exist in this env (or is soft-deleted).
    fn bot_aggregate_statement(
        &self,
        command: &CreateOwnershipTransfer,
    ) -> bcs_db_api::DbStatement {
        bcs_db_api::DbStatement::with_params(
            &format!(
                "SELECT b.ownership_version AS ownership_version, b.name AS bot_name, \
                        {} , \
                        (SELECT COUNT(*) FROM bcs_bots rh \
                          WHERE rh.bot_uuid = ? AND rh.env = ? AND rh.actor_kind = 'human' \
                            AND COALESCE(rh.is_deleted, 0) = 0) AS recipient_live \
                 FROM bcs_bots b \
                 WHERE b.bot_uuid = ? AND b.env = ? AND COALESCE(b.is_deleted, 0) = 0",
                db_now_expr(&self.flavor)
            ),
            vec![
                DbValue::from(human_actor_id(&command.to_user_id)),
                DbValue::from(self.env.as_str()),
                DbValue::from(command.bot_id.as_str()),
                DbValue::from(self.env.as_str()),
            ],
        )
    }

    /// The approved owner-slot rows of the Bot (the strict decode input).
    fn owner_slot_statement(&self, bot_id: &str) -> bcs_db_api::DbStatement {
        bcs_db_api::DbStatement::with_params(
            "SELECT from_id FROM edge_grants \
             WHERE env = ? AND to_id = ? AND grant_kind = 'owner' AND status = 'approved' \
             ORDER BY id",
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
            ],
        )
    }

    /// The Bot's at-most-one stored pending row (partial unique slot), with
    /// the read transaction's own DB-clock alias for expiry comparison.
    fn pending_row_statement(&self, bot_id: &str) -> bcs_db_api::DbStatement {
        bcs_db_api::DbStatement::with_params(
            &format!(
                "SELECT from_user_id, expected_owner_version, expires_at, {} \
                 FROM bot_ownership_transfers \
                 WHERE env = ? AND bot_id = ? AND status = 'pending' LIMIT 1",
                db_now_expr(&self.flavor)
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
            ],
        )
    }

    /// Cleanup 1: this Bot's time-lapsed pendings materialize `expired`
    /// (system decider `ownership-deadline`); `decided_at = expires_at`
    /// keeps the logical decision time the later projection shows.
    fn cleanup_expired_statement(&self, bot_id: &str) -> bcs_db_api::DbStatement {
        let now = self.flavor.now();
        bcs_db_api::DbStatement::with_params(
            &format!(
                "UPDATE bot_ownership_transfers \
                 SET status = 'expired', decision_actor_kind = 'system', \
                     decided_by = 'ownership-deadline', decided_at = expires_at, \
                     gmt_modified = {now} \
                 WHERE env = ? AND bot_id = ? AND status = 'pending' \
                   AND expires_at <= {now}"
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
            ],
        )
    }

    /// Cleanup 2: this Bot's owner/version-mismatched pendings materialize
    /// `invalidated(owner_changed)` (the §10.2 committed-invalidation
    /// statement, applied in the create cleanup). The current-owner and
    /// current-version subqueries make the mismatch check a condition of
    /// the changing statement itself under the same lock; a missing owner
    /// edge (NULL) keeps the row untouched so the strictly-validated
    /// caller paths decide it under their own branches.
    fn cleanup_mismatched_statement(&self, bot_id: &str) -> bcs_db_api::DbStatement {
        let now = self.flavor.now();
        bcs_db_api::DbStatement::with_params(
            &format!(
                "UPDATE bot_ownership_transfers \
                 SET status = 'invalidated', terminal_reason = 'owner_changed', \
                     decision_actor_kind = 'system', decided_by = 'ownership-validation', \
                     decided_at = {now}, gmt_modified = {now} \
                 WHERE env = ? AND bot_id = ? AND status = 'pending' \
                   AND ( from_user_id <> (SELECT SUBSTR(o.from_id, 7) FROM edge_grants o \
                           WHERE o.env = ? AND o.to_id = ? AND o.grant_kind = 'owner' \
                             AND o.status = 'approved' ORDER BY o.id LIMIT 1) \
                         OR expected_owner_version <> (SELECT b.ownership_version \
                               FROM bcs_bots b \
                               WHERE b.bot_uuid = ? AND b.env = ? \
                                 AND COALESCE(b.is_deleted, 0) = 0) )"
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(self.env.as_str()),
                DbValue::from(bot_id),
                DbValue::from(bot_id),
                DbValue::from(self.env.as_str()),
            ],
        )
    }

    /// The guarded pending INSERT: every validated predicate is a condition
    /// of the changing statement itself (live+initialized Bot, unique
    /// owner slot, actor-is-current-owner, exact version, live recipient,
    /// no self-transfer, slot free after the cleanups ran); the durable
    /// unique idempotency key and the pending slot are the final defenses
    /// that turn a same-key/same-slot racer into a bounded retry.
    fn pending_insert_statement(
        &self,
        transfer_id: &str,
        command: &CreateOwnershipTransfer,
        bot_name_snapshot: &str,
    ) -> bcs_db_api::DbStatement {
        let actor_edge = human_actor_id(&command.actor_user_id);
        let recipient_edge = human_actor_id(&command.to_user_id);
        let params = vec![
            // SELECT-list values (transfer identity + payload + snapshot).
            DbValue::from(transfer_id),
            DbValue::from(self.env.as_str()),
            DbValue::from(command.bot_id.as_str()),
            DbValue::from(command.actor_user_id.as_str()),
            DbValue::from(command.to_user_id.as_str()),
            DbValue::from(command.expected_owner_version as i64),
            DbValue::from(command.client_request_id.as_str()),
            DbValue::from(bot_name_snapshot),
            // tb: live, initialized Bot row.
            DbValue::from(command.bot_id.as_str()),
            DbValue::from(self.env.as_str()),
            // os: the unique approved owner slot exists.
            DbValue::from(self.env.as_str()),
            DbValue::from(command.bot_id.as_str()),
            // ao: the ACTOR is that current owner.
            DbValue::from(self.env.as_str()),
            DbValue::from(command.bot_id.as_str()),
            DbValue::from(actor_edge.as_str()),
            // Exact version snapshot.
            DbValue::from(command.bot_id.as_str()),
            DbValue::from(self.env.as_str()),
            DbValue::from(command.expected_owner_version as i64),
            // rh: the recipient is a live same-env Human.
            DbValue::from(recipient_edge.as_str()),
            DbValue::from(self.env.as_str()),
            // Slot free (after the cleanup statements in this transaction).
            DbValue::from(self.env.as_str()),
            DbValue::from(command.bot_id.as_str()),
            // No self-transfer.
            DbValue::from(command.to_user_id.as_str()),
            DbValue::from(command.actor_user_id.as_str()),
        ];
        bcs_db_api::DbStatement::with_params(
            &format!(
                "INSERT INTO bot_ownership_transfers \
                     (transfer_id, env, bot_id, from_user_id, to_user_id, \
                      expected_owner_version, client_request_id, status, expires_at, \
                      bot_name_snapshot) \
                 SELECT ?, ?, ?, ?, ?, ?, ?, 'pending', {}, ?{dual} \
                 WHERE EXISTS (SELECT 1 FROM bcs_bots tb \
                       WHERE tb.bot_uuid = ? AND tb.env = ? AND tb.ownership_version > 0 \
                         AND COALESCE(tb.is_deleted, 0) = 0) \
                   AND EXISTS (SELECT 1 FROM edge_grants os \
                       WHERE os.env = ? AND os.to_id = ? AND os.grant_kind = 'owner' \
                         AND os.status = 'approved') \
                   AND EXISTS (SELECT 1 FROM edge_grants ao \
                       WHERE ao.env = ? AND ao.to_id = ? AND ao.from_id = ? \
                         AND ao.grant_kind = 'owner' AND ao.status = 'approved') \
                   AND (SELECT nb.ownership_version FROM bcs_bots nb \
                        WHERE nb.bot_uuid = ? AND nb.env = ?) = ? \
                   AND EXISTS (SELECT 1 FROM bcs_bots rh \
                       WHERE rh.bot_uuid = ? AND rh.env = ? AND rh.actor_kind = 'human' \
                         AND COALESCE(rh.is_deleted, 0) = 0) \
                   AND NOT EXISTS (SELECT 1 FROM bot_ownership_transfers t2 \
                       WHERE t2.env = ? AND t2.bot_id = ? AND t2.status = 'pending') \
                   AND ? <> ?",
                expires_plus_7_days(&self.flavor),
                dual = from_dual(&self.flavor),
            ),
            params,
        )
    }

    /// The committed receipt re-read (fresh creation; index-driven by the
    /// unique transfer id).
    fn receipt_statement(&self, transfer_id: &str) -> bcs_db_api::DbStatement {
        bcs_db_api::DbStatement::with_params(
            &format!(
                "SELECT {TRANSFER_ROW_COLUMNS}, {} FROM bot_ownership_transfers \
                 WHERE env = ? AND transfer_id = ? LIMIT 1",
                db_now_expr(&self.flavor)
            ),
            vec![
                DbValue::from(self.env.as_str()),
                DbValue::from(transfer_id),
            ],
        )
    }
}

/// Whether the pending row's deadline already lapsed at the read's own
/// database clock (`db_now >= expires_at`, boundary inclusive per spec
/// §9.1). Preferred as an epoch comparison through the shared timestamp
/// parser; the TEXT timestamps share the 'YYYY-MM-DD HH:MM:SS' shape in
/// both dialects, so the lexicographic fallback is chronologically
/// equivalent.
fn lapsed(expires_at: &str, db_now: &str) -> bool {
    match (
        parse_timestamp_epoch_ms(expires_at),
        parse_timestamp_epoch_ms(db_now),
    ) {
        (Some(expires_ms), Some(now_ms)) => now_ms >= expires_ms,
        _ => !expires_at.is_empty() && !db_now.is_empty() && db_now >= expires_at,
    }
}

/// Structural fail-closed of the create command (blank identity strings
/// never reach the SQL/matching layer).
fn fail_closed_create(command: &CreateOwnershipTransfer) -> ServiceResult<()> {
    if command.actor_user_id.trim().is_empty()
        || command.bot_id.trim().is_empty()
        || command.to_user_id.trim().is_empty()
        || command.client_request_id.trim().is_empty()
    {
        return Err(ServiceError::Authority(AuthorityError::InvalidSubject(
            "ownership transfer create requires non-blank actor/bot/recipient \
             and a non-blank idempotency key"
                .to_string(),
        )));
    }
    Ok(())
}

fn step_rows<'a>(
    results: &'a [DbTransactionStepResult],
    index: usize,
    context: &'static str,
) -> ServiceResult<&'a [DbRow]> {
    match results.get(index) {
        Some(DbTransactionStepResult::Rows(rows)) => Ok(rows),
        _ => Err(ServiceError::InternalError(format!(
            "authority transfer create: expected the {context} query at step {index}"
        ))),
    }
}

fn read_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    row.get_i64(column)
        .ok()
        .flatten()
        .map(|value| value.max(0) as u64)
        .ok_or_else(|| {
            ServiceError::InternalError(format!(
                "authority transfer create: aggregate column '{column}' missing or NULL"
            ))
        })
}