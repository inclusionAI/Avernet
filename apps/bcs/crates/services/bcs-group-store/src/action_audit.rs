//! Ordinary business audit lane for the Group store (plan Task 10,
//! spec §12.5).
//!
//! The group store owns its own `bcs_bot_action_audits` writes: the eventful
//! Group mutation (business state + its existing Event) and the
//! `<action>/group/applied` audit row commit in ONE DbPlugin transaction (the
//! memory twin publishes all three under one critical section), so an audit
//! INSERT failure rolls the business change AND its Event back — there is no
//! shared "audit service" here, and the pure record types come verbatim from
//! Task 1 (`bcs_service_api::types::bot_operation`).
//!
//! Slot semantics (spec §12.5 `(env, operation_id, step_key)` unique): the
//! record for one operation is a pure function of
//! `(env, operation, resource)` — including a DETERMINISTIC `audit_id` — so a
//! legitimate replay of the same operation produces a byte-identical record
//! and keeps the first committed row (DB timestamps are not part of the
//! record). The same slot carrying different content is a conflict and must
//! be rejected, never overwritten.
//!
//! The SQL text keeps the `INTO bcs_bot_action_audits` fragment stable so the
//! failure-injection harness can target exactly this INSERT.

use bcs_db_api::{DbStatement, DbValue as Value};
use bcs_service_api::port::repo::{CommitGroupEventfulMutation, GroupEventfulMutation};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, ServiceError, ServiceResult, stable_step_key,
};

/// Deterministic audit id for one (env, operation, step) slot: retries of the
/// same operation replay the same id, so a retried record is byte-identical
/// to the first committed row (its DB timestamps stay put).
pub(crate) fn group_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("group-action:{env}:{operation_id}:{step_key}")
}

/// Controlled action vocabulary for one eventful Group mutation.
pub(crate) fn eventful_mutation_action(mutation: &GroupEventfulMutation) -> BotActionKind {
    match mutation {
        GroupEventfulMutation::Delete => BotActionKind::Delete,
        _ => BotActionKind::Update,
    }
}

/// Build the `<action>/group/applied` audit record of one eventful Group
/// mutation from the typed operation context (spec §12.5): operator columns
/// project ONLY from [`bcs_service_api::types::BotOperationActor`] — never
/// from a transport request body — and Human/Bot-only are two distinct
/// legitimate branches (a `None` operator user id on the Bot/System branch
/// means "no Human was involved").
pub(crate) fn eventful_mutation_audit_record(
    command: &CommitGroupEventfulMutation,
    operation: &BotOperationContext,
    env: &str,
) -> BotActionAuditRecord {
    let action = eventful_mutation_action(&command.mutation);
    let step_key = stable_step_key(action, BotActionResourceKind::Group, BotActionAuditPhase::Applied);
    BotActionAuditRecord {
        audit_id: group_action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Group,
        resource_id: command.group_id.clone(),
        action,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

/// Build the `update/workspace/applied` audit record of one workspace
/// replacement.
pub(crate) fn workspace_audit_record(
    group_id: &str,
    operation: &BotOperationContext,
    env: &str,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(
        BotActionKind::Update,
        BotActionResourceKind::Workspace,
        BotActionAuditPhase::Applied,
    );
    BotActionAuditRecord {
        audit_id: group_action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Workspace,
        resource_id: group_id.to_string(),
        action: BotActionKind::Update,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

/// The `INTO bcs_bot_action_audits` INSERT of one audit record (see the
/// module header for the stable fragment contract used by the failure
/// injection harness).
pub(crate) fn group_action_audit_insert(record: &BotActionAuditRecord) -> DbStatement {
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
/// content as `record` (the DB timestamp columns are not part of the record
/// by construction; `audit_id` is deterministic, so an identical retry is
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
                        "group action audit column '{name}' is unexpectedly NULL"
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

/// Classify a failed eventful-mutation transaction against the audit slot
/// (spec §12.5): when the slot already carries the byte-identical record — a
/// replay of an operation whose earlier attempt FULLY committed (business
/// write + Event + audit) — the caller may re-apply the business mutation
/// WITHOUT the audit step (idempotent completion); a same-slot row with
/// DIFFERENT content is a conflict; no slot row means the failure was genuine
/// (the audit INSERT never happened — e.g. an injected failing step — and
/// the whole transaction rolled back).
#[must_use]
pub(crate) fn audit_slot_retry_classified(
    record: &BotActionAuditRecord,
    rows: Vec<bcs_db_api::DbRow>,
    transaction_error: &str,
) -> ServiceResult<()> {
    match rows.first() {
        Some(row) if action_audit_row_matches(record, row)? => Ok(()),
        Some(_) => Err(ServiceError::Conflict(format!(
            "group action audit slot '{}' already carries different content",
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
/// Regression (PR #2568 round 2: MySQL "ERROR 22001 (1406): Data too long
/// for column 'audit_id'"): the composed lane audit id must fit the
/// migration-032/033 `audit_id` budget at every worst legal input — an
/// operation id at its own column cap and the longest step-key vocabulary
/// value, per every writer pen (message/group/session/file/chat-run/edge).
mod audit_width_conformance {
    use super::group_action_audit_id;
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
            let id = group_action_audit_id(
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

