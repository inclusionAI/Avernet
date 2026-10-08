//! `bot_manager_changes` audit statement builders (spec §5.4, plan Task 4).
//!
//! The audit is written ONLY for actual state changes, inside the SAME
//! transaction as the business edge write, as a batched `INSERT … SELECT`
//! over the exact edges the mutation is changing — the row conditions are
//! shared with the mutation itself so the audited rows can never diverge
//! from the changed rows. One `operation_id` groups every edge of one
//! `mutate_manager` call; the audit records the TRUE operator (`AuditActor`
//! verbatim), never the subject.
//!
//! `audit_id` is derived per edge as `<operation_id>-<edge_id>` (unique per
//! row, no extra round trip), spelled per SQL flavor (SQLite `||`, MySQL
//! `CONCAT`).

use bcs_db_api::{DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::AuditActor;

use bcs_service_api::port::repo::bot_authority::human_actor_id;

/// `bot_manager_changes` column list written by the audit INSERT SELECT.
pub(super) const MANAGER_CHANGE_COLUMNS: &str = "audit_id, env, bot_id, subject_user_id, \
     edge_id, management_source_kind, management_source_id, action, actor_kind, actor_id, \
     operation_id";

/// Per-flavor `audit_id` expression over the selected edge row
/// (`<operation_id param> || '-' || <edge id>` / `CONCAT(?, '-', <edge id>)`).
fn audit_id_expr(flavor: &DbSqlFlavor) -> &'static str {
    match flavor {
        DbSqlFlavor::Sqlite => "? || '-' || id",
        DbSqlFlavor::Mysql => "CONCAT(?, '-', id)",
    }
}

/// The actor-still-authorized re-check, evaluated inside the mutation
/// transaction: the mutation's changing statements (and their audits)
/// select only rows a CURRENT owner/approved-manager actor may still
/// change. The guard is row-based: the actor's role edge must live on the
/// same env/Bot as the mutation.
///
/// The guard's `?` is the actor's EDGE row subject (`human_<user_id>`);
/// service/system actors hold no role edges, so a mutation driven by them
/// matches nothing and fails closed.
pub(super) fn actor_still_authorized_guard() -> &'static str {
    "EXISTS (SELECT 1 FROM edge_grants ar \
       WHERE ar.env = ? AND ar.to_id = ? AND ar.from_id = ? \
         AND ar.grant_kind IN ('owner', 'manager') AND ar.status = 'approved')"
}

/// Parameters shared by every audit statement's SELECT/WHERE tail:
/// `(operation_id_prefix, subject_user_id, actor_kind, actor_id,
///   operation_id, env, bot_id, subject_from_id, guard env, guard bot_id,
///   guard actor_from_id)` — in binding order.
#[allow(clippy::too_many_arguments)]
fn audit_params(
    env: &str,
    bot_id: &str,
    subject_user_id: &str,
    actor: &AuditActor,
    actor_from_id: &str,
    operation_id: &str,
) -> Vec<DbValue> {
    vec![
        DbValue::from(operation_id),
        DbValue::from(subject_user_id),
        DbValue::from(actor.kind_str()),
        DbValue::from(actor.actor_id()),
        DbValue::from(operation_id),
        DbValue::from(env),
        DbValue::from(bot_id),
        DbValue::from(human_actor_id(subject_user_id)),
        DbValue::from(env),
        DbValue::from(bot_id),
        DbValue::from(actor_from_id),
    ]
}

/// Batched audit INSERT SELECT for one mutation of the subject's manager
/// edges. `row_conditions` is the SAME row-selection text the mutation's
/// changing statement uses (status/kind/source conditions); the audit and
/// the change therefore always name the same rows inside the one lock.
#[allow(clippy::too_many_arguments)]
pub(super) fn manager_change_audit_statement(
    flavor: &DbSqlFlavor,
    action: &'static str,
    row_conditions: &str,
    env: &str,
    bot_id: &str,
    subject_user_id: &str,
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
               AND {actor_guard}",
            audit_id = audit_id_expr(flavor),
            actor_guard = actor_still_authorized_guard(),
        ),
        audit_params(env, bot_id, subject_user_id, actor, actor_from_id, operation_id),
    )
}