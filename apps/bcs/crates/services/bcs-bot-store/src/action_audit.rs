//! Ordinary business audit lane for the Bot control-plane store
//! (plan Task 9, spec §12.5).
//!
//! The bot store owns its own `bcs_bot_action_audits` writes: the actual
//! control-plane UPDATE and its `update/bot/applied` audit row commit in
//! ONE DbPlugin transaction (the memory twin publishes both under one
//! critical section), so an audit INSERT failure rolls the business
//! change back — there is no shared "audit service" here, and the pure
//! record types come verbatim from Task 1
//! (`bcs_service_api::types::bot_operation`).
//!
//! Slot semantics (spec §12.5 `(env, operation_id, step_key)` unique):
//! the record for one operation is a pure function of
//! `(env, operation, resource)` — including a DETERMINISTIC `audit_id`
//! — so a legitimate retry of the same operation produces the byte-
//! identical record: the first committed row (with its DB-generated
//! timestamps, which are not part of the record) is kept and the retry
//! is an idempotent no-op. The same slot carrying different content is
//! a conflict and must be rejected, never overwritten.
//!
//! The SQL text keeps the `INTO bcs_bot_action_audits` fragment stable
//! so the Task 5 transaction-step failure-injection harness can target
//! exactly this INSERT.

use bcs_db_api::DbStatement;
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, ServiceError, ServiceResult, stable_step_key,
};

use super::{MemoryBotRepo, PersistentBotRepo, Value};
use crate::memory::memory_authority::MemoryAuthorityState;

/// Deterministic audit id for one (env, operation, step) slot: retries of
/// the same operation replay the same id, so a retried record is byte-
/// identical to the first committed row (its DB timestamps stay put).
pub(crate) fn action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("bot-action:{env}:{operation_id}:{step_key}")
}

/// Build the `update/bot/applied` audit record of one control-plane
/// patch from the typed operation context (spec §12.5): operator columns
/// project ONLY from [`bcs_service_api::types::BotOperationActor`] —
/// never from a transport request body — and Human/Bot-only are two
/// distinct legitimate branches (a `None` operator user id on the Bot
/// branch means "no Human was involved").
pub(crate) fn patch_audit_record(
    operation: &BotOperationContext,
    env: &str,
    bot_id: &str,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(
        BotActionKind::Update,
        BotActionResourceKind::Bot,
        BotActionAuditPhase::Applied,
    );
    BotActionAuditRecord {
        audit_id: action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Bot,
        resource_id: bot_id.to_string(),
        action: BotActionKind::Update,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

/// The `INTO bcs_bot_action_audits` INSERT of one audit record (see the
/// module header for the stable fragment contract used by the failure
/// injection harness).
pub(crate) fn action_audit_insert(record: &BotActionAuditRecord) -> DbStatement {
    DbStatement::with_params(
        "INSERT INTO bcs_bot_action_audits \
           (audit_id, env, operation_id, step_key, operator_kind, operator_id, \
            operator_user_id, effective_actor_id, resource_kind, resource_id, \
            action, phase, reason_code) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            Value::from(record.audit_id.as_str()),
            Value::from(record.env.as_str()),
            Value::from(record.operation_id.as_str()),
            Value::from(record.step_key.as_str()),
            Value::from(record.operator.operator_kind()),
            Value::from(record.operator.operator_id()),
            record
                .operator
                .operator_user_id()
                .map(Value::from)
                .unwrap_or(Value::Null),
            Value::from(record.operator.effective_actor_id()),
            Value::from(record.resource_kind.as_str()),
            Value::from(record.resource_id.as_str()),
            Value::from(record.action.as_str()),
            Value::from(record.phase.as_str()),
            record
                .reason_code
                .as_deref()
                .map(Value::from)
                .unwrap_or(Value::Null),
        ],
    )
}

impl MemoryAuthorityState {
    /// The memory twin of the audit INSERT: append one audit record under
    /// the SAME critical section that publishes the business mutation,
    /// with the SQL slot semantics — a same-slot record with identical
    /// content is an idempotent no-op (the FIRST row stays), different
    /// content under the same `(env, operation_id, step_key)` slot is a
    /// conflict that aborts the whole mutation ("失败丢弃暂存状态").
    pub(crate) fn append_action_audit_once(&mut self, record: &BotActionAuditRecord) -> ServiceResult<()> {
        if let Some(existing) = self
            .action_audit_records
            .iter()
            .find(|existing| existing.same_slot(record))
        {
            if existing.content_conflicts(record) {
                return Err(ServiceError::Conflict(format!(
                    "bot action audit slot {} already carries different content",
                    record.step_key
                )));
            }
            return Ok(());
        }
        self.action_audit_records.push(record.clone());
        Ok(())
    }
}

impl MemoryBotRepo {
    /// Read the memory twin's published bot action audit rows (the
    /// in-memory counterpart of querying `bcs_bot_action_audits`).
    /// TEST/diagnostic OBSERVATION lever — the audit table is not a
    /// permission fact source and no public query API is added.
    pub async fn bot_action_audit_records(
        &self,
    ) -> ServiceResult<Vec<BotActionAuditRecord>> {
        let authority = self.authority.read().await;
        Ok(authority.action_audit_records.clone())
    }
}

/// Whether one persisted audit row carries the SAME application-authored
/// content as `record` (the DB timestamp columns are not part of the
/// record by construction; `audit_id` is deterministic, so an identical
/// retry is byte-identical to the first committed row).
fn action_audit_row_matches(
    record: &BotActionAuditRecord,
    row: &bcs_db_api::DbRow,
) -> ServiceResult<bool> {
    let column = |name: &str| -> ServiceResult<String> {
        row.get_string(name)
            .map_err(|error| ServiceError::InternalError(error.to_string()))
            .and_then(|value| {
                value.ok_or_else(|| {
                    ServiceError::InternalError(format!(
                        "bot action audit column '{name}' is unexpectedly NULL"
                    ))
                })
            })
    };
    let optional_column = |name: &str| -> ServiceResult<Option<String>> {
        row.get_string(name)
            .map_err(|error| ServiceError::InternalError(error.to_string()))
    };
    let stored_operator_user_id = optional_column("operator_user_id")?;
    let stored_reason_code = optional_column("reason_code")?;
    Ok(column("audit_id")? == record.audit_id
        && column("operator_kind")? == record.operator.operator_kind()
        && column("operator_id")? == record.operator.operator_id()
        && stored_operator_user_id.as_deref() == record.operator.operator_user_id()
        && column("effective_actor_id")? == record.operator.effective_actor_id()
        && column("resource_kind")? == record.resource_kind.as_str()
        && column("resource_id")? == record.resource_id
        && column("action")? == record.action.as_str()
        && column("phase")? == record.phase.as_str()
        && stored_reason_code.as_deref() == record.reason_code.as_deref())
}

impl PersistentBotRepo {
    /// Classify a failed patch transaction against the audit slot and, in
    /// the single legitimate case, complete the operation: when the slot
    /// already carries the byte-identical record — a replay of an
    /// operation whose earlier attempt FULLY committed (UPDATE + audit) —
    /// the first audit row (with its DB timestamps) stays and only the
    /// business UPDATE is re-applied. A same-slot row with DIFFERENT
    /// content is a conflict; no slot row means the failure was genuine
    /// (the audit INSERT never happened — e.g. an injected failing step —
    /// and the business UPDATE rolled back with the transaction).
    pub(super) async fn retry_identical_patch_if_slot_matches(
        &self,
        transaction_error: &str,
        record: &BotActionAuditRecord,
        update_sql: &str,
        update_params: Vec<Value>,
    ) -> ServiceResult<()> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT audit_id, operator_kind, operator_id, operator_user_id, \
                        effective_actor_id, resource_kind, resource_id, action, phase, \
                        reason_code \
                 FROM bcs_bot_action_audits \
                 WHERE env = ? AND operation_id = ? AND step_key = ?",
                vec![
                    Value::from(record.env.as_str()),
                    Value::from(record.operation_id.as_str()),
                    Value::from(record.step_key.as_str()),
                ],
            ))
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        match rows.first() {
            Some(row) if action_audit_row_matches(record, row)? => {
                self.db
                    .execute(DbStatement::with_params(update_sql, update_params))
                    .await
                    .map(|_| ())
                    .map_err(|error| ServiceError::InternalError(error.to_string()))
            }
            Some(_) => Err(ServiceError::Conflict(format!(
                "bot action audit slot '{}' already carries different content",
                record.step_key
            ))),
            None => Err(ServiceError::InternalError(transaction_error.to_string())),
        }
    }
}