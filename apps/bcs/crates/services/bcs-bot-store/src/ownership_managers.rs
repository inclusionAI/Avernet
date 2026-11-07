//! Provider-only initial manager snapshot. SQL work is bounded and batched;
//! all steps are spliced into the caller's owner/version transaction.
use bcs_db_api::{DbSqlFlavor, DbStatement, DbTransactionParam, DbTransactionStep, DbValue};
use bcs_service_api::{ServiceResult, types::bot_authority::{
    AuthorityActor, AuthorityMutation, OwnershipInitialization, validate_initial_managers,
    normalize_provider_owners,
}};

pub(crate) fn validate_provider_initialization(value: &OwnershipInitialization) -> ServiceResult<()> {
    if !matches!(&value.mutation.actor, AuthorityActor::Provider { provider_id } if !provider_id.trim().is_empty()) {
        return Err(crate::ownership::initialization_invalid_public("Provider authority initialization requires a Provider actor"));
    }
    let (owner, _) = normalize_provider_owners(std::slice::from_ref(&value.owner_user_id))?;
    if owner != value.owner_user_id {
        return Err(crate::ownership::initialization_invalid_public("owner must be normalized"));
    }
    crate::ownership::validate_mutation(&value.mutation)?;
    validate_initial_managers(value)
}

pub(crate) fn audit_state(owner: &str, managers: &[String]) -> String {
    if managers.is_empty() { return crate::ownership::initialize_audit_state(owner, 1); }
    serde_json::json!({"owner": owner, "version": 1,
        "managers": managers.iter().map(|id| format!("human_{id}")).collect::<Vec<_>>()}).to_string()
}

pub(crate) fn audit_statement(
    env: &str, bot_id: &str, mutation: &AuthorityMutation, owner: &str, managers: &[String],
) -> DbStatement {
    DbStatement::with_params(
        "INSERT INTO bot_authority_changes \
         (env, bot_id, mutation_id, sequence, action, actor_kind, actor_id, subject_id, before_state, after_state) \
         VALUES (?, ?, ?, 0, 'initialize', ?, ?, ?, NULL, ?)",
        vec![DbValue::from(env), DbValue::from(bot_id), DbValue::from(mutation.mutation_id.as_str()),
            DbValue::from(mutation.actor.kind().as_str()), DbValue::from(mutation.actor.actor_id()),
            DbValue::from(owner), DbValue::from(audit_state(owner, managers))],
    )
}

/// Five statements for 1..=100 managers: batched Human ensure, live-Human guard,
/// batched edge upsert, exact-edge guard and guard consumption. No per-manager database round trips.
/// Caller has locked/fenced the Bot's uninitialized state. No external I/O here.
pub(crate) fn append_steps(
    steps: &mut Vec<DbTransactionStep>, flavor: DbSqlFlavor, env: &str,
    bot_id: &str, managers: &[String], steps_base: usize,
) {
    if managers.is_empty() { return; }
    let mut humans = Vec::new();
    let mut edges = Vec::new();
    let mut actor_ids = Vec::new();
    for user in managers {
        let actor = format!("human_{user}");
        humans.extend([DbValue::from(actor.as_str()), DbValue::from(user.as_str()),
            DbValue::from(uuid::Uuid::new_v4().to_string()), DbValue::from(user.as_str()), DbValue::from(env)]);
        edges.extend([DbValue::from(env), DbValue::from(actor.as_str()), DbValue::from(bot_id)]);
        actor_ids.push(DbValue::from(actor));
    }
    let human_values = vec!["(?, 'human', ?, '{\"summary\":null}', ?, ?, 'protected', 'online', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"; managers.len()].join(", ");
    let human_conflict = match flavor {
        DbSqlFlavor::Mysql => " ON DUPLICATE KEY UPDATE bot_uuid = bot_uuid",
        DbSqlFlavor::Sqlite => " ON CONFLICT(bot_uuid, env) DO NOTHING",
    };
    steps.push(DbTransactionStep::Execute(DbStatement::with_params(format!(
        "INSERT INTO bcs_bots (bot_uuid, actor_kind, name, bot_info, session_token, created_by, visibility, status, env, registered_at, updated_at) VALUES {human_values}{human_conflict}"), humans)));
    let placeholders = vec!["?"; managers.len()].join(", ");
    let identity = match flavor { DbSqlFlavor::Mysql => "CAST(bot_uuid AS BINARY)", DbSqlFlavor::Sqlite => "bot_uuid" };
    let mut params = vec![DbValue::from(env)];
    params.extend(actor_ids.clone());
    params.push(DbValue::from(managers.len() as i64));
    let human_guard = steps_base + steps.len();
    steps.push(DbTransactionStep::Query(DbStatement::with_params(format!(
        "SELECT COUNT(*) AS ok FROM bcs_bots WHERE env = ? AND {identity} IN ({placeholders}) AND actor_kind = 'human' AND COALESCE(is_deleted, 0) = 0 HAVING COUNT(*) = ?"), params)));
    let edge_values = vec!["(?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from')"; managers.len()].join(", ");
    let edge_conflict = match flavor {
        DbSqlFlavor::Mysql => " ON DUPLICATE KEY UPDATE status = 'approved', rules = NULL",
        DbSqlFlavor::Sqlite => " ON CONFLICT(from_id, to_id, env, grant_kind, grant_ref_id) DO UPDATE SET status = 'approved', rules = NULL",
    };
    steps.push(DbTransactionStep::Execute(DbStatement::with_params(format!(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) VALUES {edge_values}{edge_conflict}"), edges)));
    // MySQL's pre-existing unique keys may use case-insensitive collation.
    // A successful upsert is not evidence of the requested byte-exact identity.
    let identity = match flavor { DbSqlFlavor::Mysql => "CAST(from_id AS BINARY)", DbSqlFlavor::Sqlite => "from_id" };
    let mut params = vec![DbValue::from(env), DbValue::from(bot_id)];
    params.extend(actor_ids);
    params.push(DbValue::from(managers.len() as i64));
    let edge_guard = steps_base + steps.len();
    steps.push(DbTransactionStep::Query(DbStatement::with_params(format!(
        "SELECT COUNT(*) AS ok FROM edge_grants WHERE env = ? AND to_id = ? AND {identity} IN ({placeholders}) AND grant_kind = 'manager' AND grant_ref_id = 0 AND rules IS NULL AND status = 'approved' HAVING COUNT(*) = ?"), params)));
    steps.push(DbTransactionStep::Query(DbStatement::with_transaction_params(
        "SELECT ? AS humans_live, ? AS managers_live", vec![
            DbTransactionParam::query_result(human_guard, 0, "ok"),
            DbTransactionParam::query_result(edge_guard, 0, "ok"),
        ])));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn statement_count_is_constant_and_batch_size_is_bounded() {
        for flavor in [DbSqlFlavor::Mysql, DbSqlFlavor::Sqlite] {
            for size in [0, 1, 100] {
                let managers = (0..size).map(|i| format!("manager-{i}")).collect::<Vec<_>>();
                let mut steps = Vec::new();
                append_steps(&mut steps, flavor, "test", "bot", &managers, 7);
                assert_eq!(steps.len(), if size == 0 { 0 } else { 5 });
                for step in steps {
                    let statement = match step {
                        DbTransactionStep::Execute(s) | DbTransactionStep::Query(s) => s,
                        DbTransactionStep::ExecuteChecked { statement, .. } => statement,
                    };
                    assert!(statement.params().len() <= 500);
                }
            }
        }
    }
}
