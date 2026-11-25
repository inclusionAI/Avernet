//! Local outcome reporting explicitly observes rollback instead of relying on
//! Transaction::drop, whose rollback error cannot be reported to the caller.
use super::*;
use bcs_db_api::{DbTransactionFailure, DbTransactionResult, validate_transaction_outcome_steps};

impl LocalSqliteDbPlugin {
    pub(super) async fn transaction_with_outcome_inner(
        &self,
        steps: Vec<DbTransactionStep>,
        options: DbTransactionOptions,
    ) -> DbTransactionResult<Vec<DbTransactionStepResult>> {
        validate_transaction_outcome_steps(&steps).map_err(DbTransactionFailure::not_committed)?;
        // The outer failure can include a blocking-task panic after commit, so
        // never infer rollback from that opaque pool/task error.
        self.with_connection(move |connection| Ok(run(connection, steps, options)))
            .await
            .map_err(DbTransactionFailure::unknown)?
    }
}
fn run(
    connection: &mut Connection,
    steps: Vec<DbTransactionStep>,
    options: DbTransactionOptions,
) -> DbTransactionResult<Vec<DbTransactionStepResult>> {
    let behavior = if steps
        .iter()
        .all(|s| matches!(s, DbTransactionStep::Query(_)))
    {
        rusqlite::TransactionBehavior::Deferred
    } else {
        rusqlite::TransactionBehavior::Immediate
    };
    let tx = connection
        .transaction_with_behavior(behavior)
        .map_err(|e| {
            DbTransactionFailure::not_committed(DbError::Backend(format!(
                "begin sqlite transaction: {e}"
            )))
        })?;
    match execute_steps(&tx, steps, options) {
        Ok(results) => {
            // Even a local commit failure is conservative: the detailed API
            // does not infer certainty from a driver-specific error string.
            tx.commit().map_err(|e| {
                DbTransactionFailure::unknown(DbError::Backend(format!(
                    "commit sqlite transaction: {e}"
                )))
            })?;
            Ok(results)
        }
        Err(error) => match tx.rollback() {
            Ok(()) => Err(DbTransactionFailure::not_committed(error)),
            Err(_) => Err(DbTransactionFailure::unknown(error)),
        },
    }
}

pub(super) fn execute_steps(
    tx: &rusqlite::Transaction<'_>,
    steps: Vec<DbTransactionStep>,
    options: DbTransactionOptions,
) -> DbResult<Vec<DbTransactionStepResult>> {
    let transaction_started = profile_start();
    let deadline = options.deadline();
    let deadline_started = std::time::Instant::now();
    let mut results = Vec::with_capacity(steps.len());

    for (step_index, step) in steps.into_iter().enumerate() {
        match step {
            DbTransactionStep::Query(statement) => {
                let params = statement.resolve_transaction_params(&results, step_index)?;
                results.push(DbTransactionStepResult::Rows(query_with_connection_params(
                    tx,
                    statement.sql(),
                    &params,
                )?));
            }
            DbTransactionStep::Execute(statement) => {
                let params = statement.resolve_transaction_params(&results, step_index)?;
                let result = execute_with_connection_params(tx, statement.sql(), &params)?;
                let stop = statement.stops_transaction_on_no_rows() && result.affected_rows == 0;
                results.push(DbTransactionStepResult::Executed(result));
                if stop {
                    // Early success skips the REMAINING steps
                    // only; the pre-commit deadline check below
                    // still gates this commit path like any other.
                    break;
                }
            }
            DbTransactionStep::ExecuteChecked {
                statement,
                expected_affected_rows,
            } => {
                let params = statement.resolve_transaction_params(&results, step_index)?;
                let result = execute_with_connection_params(tx, statement.sql(), &params)?;
                if result.affected_rows != expected_affected_rows {
                    return Err(DbError::ConditionFailed {
                        expected: expected_affected_rows,
                        actual: result.affected_rows,
                    });
                }
                results.push(DbTransactionStepResult::Executed(result));
            }
        }
        // Between-step deadline check (Task-0 方案 A): stop
        // before the next step, roll the whole transaction
        // back through the Err path. A single statement that
        // is still running is not interruptible — the overrun
        // is only observable here, after it finished.
        if let Some(deadline) = deadline {
            if deadline_started.elapsed() > deadline {
                return Err(DbError::Timeout);
            }
        }
    }
    // Pre-commit deadline check (plugin contract: "between
    // two steps and before commit"): every success path —
    // including a stop-on-no-rows early exit — commits only
    // inside the deadline, otherwise the whole transaction
    // rolls back through the Err path.
    if let Some(deadline) = deadline {
        if deadline_started.elapsed() > deadline {
            return Err(DbError::Timeout);
        }
    }

    profile_elapsed("sqlite.transaction_steps", transaction_started);
    Ok(results)
}
