//! Ordinary business-audit lane for the direct-chat run store
//! (plan Task 12, spec §12.5).
//!
//! Creating a run is a durable command whose external I/O (the HTTP/WS send
//! to the bot) follows: the store therefore commits the `launch/message/
//! admitted` identity snapshot in the SAME DbPlugin transaction as the
//! `bcs_chat_runs` row (memory twin: the same critical section). New
//! commands MUST carry their operation context — a missing one is rejected,
//! never downgraded to a forged System actor. Pre-cutover history rows read
//! back with `operation_id = NULL` and stay legal on reads.

use bcs_db_api::{DbStatement, DbValue};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, stable_step_key,
};

/// Deterministic audit id of one (env, operation, step) slot.
pub(crate) fn chat_run_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("chat-run-action:{env}:{operation_id}:{step_key}")
}

/// The `launch/message/admitted` record of one run creation.
pub(crate) fn run_creation_audit_record(
    operation: &BotOperationContext,
    env: &str,
    run_id: &str,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(
        BotActionKind::Launch,
        BotActionResourceKind::Message,
        BotActionAuditPhase::Admitted,
    );
    BotActionAuditRecord::new(
        chat_run_action_audit_id(env, &operation.operation_id, &step_key),
        env,
        operation.operation_id.clone(),
        operation.actor.clone(),
        BotActionResourceKind::Message,
        run_id,
        BotActionKind::Launch,
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

#[cfg(test)]
/// Regression (PR #2568 round 2: MySQL "ERROR 22001 (1406): Data too long
/// for column 'audit_id'"): the composed lane audit id must fit the
/// migration-032/033 `audit_id` budget at every worst legal input — an
/// operation id at its own column cap and the longest step-key vocabulary
/// value, per every writer pen (message/group/session/file/chat-run/edge).
mod audit_width_conformance {
    use super::chat_run_action_audit_id;
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
            let id = chat_run_action_audit_id(
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

