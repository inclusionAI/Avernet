//! Opt-in transaction outcome reporting; legacy DbError remains unchanged.
use crate::{DbError, DbResult, DbTransactionStep, split_sql_statements};
use thiserror::Error;

/// Certainty about transactionally managed effects, not a retry instruction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum DbTransactionOutcome {
    /// No steps ran, or an explicit rollback completed before returning.
    NotCommitted,
    /// Commit may have happened, or rollback could not be confirmed.
    CommitUnknown,
}

/// Driver cause plus independently established commit certainty.
/// Never classify certainty by parsing a provider's error message.
#[derive(Debug, Error)]
#[error("database transaction failed ({outcome:?}): {error}")]
pub struct DbTransactionFailure {
    outcome: DbTransactionOutcome,
    #[source]
    error: DbError,
}

pub type DbTransactionResult<T> = Result<T, DbTransactionFailure>;

impl DbTransactionFailure {
    pub fn not_committed(error: DbError) -> Self {
        Self {
            outcome: DbTransactionOutcome::NotCommitted,
            error,
        }
    }
    pub fn unknown(error: DbError) -> Self {
        Self {
            outcome: DbTransactionOutcome::CommitUnknown,
            error,
        }
    }
    /// Legacy plugins expose only two errors with an explicit rollback contract.
    /// In particular a Backend error, even a duplicate-key-looking one, supplies
    /// no transaction phase or rollback acknowledgement.
    pub fn from_legacy(error: DbError) -> Self {
        match error {
            DbError::ConditionFailed { .. } | DbError::Timeout => Self::not_committed(error),
            _ => Self::unknown(error),
        }
    }
    pub fn outcome(&self) -> DbTransactionOutcome {
        self.outcome
    }
    pub fn error(&self) -> &DbError {
        &self.error
    }
    /// Discards certainty. Only use at a legacy compatibility boundary.
    pub fn into_error(self) -> DbError {
        self.error
    }
}

/// Conservative SQL subset for the opt-in detailed executor. Unlike the legacy
/// API this cannot run DDL, transaction control, scripts or commented SQL. Data
/// containing comment markers belongs in bound values. Callers additionally own
/// transaction-safe tables/functions/triggers; no external or nontransactional
/// side effects may be included. This is an input restriction, not a SQL parser.
pub fn validate_transaction_outcome_steps(steps: &[DbTransactionStep]) -> DbResult<()> {
    for step in steps {
        step.validate()?;
        let statement = match step {
            DbTransactionStep::Query(s)
            | DbTransactionStep::Execute(s)
            | DbTransactionStep::ExecuteChecked { statement: s, .. } => s,
        };
        let sql = statement.sql().trim();
        let keyword: String = sql.chars().take_while(char::is_ascii_alphabetic).collect();
        if !matches!(
            keyword.to_ascii_uppercase().as_str(),
            "SELECT" | "INSERT" | "UPDATE" | "DELETE"
        ) || sql.contains("--")
            || sql.contains("/*")
            || sql.contains('#')
            || split_sql_statements(sql).len() != 1
        {
            return Err(DbError::Unsupported("detailed transactions require single uncommented SELECT/INSERT/UPDATE/DELETE statements".into()));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{DbStatement, DbValue};
    #[test]
    fn legacy_causes_do_not_invent_commit_certainty() {
        for error in [
            DbError::Backend("1062 lost acknowledgement".into()),
            DbError::Conversion("decode".into()),
            DbError::InvalidInput("input".into()),
            DbError::Unsupported("legacy".into()),
        ] {
            let failure = DbTransactionFailure::from_legacy(error);
            assert_eq!(failure.outcome(), DbTransactionOutcome::CommitUnknown);
            assert!(!failure.to_string().is_empty());
        }
        for error in [
            DbError::Timeout,
            DbError::ConditionFailed {
                expected: 1,
                actual: 0,
            },
        ] {
            assert_eq!(
                DbTransactionFailure::from_legacy(error).outcome(),
                DbTransactionOutcome::NotCommitted
            );
        }
        let failure = DbTransactionFailure::not_committed(DbError::InvalidInput("bad".into()));
        assert!(matches!(failure.error(), DbError::InvalidInput(_)));
        assert!(matches!(failure.into_error(), DbError::InvalidInput(_)));
    }
    #[test]
    fn detailed_input_rejects_implicit_commit_and_hidden_scripts_before_execution() {
        for sql in [
            "CREATE TABLE x (id INT)",
            "COMMIT",
            "ROLLBACK",
            "CALL foo()",
            "BEGIN",
            "SELECT 1; COMMIT",
            "SELECT 1 /*! ; COMMIT */",
            "SELECT 1--2; COMMIT",
            "# hi\nSELECT 1",
            "",
        ] {
            assert!(
                validate_transaction_outcome_steps(&[DbTransactionStep::Query(DbStatement::new(
                    sql
                ))])
                .is_err(),
                "{sql}"
            );
        }
        for sql in [
            "SELECT 1;",
            "insert into t values (?)",
            "UPDATE t SET v=?",
            "DELETE FROM t",
        ] {
            assert!(
                validate_transaction_outcome_steps(&[DbTransactionStep::Execute(
                    DbStatement::with_params(sql, vec![DbValue::from("/*;--#")])
                )])
                .is_ok(),
                "{sql}"
            );
        }
        let bad = DbTransactionStep::Query(
            DbStatement::new("SELECT 1").with_transaction_stop_on_no_rows(),
        );
        assert!(matches!(
            validate_transaction_outcome_steps(&[bad]),
            Err(DbError::InvalidInput(_))
        ));
    }
}
