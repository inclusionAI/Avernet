//! Event store results helpers.

use super::*;

pub(super) fn transaction_rows(
    results: &[DbTransactionStepResult],
    step: usize,
) -> Result<&[DbRow], EventRepoError> {
    match results.get(step) {
        Some(DbTransactionStepResult::Rows(rows)) => Ok(rows),
        Some(DbTransactionStepResult::Executed(_)) => Err(EventRepoError::Storage(format!(
            "transaction step {step} returned an execute result"
        ))),
        None => Err(EventRepoError::Storage(format!(
            "transaction result {step} is missing"
        ))),
    }
}

pub(super) fn transaction_affected_rows(
    results: &[DbTransactionStepResult],
    step: usize,
) -> Result<u64, EventRepoError> {
    match results.get(step) {
        Some(DbTransactionStepResult::Executed(result)) => Ok(result.affected_rows),
        Some(DbTransactionStepResult::Rows(_)) => Err(EventRepoError::Storage(format!(
            "transaction step {step} returned rows"
        ))),
        None => Err(EventRepoError::Storage(format!(
            "transaction result {step} is missing"
        ))),
    }
}
pub(super) fn transaction_target_ids(
    results: &[DbTransactionStepResult],
    target_query_step: usize,
) -> Result<Vec<String>, EventRepoError> {
    let rows = match results.get(target_query_step) {
        Some(DbTransactionStepResult::Rows(rows)) => rows,
        Some(DbTransactionStepResult::Executed(_)) => {
            return Err(EventRepoError::Storage(
                "target query transaction step returned execute result".to_string(),
            ));
        }
        None => {
            return Err(EventRepoError::Storage(
                "target query transaction result missing".to_string(),
            ));
        }
    };
    rows.iter().map(|row| column(row, "target_id")).collect()
}
pub(super) fn storage_error(error: DbError) -> EventRepoError {
    EventRepoError::Storage(error.to_string())
}

pub(super) fn map_write_error(error: DbError) -> EventRepoError {
    if error.is_duplicate_key() {
        EventRepoError::Conflict(error.to_string())
    } else {
        storage_error(error)
    }
}
