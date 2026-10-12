//! Ordinary business-audit lane for the managed message-delivery store
//! (plan Task 12, spec §12.5).
//!
//! The delivery admission is a durable command whose external I/O (the
//! actual bot send) follows later: the store therefore commits the admitted
//! identity snapshot — `send/message/admitted` — in the SAME DbPlugin
//! transaction as the canonical message + delivery rows (memory twin: the
//! same critical section that publishes the staged state, so an audit
//! failure aborts the whole staged admission).
//!
//! The slot semantics are the shared Task 1 contract: deterministic
//! `audit_id`, `(env, operation_id, step_key)` unique, byte-identical
//! retries are idempotent, divergent same-slot content is a conflict.

use bcs_db_api::{DbStatement, DbValue};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, stable_step_key,
};

/// Deterministic audit id of one (env, operation, step) slot.
pub(crate) fn delivery_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("message-action:{env}:{operation_id}:{step_key}")
}

/// The `send/message/admitted` record of one admission: the operator and
/// effective actor project ONLY from the REQUIRED [`BotOperationContext`].
pub(crate) fn admission_audit_record(
    operation: &BotOperationContext,
    env: &str,
    message_id: &str,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(
        BotActionKind::Send,
        BotActionResourceKind::Message,
        BotActionAuditPhase::Admitted,
    );
    BotActionAuditRecord::new(
        delivery_action_audit_id(env, &operation.operation_id, &step_key),
        env,
        operation.operation_id.clone(),
        operation.actor.clone(),
        BotActionResourceKind::Message,
        message_id,
        BotActionKind::Send,
        BotActionAuditPhase::Admitted,
        None,
    )
}

/// The `INTO bcs_bot_action_audits` INSERT of one audit record.
pub(crate) fn action_audit_insert(record: &BotActionAuditRecord) -> DbStatement {
    DbStatement::with_params(
        "INSERT INTO bcs_bot_action_audits \
           (audit_id, env, operation_id, step_key, operator_kind, operator_id, \
            operator_user_id, effective_actor_id, resource_kind, resource_id, \
            action, phase, reason_code) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from(record.audit_id.as_str()),
            DbValue::from(record.env.as_str()),
            DbValue::from(record.operation_id.as_str()),
            DbValue::from(record.step_key.as_str()),
            DbValue::from(record.operator.operator_kind()),
            DbValue::from(record.operator.operator_id()),
            record
                .operator
                .operator_user_id()
                .map(DbValue::from)
                .unwrap_or(DbValue::Null),
            DbValue::from(record.operator.effective_actor_id()),
            DbValue::from(record.resource_kind.as_str()),
            DbValue::from(record.resource_id.as_str()),
            DbValue::from(record.action.as_str()),
            DbValue::from(record.phase.as_str()),
            record
                .reason_code
                .as_deref()
                .map(DbValue::from)
                .unwrap_or(DbValue::Null),
        ],
    )
}
/// The `abort/message/admitted` record of one externally initiated abort:
/// the durable cancellation-intent row (with `abort_request_id` and the
/// given per-delivery operation id) and this record commit together,
/// BEFORE the external abort I/O runs.
pub(crate) fn abort_admitted_audit_record(
    operation: &BotOperationContext,
    env: &str,
    delivery_id: &str,
) -> BotActionAuditRecord {
    BotActionAuditRecord::new(
        delivery_action_audit_id(
            env,
            &operation.operation_id,
            "abort/message/admitted",
        ),
        env,
        operation.operation_id.clone(),
        operation.actor.clone(),
        BotActionResourceKind::Message,
        delivery_id,
        BotActionKind::Abort,
        BotActionAuditPhase::Admitted,
        None,
    )
}

/// The `abort/message/applied` record of a control transition that mutates
/// ONLY durable rows (queued-message cancel, manual not-sent/stopped
/// resolution): the record commits with the row update in the same
/// transaction, no external I/O follows from it.
pub(crate) fn control_applied_audit_record(
    operation: &BotOperationContext,
    env: &str,
    delivery_id: &str,
) -> BotActionAuditRecord {
    BotActionAuditRecord::new(
        delivery_action_audit_id(
            env,
            &operation.operation_id,
            "abort/message/applied",
        ),
        env,
        operation.operation_id.clone(),
        operation.actor.clone(),
        BotActionResourceKind::Message,
        delivery_id,
        BotActionKind::Abort,
        BotActionAuditPhase::Applied,
        None,
    )
}

/// §12.5 record built from one control-audit carrier (plan Task 12 fix
/// round): the store owns the env and the channel derives step keys from
/// the controlled vocabulary, so the carrier never carries env text.
pub(crate) fn control_audit_record(
    carrier: &bcs_service_api::port::repo::message_delivery::DeliveryControlAudit,
    env: &str,
    action: BotActionKind,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(action, BotActionResourceKind::Message, carrier.phase);
    BotActionAuditRecord::new(
        delivery_action_audit_id(env, &carrier.operation.operation_id, &step_key),
        env,
        carrier.operation.operation_id.clone(),
        carrier.operation.actor.clone(),
        BotActionResourceKind::Message,
        carrier.resource_id.clone(),
        action,
        carrier.phase,
        None,
    )
}

#[cfg(test)]
/// Regression (PR #2568 round 2: MySQL "ERROR 22001 (1406): Data too long
/// for column 'audit_id'"): the composed lane audit id must fit the
/// migration-032/033 `audit_id` budget at every worst legal input — an
/// operation id at its own column cap and the longest step-key vocabulary
/// value, per every writer pen (message/group/session/file/chat-run/edge).
mod audit_width_conformance {
    use super::delivery_action_audit_id;
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
            let id = delivery_action_audit_id(
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

