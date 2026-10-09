//! Ordinary-business audit record builders for the session-file service
//! (plan Task 11, spec §12.5).
//!
//! These builders ONLY construct the Task-1 pure records that bracket the
//! service's external backend I/O (`admitted` before side effects;
//! `completed` AFTER the metadata transaction that carries it — no, that one
//! is committed by the store; `failed`/`unknown` on explicit or
//! indeterminate outcomes). The records persist through
//! [`bcs_service_api::port::repo::SessionFileRepoPort::record_operation_phase`],
//! which is the ONLY audit lane outside the store's atomic mutations.
//!
//! `audit_id` is a deterministic function of `(env, operation_id, step_key)`,
//! so an identical retry of one logical step is byte-identical to the first
//! committed row and the store keeps the first content.

use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, stable_step_key,
};

/// Deterministic audit id for one (env, operation, step) slot.
fn file_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("file-action:{env}:{operation_id}:{step_key}")
}

/// Build one session-file phase record for the delete flow
/// (`delete/session_file/admitted|completed|failed|unknown`).
pub(crate) fn delete_phase_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
    phase: BotActionAuditPhase,
    reason_code: Option<&str>,
) -> BotActionAuditRecord {
    phase_record(
        operation,
        env,
        file_id,
        BotActionKind::Delete,
        phase,
        reason_code,
    )
}

/// Build one session-file phase record for the share-mint flow
/// (`share/session_file/admitted|completed|failed|unknown`). Share has no
/// metadata change, so both real phases persist through the standalone
/// recorder.
pub(crate) fn share_phase_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
    phase: BotActionAuditPhase,
    reason_code: Option<&str>,
) -> BotActionAuditRecord {
    phase_record(
        operation,
        env,
        file_id,
        BotActionKind::Share,
        phase,
        reason_code,
    )
}

fn phase_record(
    operation: &BotOperationContext,
    env: &str,
    file_id: &str,
    action: BotActionKind,
    phase: BotActionAuditPhase,
    reason_code: Option<&str>,
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
        reason_code: reason_code.map(|reason| reason.to_string()),
    }
}

/// A new operation context derived from a parent one, scoped to ONE resource
/// row. Bulk system lanes (pending sweep, session-delete cleanup) interleave
/// per-row steps that share step keys but target different `resource_id`s —
/// the `(env, operation_id, step_key)` slot must never carry two different
/// contents (spec §12.5), so each row gets its own deterministic
/// sub-operation-id.
pub(crate) fn per_resource_operation(
    parent: &BotOperationContext,
    resource_id: &str,
) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("{}:{}", parent.operation_id, resource_id),
        actor: parent.actor.clone(),
    }
}

/// The independent operation of a background runtime cleanup (spec §12.5):
/// the sweep is an honest System action — it records ITSELF as the operator
/// and never forges or replays a historical Human identity.
pub(crate) fn sweep_operation(row_file_id: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("session-file-pending-sweep:{row_file_id}"),
        actor: bcs_service_api::types::BotOperationActor::System {
            system_id: "session-file-pending-sweep".to_string(),
            effective_actor_id: "session-file-pending-sweep".to_string(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn per_resource_operation_keeps_operator_and_splits_slot() {
        let parent = BotOperationContext {
            operation_id: "bulk-clean-1".into(),
            actor: bcs_service_api::types::BotOperationActor::System {
                system_id: "runtime-cleanup".into(),
                effective_actor_id: "runtime-cleanup".into(),
            },
        };
        let for_a = per_resource_operation(&parent, "file-a");
        let for_b = per_resource_operation(&parent, "file-b");
        assert_eq!(for_a.operation_id, "bulk-clean-1:file-a");
        assert_eq!(for_b.operation_id, "bulk-clean-1:file-b");
        assert_eq!(for_a.actor, parent.actor);
    }

    #[test]
    fn delete_and_share_records_use_distinct_stable_step_keys() {
        let operation = BotOperationContext {
            operation_id: "op-1".into(),
            actor: bcs_service_api::types::BotOperationActor::Bot {
                bot_id: "bot-x".into(),
            },
        };
        let admitted = delete_phase_record(&operation, "test", "f-1", BotActionAuditPhase::Admitted, None);
        assert_eq!(admitted.step_key, "delete/session_file/admitted");
        assert_eq!(admitted.operator.operator_user_id(), None);
        let completed = share_phase_record(
            &operation,
            "test",
            "f-1",
            BotActionAuditPhase::Completed,
            None,
        );
        assert_eq!(completed.step_key, "share/session_file/completed");
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

