//! Shared step semantics for both legacy and outcome-aware MySQL execution.
use super::*;
pub(super) async fn execute_steps(
    tx: &mut MysqlTransaction<'_>,
    steps: Vec<PreparedTransactionStep>,
    deadline: Option<std::time::Duration>,
    started: std::time::Instant,
) -> DbResult<Vec<DbTransactionStepResult>> {
    let mut results = Vec::with_capacity(steps.len());
    for (step_index, step) in steps.into_iter().enumerate() {
        match step {
            PreparedTransactionStep::Query(statement) => {
                let params = statement.resolve_transaction_params(&results, step_index)?;
                let rows = tx.query(statement.sql(), mysql_params(&params)?).await?;
                let rows = rows
                    .into_iter()
                    .map(row_to_db_row)
                    .collect::<DbResult<Vec<_>>>()?;
                results.push(DbTransactionStepResult::Rows(rows));
            }
            PreparedTransactionStep::Execute(statement) => {
                let params = statement.resolve_transaction_params(&results, step_index)?;
                let result = tx
                    .execute_result(statement.sql(), mysql_params(&params)?)
                    .await?;
                let stop = statement.stops_transaction_on_no_rows() && result.affected_rows == 0;
                results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: result.affected_rows,
                    last_insert_id: result.last_insert_id,
                }));
                if stop {
                    // Early success skips the
                    // REMAINING steps only; the
                    // pre-commit deadline check
                    // below still gates this commit
                    // path like any other.
                    break;
                }
            }
            PreparedTransactionStep::ExecuteChecked(statement, expected) => {
                let params = statement.resolve_transaction_params(&results, step_index)?;
                let result = tx
                    .execute_result(statement.sql(), mysql_params(&params)?)
                    .await?;
                if result.affected_rows != expected {
                    return Err(DbError::ConditionFailed {
                        expected,
                        actual: result.affected_rows,
                    });
                }
                results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: result.affected_rows,
                    last_insert_id: result.last_insert_id,
                }));
            }
        }
        if let Some(deadline) = deadline {
            if started.elapsed() > deadline {
                return Err(DbError::Timeout);
            }
        }
    }
    // Pre-commit deadline check (plugin contract:
    // "between two steps and before commit"):
    // every success path — including a
    // stop-on-no-rows early exit — commits only
    // inside the deadline.
    if let Some(deadline) = deadline {
        if started.elapsed() > deadline {
            return Err(DbError::Timeout);
        }
    }
    Ok(results)
}
