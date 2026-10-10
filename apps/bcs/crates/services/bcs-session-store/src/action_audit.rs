//! Ordinary business audit lane for the Session store (plan Task 11,
//! spec §12.5).
//!
//! The session store owns its own `bcs_bot_action_audits` writes: every
//! audited Session mutation (creation, collect/uncollect favorites, eventful
//! participant mutations and eventful completion) commits its `applied` audit
//! row in the SAME DbPlugin transaction as the business write (the memory
//! twin publishes state and audit together under one critical section), so an
//! audit INSERT failure rolls the business change back — there is no shared
//! "audit service" here, and the pure record types come verbatim from Task 1
//! (`bcs_service_api::types::bot_operation`).
//!
//! Slot semantics (spec §12.5 `(env, operation_id, step_key)` unique): the
//! record for one operation is a pure function of `(env, operation, resource)`
//! — including a DETERMINISTIC `audit_id` — so a legitimate replay of the same
//! operation produces a byte-identical record and keeps the first committed
//! row (DB timestamps are not part of the record). The same slot carrying
//! different content is a conflict and must be rejected, never overwritten.
//!
//! Controlled action mapping for Session-store resources:
//! - Session creation → `create/session/applied`;
//! - collect/uncollect → `collect/session/applied` (both directions of the
//!   favorite-state flip — `BotActionKind::Collect` covers both);
//! - participant add → `create_participant/session/applied`
//!   ([`CREATE_PARTICIPANT_STEP_KEY`]: a membership record is created as its
//!   own stable sub-command step, so ONE shared operation whose launch both
//!   creates the Session and materializes its deferred Human creator commits
//!   two DISTINCT audit rows — reusing `create/session/applied` here would
//!   collide in the `(env, operation_id, step_key)` slot and roll the
//!   deferred membership back after the Session had already committed),
//!   participant remove → `delete/session/applied`;
//! - participant mode/scope updates, title updates and eventful completion →
//!   `update/session/applied`.
//!
//! Idempotency: an already-collected collect / an uncollected uncollect uses
//! a conditional UPDATE with `with_transaction_stop_on_no_rows`, so a
//! no-change attempt ends the transaction BEFORE the audit step — "幂等无变化
//! 不制造 applied" holds without relying on any dialect's no-op
//! affected_rows counting.
//!
//! The SQL text keeps the `INTO bcs_bot_action_audits` fragment stable so the
//! failure-injection harness can target exactly this INSERT.

use bcs_db_api::{DbStatement, DbValue as Value};
use bcs_service_api::types::{
    BotActionAuditPhase, BotActionAuditRecord, BotActionKind, BotActionResourceKind,
    BotOperationContext, ServiceError, ServiceResult, stable_step_key,
};

/// Deterministic audit id for one (env, operation, step) slot: retries of the
/// same operation replay the same id, so a retried record is byte-identical
/// to the first committed row (its DB timestamps stay put).
pub(crate) fn session_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("session-action:{env}:{operation_id}:{step_key}")
}

/// Build one `applied` audit record for a Session-store mutation from the
/// typed operation context (spec §12.5): operator columns project ONLY from
/// [`bcs_service_api::types::BotOperationActor`] — never from a transport
/// request body — and Human/Bot-only are two distinct legitimate branches
/// (a `None` operator user id on the Bot/System branch means "no Human was
/// involved").
fn applied_record(
    operation: &BotOperationContext,
    env: &str,
    resource_id: &str,
    action: BotActionKind,
) -> BotActionAuditRecord {
    let step_key = stable_step_key(action, BotActionResourceKind::Session, BotActionAuditPhase::Applied);
    BotActionAuditRecord {
        audit_id: session_action_audit_id(env, &operation.operation_id, &step_key),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key,
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Session,
        resource_id: resource_id.to_string(),
        action,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

/// `create/session/applied` — Session creation.
pub(crate) fn create_session_audit_record(
    operation: &BotOperationContext,
    env: &str,
    session_id: &str,
) -> BotActionAuditRecord {
    applied_record(operation, env, session_id, BotActionKind::Create)
}

/// `create_participant/session/applied` — the participant-membership
/// creation sub-command step (plan Task 11: 子命令同 operation、不同稳定
/// step_key). A membership write is its own stable step WITHIN the owning
/// operation, deliberately distinct from the operation's `create/session/
/// applied` row so one shared launch operation that creates a Session AND
/// materializes a deferred participant commits two coexisting audit rows
/// instead of colliding in the `(env, operation_id, step_key)` unique slot.
pub(crate) const CREATE_PARTICIPANT_STEP_KEY: &str = "create_participant/session/applied";

/// The `create_participant/session/applied` audit record of one participant
/// membership creation (both the plain and the eventful store lane).
pub(crate) fn create_participant_audit_record(
    operation: &BotOperationContext,
    env: &str,
    session_id: &str,
) -> BotActionAuditRecord {
    BotActionAuditRecord {
        audit_id: session_action_audit_id(env, &operation.operation_id, CREATE_PARTICIPANT_STEP_KEY),
        env: env.to_string(),
        operation_id: operation.operation_id.clone(),
        step_key: CREATE_PARTICIPANT_STEP_KEY.to_string(),
        operator: operation.actor.clone(),
        resource_kind: BotActionResourceKind::Session,
        resource_id: session_id.to_string(),
        action: BotActionKind::Create,
        phase: BotActionAuditPhase::Applied,
        reason_code: None,
    }
}

/// `collect/session/applied` — collect AND uncollect favorites (both are the
/// same favorite-state mutation, distinguished only by their operation).
pub(crate) fn collect_state_audit_record(
    operation: &BotOperationContext,
    env: &str,
    session_id: &str,
) -> BotActionAuditRecord {
    applied_record(operation, env, session_id, BotActionKind::Collect)
}

/// `delete/session/applied` — participant removal.
pub(crate) fn remove_participant_audit_record(
    operation: &BotOperationContext,
    env: &str,
    session_id: &str,
) -> BotActionAuditRecord {
    applied_record(operation, env, session_id, BotActionKind::Delete)
}

/// `update/session/applied` — participant mode/scope updates and eventful
/// completion.
pub(crate) fn update_session_audit_record(
    operation: &BotOperationContext,
    env: &str,
    session_id: &str,
) -> BotActionAuditRecord {
    applied_record(operation, env, session_id, BotActionKind::Update)
}

/// The `INTO bcs_bot_action_audits` INSERT of one audit record (see the
/// module header for the stable fragment contract used by the failure
/// injection harness).
pub(crate) fn session_action_audit_insert(record: &BotActionAuditRecord) -> DbStatement {
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
                        "session action audit column '{name}' is unexpectedly NULL"
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

/// Classify a failed audited mutation against the audit slot (spec §12.5):
/// when the slot already carries the byte-identical record — a replay of an
/// operation whose earlier attempt FULLY committed (business write + audit)
/// — the caller may re-apply the business mutation WITHOUT the audit step
/// (idempotent completion); a same-slot row with DIFFERENT content is a
/// conflict; no slot row means the failure was genuine (the audit INSERT
/// never happened — e.g. an injected failing step — and the whole
/// transaction rolled back leaving no partial success).
#[must_use]
pub(crate) fn audit_slot_retry_classified(
    record: &BotActionAuditRecord,
    rows: Vec<bcs_db_api::DbRow>,
    transaction_error: &str,
) -> ServiceResult<()> {
    match rows.first() {
        Some(row) if action_audit_row_matches(record, row)? => Ok(()),
        Some(_) => Err(ServiceError::Conflict(format!(
            "session action audit slot '{}' already carries different content",
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

// ---------------------------------------------------------------------------
// Shared conformance fixtures (memory + SQL twins)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collect_step_key_uses_controlled_vocabulary() {
        let operation = BotOperationContext {
            operation_id: "collect-operation-1".into(),
            actor: bcs_service_api::types::BotOperationActor::Human {
                user_id: "a".into(),
                effective_actor_id: "bot-x".into(),
            },
        };
        let record = collect_state_audit_record(&operation, "contract", "session-a");
        assert_eq!(record.step_key, "collect/session/applied");
        assert_eq!(record.resource_id, "session-a");
        assert_eq!(record.operator.operator_user_id(), Some("a"));
        assert_eq!(record.operator.effective_actor_id(), "bot-x");
    }
}

#[cfg(test)]
/// Regression (PR #2568 round 2: MySQL "ERROR 22001 (1406): Data too long
/// for column 'audit_id'"): the composed lane audit id must fit the
/// migration-032/033 `audit_id` budget at every worst legal input — an
/// operation id at its own column cap and the longest step-key vocabulary
/// value, per every writer pen (message/group/session/file/chat-run/edge).
mod audit_width_conformance {
    use super::session_action_audit_id;
    use super::CREATE_PARTICIPANT_STEP_KEY;
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
            let id = session_action_audit_id(
                env,
                &"o".repeat(AUTHORITY_OPERATION_ID_VARCHAR_WIDTH),
                &step,
            );
            assert!(
                id.len() <= AUTHORITY_AUDIT_ID_VARCHAR_WIDTH,
                "lane audit_id length {} exceeds the durable {AUTHORITY_AUDIT_ID_VARCHAR_WIDTH}-char budget",
                id.len()
            );
            // The dedicated participant-membership step key is longer than
            // every stable-vocabulary key; it must still fit the budgets.
            let participant_id = session_action_audit_id(
                env,
                &"o".repeat(AUTHORITY_OPERATION_ID_VARCHAR_WIDTH),
                CREATE_PARTICIPANT_STEP_KEY,
            );
            assert!(
                participant_id.len() <= AUTHORITY_AUDIT_ID_VARCHAR_WIDTH,
                "participant audit_id length {} exceeds the durable {AUTHORITY_AUDIT_ID_VARCHAR_WIDTH}-char budget",
                participant_id.len()
            );
        }
    }
}

