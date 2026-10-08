//! Audit statement builders (spec §5.4 + §12.5).
//!
//! # Two STRICTLY SEPARATE audit lanes live in this module
//!
//! 1. `bot_manager_changes` (spec §5.4, plan Task 4): the ROLE lifecycle
//!    ledger. Written ONLY for actual state changes, inside the SAME
//!    transaction as the business edge write, as a batched `INSERT … SELECT`
//!    over the exact edges the mutation is changing — the row conditions are
//!    shared with the mutation itself so the audited rows can never diverge
//!    from the changed rows. One `operation_id` groups every edge of one
//!    `mutate_manager` call; the audit records the TRUE operator (`AuditActor`
//!    verbatim), never the subject.
//!
//!    `audit_id` is derived per edge as `<operation_id>-<edge_id>` (unique per
//!    row, no extra round trip), spelled per SQL flavor (SQLite `||`, MySQL
//!    `CONCAT`).
//!
//! 2. `bcs_bot_action_audits` (spec §12.5, plan Task 12): the ORDINARY
//!    BUSINESS audit of the friend/invitation lanes (permission-request
//!    inserts/decisions and friend edge grants/revokes). These rows are
//!    appended in the same store transaction as the business row they
//!    describe, carry the typed operator/effective actor from the REQUIRED
//!    [`BotOperationContext`], and NEVER interleave with the manager table
//!    above: the friend-lane builders here do not touch
//!    `bot_manager_changes`, and the manager mutations never write the
//!    per-record columns of `bcs_bot_action_audits`. A connect
//!    approve/reject/cancel decision is a permission-request decision, not a
//!    role change, so its audit id/step is derived from the friend operation
//!    alone — the role lane ids stay entirely outside this flow.

use bcs_db_api::{DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::AuditActor;
use bcs_service_api::types::BotActionAuditRecord;
use bcs_service_api::ServiceError;

/// `bot_manager_changes` column list written by the audit INSERT SELECT.
pub(super) const MANAGER_CHANGE_COLUMNS: &str = "audit_id, env, bot_id, subject_user_id, \
     edge_id, management_source_kind, management_source_id, action, actor_kind, actor_id, \
     operation_id";

/// Per-flavor `audit_id` expression over the selected edge row
/// (`<operation_id param> || '-' || <edge id>` / `CONCAT(?, '-', <edge id>)`).
pub(super) fn audit_id_expr(flavor: &DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Sqlite => "? || '-' || id",
        DbSqlFlavor::Mysql => "CONCAT(?, '-', id)",
    }
}

/// The FULL in-transaction mutation guard family shared by every changing
/// statement and its batched audit: the validated predicates of
/// `mutate_manager` are re-proved against CURRENT rows under the write
/// lock, so the phase-1 read stays a fast path and can never authorize a
/// stale mutation (plan/spec §5.4: 事务内检查 actor 仍有权、目标是同 env
/// live Human、目标非 owner):
///
/// - the Bot is live and ownership-initialized (version > 0) in this env;
/// - its unique approved owner slot exists (the schema's partial unique
///   index keeps `EXISTS` == exactly one);
/// - the ACTOR still holds a current owner/manager role on the Bot;
/// - the SUBJECT is a live human actor of the same env;
/// - the SUBJECT is NOT the owner (owner changes only via the transfer
///   flow).
///
/// Binding order (12 `?`): `(bot_id, env, env, bot_id, env, bot_id,
/// actor_from_id, subject_from_id, env, env, bot_id, subject_from_id)`.
pub(super) fn mutation_guards() -> &'static str {
    "EXISTS (SELECT 1 FROM bcs_bots tb \
         WHERE tb.bot_uuid = ? AND tb.env = ? \
           AND tb.ownership_version > 0 AND COALESCE(tb.is_deleted, 0) = 0) \
     AND EXISTS (SELECT 1 FROM edge_grants os \
         WHERE os.env = ? AND os.to_id = ? \
           AND os.grant_kind = 'owner' AND os.status = 'approved') \
     AND EXISTS (SELECT 1 FROM edge_grants ar \
         WHERE ar.env = ? AND ar.to_id = ? AND ar.from_id = ? \
           AND ar.grant_kind IN ('owner', 'manager') AND ar.status = 'approved') \
     AND EXISTS (SELECT 1 FROM bcs_bots th \
         WHERE th.bot_uuid = ? AND th.env = ? AND th.actor_kind = 'human' \
           AND COALESCE(th.is_deleted, 0) = 0) \
     AND NOT EXISTS (SELECT 1 FROM edge_grants so \
         WHERE so.env = ? AND so.to_id = ? AND so.from_id = ? \
           AND so.grant_kind = 'owner' AND so.status = 'approved')"
}

/// The parameters bound into [`mutation_guards`], in its documented order.
pub(super) fn mutation_guard_params(
    env: &str,
    bot_id: &str,
    actor_from_id: &str,
    subject_from_id: &str,
) -> Vec<DbValue> {
    vec![
        // tb (live, initialized bot row)
        DbValue::from(bot_id),
        DbValue::from(env),
        // os (unique approved owner slot exists)
        DbValue::from(env),
        DbValue::from(bot_id),
        // ar (actor still authorized)
        DbValue::from(env),
        DbValue::from(bot_id),
        DbValue::from(actor_from_id),
        // th (subject is a live same-env human)
        DbValue::from(subject_from_id),
        DbValue::from(env),
        // so (subject is not the owner)
        DbValue::from(env),
        DbValue::from(bot_id),
        DbValue::from(subject_from_id),
    ]
}

/// Parameters shared by every audit statement's SELECT/WHERE tail, in
/// binding order: the SELECT-list literals
/// `(operation_id_prefix, subject_user_id, actor_kind, actor_id,
///   operation_id)`, then the row_conditions' `(env, bot_id,
///   subject_from_id)`, then the [`mutation_guards`] tail.
#[allow(clippy::too_many_arguments)]
fn audit_params(
    env: &str,
    bot_id: &str,
    subject_user_id: &str,
    subject_from_id: &str,
    actor: &AuditActor,
    actor_from_id: &str,
    operation_id: &str,
) -> Vec<DbValue> {
    let mut params = vec![
        DbValue::from(operation_id),
        DbValue::from(subject_user_id),
        DbValue::from(actor.kind_str()),
        DbValue::from(actor.actor_id()),
        DbValue::from(operation_id),
        DbValue::from(env),
        DbValue::from(bot_id),
        DbValue::from(subject_from_id),
    ];
    params.extend(mutation_guard_params(env, bot_id, actor_from_id, subject_from_id));
    params
}

/// Batched audit INSERT SELECT for one mutation of the subject's manager
/// edges. `row_conditions` is the SAME row-selection text the mutation's
/// changing statement uses (status/kind/source conditions), and the shared
/// [`mutation_guards`] tail re-proves the validated predicates against
/// CURRENT rows inside the one lock; the audit and the change therefore
/// always name the same rows, and neither can touch a state the validated
/// plan no longer describes.
#[allow(clippy::too_many_arguments)]
pub(super) fn manager_change_audit_statement(
    flavor: &DbSqlFlavor,
    action: &'static str,
    row_conditions: &str,
    env: &str,
    bot_id: &str,
    subject_user_id: &str,
    subject_from_id: &str,
    actor: &AuditActor,
    actor_from_id: &str,
    operation_id: &str,
) -> DbStatement {
    DbStatement::with_params(
        &format!(
            "INSERT INTO bot_manager_changes ({MANAGER_CHANGE_COLUMNS}) \
             SELECT {audit_id}, env, to_id, ?, id, management_source_kind, \
               management_source_id, '{action}', ?, ?, ? \
             FROM edge_grants \
             WHERE {row_conditions} \
               AND {guards}",
            audit_id = audit_id_expr(flavor),
            guards = mutation_guards(),
        ),
        audit_params(
            env,
            bot_id,
            subject_user_id,
            subject_from_id,
            actor,
            actor_from_id,
            operation_id,
        ),
    )
}

// ---------------------------------------------------------------------------
// bcs_bot_action_audits — ORDINARY BUSINESS audit lane (plan Task 12)
// ---------------------------------------------------------------------------

/// Deterministic audit id of one (env, operation, step) slot: a legitimate
/// retry of the same operation composes the byte-identical record, so the
/// FIRST committed row (with its DB-generated timestamps, which are not part
/// of the record) is preserved and the retry is an idempotent no-op.
pub(crate) fn business_action_audit_id(env: &str, operation_id: &str, step_key: &str) -> String {
    format!("edge-action:{env}:{operation_id}:{step_key}")
}

/// Fail-closed validation of a NEW command's operation context (spec §12.5):
/// an empty operation id means the command arrived without its required
/// context. It is REJECTED (the whole business write stops), never silently
/// downgraded to a forged System operator. History rows predating the
/// cutover carry NULL operation ids and stay legal on READ; this check
/// applies to newly written records only.
pub(crate) fn require_business_operation(
    operation: &bcs_service_api::types::BotOperationContext,
) -> Result<(), ServiceError> {
    if operation.operation_id.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "friend-lane write is missing its required BotOperationContext \
                     (empty operation id): refusing to record a forged System operator"
                .into(),
            request_id: None,
        });
    }
    Ok(())
}

/// The `bcs_bot_action_audits` INSERT of one friend/invitation business
/// record. Must join the business write in the SAME DbPlugin transaction —
/// never a separate commit after the business change.
pub(crate) fn business_action_audit_insert(record: &BotActionAuditRecord) -> DbStatement {
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