//! Durable Agent invocation storage. SQL, time, CAS and audit stay here, never
//! in the application or a delivery adapter. Installation creates no eligible
//! facts and enables no writer/runtime. Trusted composition owns activation.
mod facts;
mod scope;
mod snapshots;
mod management_reads;

use std::num::NonZeroU64;
use std::sync::Arc;
use std::time::Duration;

use bcs_db_api::{DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbTransactionOptions, DbValue};
use bcs_service_api::types::agent_invoke::*;

pub struct DbAgentInvokeStore {
    db: Arc<dyn DbPlugin>,
    flavor: DbSqlFlavor,
    scope: AgentInvokeScope,
    limits: AgentInvokeLimits,
    max_fact_ttl_ms: NonZeroU64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeScopeState {
    pub revision: NonZeroU64,
    pub writer_enabled: bool,
    pub runtime_enabled: bool,
}

struct ScopeRow {
    id: u64,
    revision: NonZeroU64,
    now_ms: u64,
    state: AgentInvokeScopeState,
}

impl DbAgentInvokeStore {
    pub fn new(
        db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, scope: AgentInvokeScope,
        limits: AgentInvokeLimits, max_fact_ttl_ms: NonZeroU64,
    ) -> AgentInvokeResult<Self> {
        // Canonical env spelling prevents aliases in legacy PAD SPACE / CI
        // env columns. IDs themselves are never trimmed or case-folded.
        if scope.namespace.trim().is_empty() || scope.namespace.trim() != scope.namespace
            || scope.namespace.chars().count() > 191 || scope.env.is_empty()
            || scope.env.len() > 64 || !scope.env.bytes().all(|c| {
                c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, b'-' | b'_' | b'.')
            }) || max_fact_ttl_ms.get() > i64::MAX as u64
        { return Err(AgentInvokeError::InvalidInput); }
        Ok(Self { db, flavor, scope, limits, max_fact_ttl_ms })
    }

    fn require_scope(&self, scope: &AgentInvokeScope) -> AgentInvokeResult<()> {
        if scope != &self.scope { return Err(AgentInvokeError::NotFound); }
        Ok(())
    }

    fn validate_bot_id(bot_id: &str) -> AgentInvokeResult<()> {
        if bot_id.trim().is_empty() || bot_id.starts_with("human_") || bot_id.chars().count() > 256 {
            return Err(AgentInvokeError::InvalidInput);
        }
        Ok(())
    }

    fn now_ms(&self) -> &'static str {
        match self.flavor {
            DbSqlFlavor::Mysql => "CAST(UNIX_TIMESTAMP(CURRENT_TIMESTAMP(3)) * 1000 AS UNSIGNED)",
            DbSqlFlavor::Sqlite => "CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER)",
        }
    }

    fn exact(&self, column: &str) -> String {
        match self.flavor {
            DbSqlFlavor::Mysql => format!("CAST({column} AS BINARY)"),
            DbSqlFlavor::Sqlite => format!("{column} COLLATE BINARY"),
        }
    }

    async fn query(&self, statement: DbStatement) -> AgentInvokeResult<Vec<DbRow>> {
        self.db.query(statement).await.map_err(|_| AgentInvokeError::Unavailable)
    }

    async fn read_scope_row(&self) -> AgentInvokeResult<ScopeRow> {
        let rows = self.query(self.scope_statement(false)).await?;
        self.decode_scope_rows(&rows)
    }

    fn scope_statement(&self, lock: bool) -> DbStatement {
        let suffix = if lock && self.flavor == DbSqlFlavor::Mysql { " FOR UPDATE" } else { "" };
        DbStatement::with_params(format!(
            "SELECT *, {} AS db_now_ms FROM agent_invoke_scopes \
             WHERE namespace = ? AND env = ? AND {} = ? AND {} = ?{suffix}",
            self.now_ms(), self.exact("namespace"), self.exact("env"),
        ), vec![self.scope.namespace.as_str().into(), self.scope.env.as_str().into(),
            self.scope.namespace.as_str().into(), self.scope.env.as_str().into()])
    }

    fn decode_scope_rows(&self, rows: &[DbRow]) -> AgentInvokeResult<ScopeRow> {
        let row = match rows {
            [row] => row,
            [] => return Err(AgentInvokeError::Unavailable),
            _ => return Err(AgentInvokeError::InvalidState),
        };
        if string(row, "namespace")? != self.scope.namespace || string(row, "env")? != self.scope.env {
            return Err(AgentInvokeError::InvalidState);
        }
        for (column, expected) in [
            ("max_out_degree", self.limits.max_out_degree.get() as u64),
            ("max_in_degree", self.limits.max_in_degree.get() as u64),
            ("max_pending_out", self.limits.max_pending_out.get() as u64),
            ("max_pending_in", self.limits.max_pending_in.get() as u64),
            ("max_fact_ttl_ms", self.max_fact_ttl_ms.get()),
        ] {
            if number(row, column)? != expected { return Err(AgentInvokeError::Conflict); }
        }
        let revision = positive(row, "revision")?;
        Ok(ScopeRow {
            id: positive(row, "id")?.get(), revision,
            now_ms: number(row, "db_now_ms")?,
            state: AgentInvokeScopeState {
                revision, writer_enabled: boolean(row, "writer_enabled")?,
                runtime_enabled: boolean(row, "runtime_enabled")?,
            },
        })
    }

    fn transaction_options(&self) -> DbTransactionOptions {
        DbTransactionOptions::with_deadline(Duration::from_secs(5))
    }
}

fn number(row: &DbRow, key: &str) -> AgentInvokeResult<u64> {
    match row.get(key) {
        Some(DbValue::I64(n)) if *n >= 0 => Ok(*n as u64),
        Some(DbValue::U64(n)) if *n <= i64::MAX as u64 => Ok(*n),
        _ => Err(AgentInvokeError::InvalidState),
    }
}
fn positive(row: &DbRow, key: &str) -> AgentInvokeResult<NonZeroU64> {
    NonZeroU64::new(number(row, key)?).ok_or(AgentInvokeError::InvalidState)
}
fn boolean(row: &DbRow, key: &str) -> AgentInvokeResult<bool> {
    match row.get(key) {
        Some(DbValue::Bool(value)) => Ok(*value),
        _ => match number(row, key)? { 0 => Ok(false), 1 => Ok(true), _ => Err(AgentInvokeError::InvalidState) },
    }
}
fn string(row: &DbRow, key: &str) -> AgentInvokeResult<String> {
    match row.get(key) {
        Some(DbValue::String(value)) => Ok(value.clone()),
        Some(DbValue::Bytes(value)) => String::from_utf8(value.clone()).map_err(|_| AgentInvokeError::InvalidState),
        _ => Err(AgentInvokeError::InvalidState),
    }
}
fn advance(version: u64) -> AgentInvokeResult<NonZeroU64> {
    version.checked_add(1).filter(|v| *v <= i64::MAX as u64)
        .and_then(NonZeroU64::new).ok_or(AgentInvokeError::InvalidState)
}
fn write_error(error: DbError) -> AgentInvokeError {
    match error {
        DbError::ConditionFailed { .. } => AgentInvokeError::Conflict,
        error if error.is_duplicate_key() => AgentInvokeError::Conflict,
        DbError::InvalidInput(_) | DbError::Unsupported(_) | DbError::Conversion(_) | DbError::Timeout => AgentInvokeError::Unavailable,
        // The present DbPlugin vocabulary does not distinguish an execution
        // backend error from a lost commit acknowledgement. Never retry a write
        // on that ambiguity. The full lifecycle recovers using its receipt key.
        DbError::Backend(_) => AgentInvokeError::CommitUnknown,
    }
}

#[cfg(test)]
mod tests;
