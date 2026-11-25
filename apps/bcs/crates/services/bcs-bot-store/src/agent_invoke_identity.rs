//! Persisted physical-identity fences. These are not Agent eligibility claims.
//! Compare before assigning: MySQL evaluates SET assignments left to right.
use bcs_db_api::{
    DbError, DbPlugin, DbResult, DbStatement, DbTransactionOptions, DbTransactionParam as Param,
    DbTransactionStep as Step, DbTransactionStepResult, DbValue,
};

fn same(field: &str) -> String {
    // HEX preserves opaque byte identity on both dialects. Explicit nullness
    // separates NULL from the empty string (SQLite HEX(NULL) is empty).
    format!("COALESCE(HEX({field}),'')=COALESCE(HEX(?),'') AND ({field} IS NULL)=(? IS NULL)")
}

/// One locked transaction, three statements regardless of graph size. Missing
/// rows retain the existing zero-row outcome. The no-change first UPDATE locks
/// the physical row without dialect-specific FOR UPDATE or changing timestamps. A genuine change at an exhausted
/// or corrupt revision fails BEFORE the UPDATE, even with non-strict MySQL.
/// Field/assignment SQL is store-owned; values always use parameters.
pub(crate) async fn update(
    db: &dyn DbPlugin,
    env: &str,
    bot_id: &str,
    identity_fields: &[(&str, DbValue)],
    assignments: &str,
    values: Vec<DbValue>,
) -> DbResult<u64> {
    let unchanged = identity_fields
        .iter()
        .map(|(name, _)| format!("({})", same(name)))
        .collect::<Vec<_>>()
        .join(" AND ");
    let changed = format!("actor_kind='bot' AND NOT ({unchanged})");
    let mut guard_params = vec![bot_id.into(), env.into()];
    guard_params.extend(
        identity_fields
            .iter()
            .flat_map(|(_, value)| [value.clone(), value.clone()]),
    );
    let mut params: Vec<Param> = identity_fields
        .iter()
        .flat_map(|(_, value)| [Param::value(value.clone()), Param::value(value.clone())])
        .collect();
    params.extend(values.into_iter().map(Param::Value));
    params.extend([
        Param::value(bot_id),
        Param::value(env),
        Param::query_result(1, 0, "ok"),
    ]);
    let results = db.transaction_with_options(vec![
        Step::Execute(DbStatement::with_params(
            "UPDATE bcs_bots SET agent_invoke_identity_revision=agent_invoke_identity_revision WHERE bot_uuid=? AND env=?",
            vec![bot_id.into(), env.into()],
        )),
        Step::Query(DbStatement::with_params(
            format!("SELECT 1 AS ok WHERE NOT EXISTS (SELECT 1 FROM bcs_bots WHERE bot_uuid=? AND env=? AND ({changed}) AND (agent_invoke_identity_revision<1 OR agent_invoke_identity_revision>=9223372036854775807))"),
            guard_params,
        )),
        Step::Execute(DbStatement::with_transaction_params(
            format!("UPDATE bcs_bots SET agent_invoke_identity_revision=CASE WHEN {changed} THEN agent_invoke_identity_revision+1 ELSE agent_invoke_identity_revision END, {assignments} WHERE bot_uuid=? AND env=? AND ?=1"),
            params,
        )),
    ], DbTransactionOptions::with_deadline(std::time::Duration::from_secs(5))).await?;
    match results.last() {
        Some(DbTransactionStepResult::Executed(result)) => Ok(result.affected_rows),
        _ => Err(DbError::Conversion(
            "identity update returned an invalid result".into(),
        )),
    }
}
