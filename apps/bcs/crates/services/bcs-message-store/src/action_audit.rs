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