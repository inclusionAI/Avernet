//! Ordinary business audit lane for the Session-file store (plan Task 11,
//! spec §12.5).
//!
//! The file store owns its own `bcs_bot_action_audits` writes. Two commit
//! classes share the table:
//!
//! 1. DB-transaction class — the metadata mutation (INSERT on prepare,
//!    status/handle UPDATE on complete/sweep, the final DELETE) commits its
//!    `applied` (or `completed` for the post-I/O delete) audit row in the
//!    SAME DbPlugin transaction as the business write. An audit INSERT
//!    failure rolls the metadata mutation back — there is never "business
//!    first, audit later" for a successful metadata change.
//!
//! 2. External-effect class — the file service persists `admitted` via
//!    [`SessionFileRepoPort::record_operation_phase`] BEFORE any backend
//!    I/O (delete/abort/share), then the outcome row: `completed` joins the
//!    final metadata DELETE transaction on backend success, an explicit
//!    backend failure persists `failed`, and an indeterminate result
//!    persists `unknown`. `admitted` is NOT proof the external effect
//!    happened, and an `unknown` row is never auto-replayed by audit key.
//!
//! Slot semantics (spec §12.5 `(env, operation_id, step_key)` unique): the
//! record is a pure function of `(env, operation, resource)` — including a
//! DETERMINISTIC `audit_id` — so a legitimate replay of the same operation
//! produces a byte-identical record and keeps the first committed row. The
//! same slot carrying different content is a conflict, never an overwrite.
//!
//! The SQL text keeps the `INTO bcs_bot_action_audits` fragment stable so the
//! failure-injection harness can target exactly this INSERT.

use bcs_db_api::{DbStatement, DbValue as Value};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, ServiceError, ServiceResult, stable_step_key,
};

/// Deterministic audit id for one (env, operation, step) slot.
pub(crate) fn file_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("file-action:{env}:{operation_id}:{step_key}")
}

/// Build one audit record for a file operation step from the typed operation
/// context (spec §12.5): operator columns project ONLY from
/// [`bcs_service_api::types::BotOperationActor`] — never from a transport
/// request body — and Human/Bot-only are two distinct legitimate branches.
fn file_audit_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
    action: BotActionKind,
    phase: BotActionAuditPhase,
    reason_code: Option<String>,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(action, BotActionResourceKind::SessionFile, phase);
    BotActionAuditRecord {
        audit_id: file_action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::SessionFile,
        resource_id: file_id.to_string(),
        action,
        phase,
        reason_code,
    }
}

/// `create/session_file/applied` — metadata INSERT on prepare_upload.
pub(crate) fn create_file_audit_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
) -> BotActionAuditRecord {
    file_audit_record(operation, env, file_id, BotActionKind::Create, BotActionAuditPhase::Applied, None)
}

/// `update/session_file/applied` — status/handle metadata change.
pub(crate) fn update_file_audit_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
) -> BotActionAuditRecord {
    file_audit_record(operation, env, file_id, BotActionKind::Update, BotActionAuditPhase::Applied, None)
}

/// `delete/session_file/<phase>` — the delete flow's phase records
/// (`admitted` before backend I/O, `completed` with the final metadata
/// DELETE, `failed` on explicit backend failure).
pub(crate) fn delete_file_audit_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
    phase: BotActionAuditPhase,
    reason_code: Option<String>,
) -> BotActionAuditRecord {
    file_audit_record(operation, env, file_id, BotActionKind::Delete, phase, reason_code)
}

/// `share/session_file/<phase>` — share-mint phase records (`admitted`
/// before minting, `completed` on success, `failed` otherwise). Share has
/// no metadata change, so both rows are standalone phase records.
pub(crate) fn share_file_audit_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
    phase: BotActionAuditPhase,
    reason_code: Option<String>,
) -> BotActionAuditRecord {
    file_audit_record(operation, env, file_id, BotActionKind::Share, phase, reason_code)
}

/// The `INTO bcs_bot_action_audits` INSERT of one audit record (see the
/// module header for the stable fragment contract used by the failure
/// injection harness).
pub(crate) fn file_action_audit_insert(record: &BotActionAuditRecord) -> DbStatement {
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

/// Whether one persisted audit row carries the SAME application-authored
/// content as `record` (DB timestamp columns are not part of the record by
/// construction; `audit_id` is deterministic, so an identical retry is
/// byte-identical to the first committed row).
pub(crate) fn action_audit_row_matches(
    record: &BotActionAuditRecord,
    row: &bcs_db_api::DbRow,
) -> ServiceResult<bool> {
    let column = |name: &str| -> ServiceResult<String> {
        row.get_string(name)
            .map_err(|error| ServiceError::InternalError(error.to_string()))
            .and_then(|value| {
                value.ok_or_else(|| {
                    ServiceError::InternalError(format!(
                        "file action audit column '{name}' is unexpectedly NULL"
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

/// Classify a same-slot situation after a failed audit INSERT (spec §12.5):
/// a slot already carrying the byte-identical record is an idempotent replay
/// of a previously committed row; different content under the slot is a
/// conflict; no slot row means the failure was genuine (the row never
/// committed, and any co-transacted business write rolled back).
pub(crate) fn audit_slot_retry_classified(
    record: &BotActionAuditRecord,
    rows: Vec<bcs_db_api::DbRow>,
    transaction_error: &str,
) -> ServiceResult<()> {
    match rows.first() {
        Some(row) if action_audit_row_matches(record, row)? => Ok(()),
        Some(_) => Err(ServiceError::Conflict(format!(
            "file action audit slot '{}' already carries different content",
            record.step_key
        ))),
        None => Err(ServiceError::InternalError(transaction_error.to_string())),
    }
}

/// The audit-slot probe SELECT used by the failure classifier.
pub(crate) fn audit_slot_select(record: &BotActionAuditRecord) -> DbStatement {
    DbStatement::with_params(
        "SELECT audit_id, operator_kind, operator_id, operator_user_id, \
                effective_actor_id, resource_kind, resource_id, action, phase, reason_code \
         FROM bcs_bot_action_audits \
         WHERE env = ? AND operation_id = ? AND step_key = ?",
        vec![
            Value::from(record.env.as_str()),
            Value::from(record.operation_id.as_str()),
            Value::from(record.step_key.as_str()),
        ],
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delete_phase_records_use_distinct_step_keys() {
        let operation = BotOperationContext {
            operation_id: "file-operation-1".into(),
            actor: bcs_service_api::types::BotOperationActor::Human {
                user_id: "a".into(),
                effective_actor_id: "bot-x".into(),
            },
        };
        let admitted =
            delete_file_audit_record(&operation, "contract", "file-1", BotActionAuditPhase::Admitted, None);
        let completed = delete_file_audit_record(
            &operation,
            "contract",
            "file-1",
            BotActionAuditPhase::Completed,
            None,
        );
        assert_eq!(admitted.step_key, "delete/session_file/admitted");
        assert_eq!(completed.step_key, "delete/session_file/completed");
        assert_ne!(admitted.step_key, completed.step_key);
        assert_eq!(admitted.operator.operator_user_id(), Some("a"));
        assert_eq!(admitted.operator.effective_actor_id(), "bot-x");
    }
}

#[cfg(test)]
/// Regression (PR #2568 round 2: MySQL "ERROR 22001 (1406): Data too long
/// for column 'audit_id'"): the composed lane audit id must fit the
/// migration-032/033 `audit_id` budget at every worst legal input — an
/// operation id at its own column cap and the longest step-key vocabulary
/// value, per every writer pen (message/group/session/file/chat-run/edge).
mod audit_width_conformance {
    use super::file_action_audit_id;
    use bcs_service_api::types::*;

    #[test]
    fn audit_id_fits_the_durable_column_budget_at_worst_inputs() {
        let step = stable_step_key(
            BotActionKind::Delete,
            BotActionResourceKind::SessionFile,
            BotActionAuditPhase::Completed,
        );
        assert_eq!(step, "delete/session_file/completed");
        for env in ["local", "dev", "pre", "gray", "prod"] {
            let id = file_action_audit_id(
                env,
                &"o".repeat(AUTHORITY_OPERATION_ID_VARCHAR_WIDTH),
                &step,
            );
            assert!(
                id.len() <= AUTHORITY_AUDIT_ID_VARCHAR_WIDTH,
                "lane audit_id length {} exceeds the durable {AUTHORITY_AUDIT_ID_VARCHAR_WIDTH}-char budget",
                id.len()
            );
        }
    }
}

