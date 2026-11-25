//! Observe actual query statements and resolved transaction bindings. No policy
//! result is manufactured; native MySQL remains the executor.
#![allow(clippy::unwrap_used, reason = "native MySQL plan assertions")]
use bcs_db_api::*;
use std::sync::{Arc, Mutex};
pub struct QueryPlans {
    inner: Arc<dyn DbPlugin>,
    data: Mutex<Vec<DbStatement>>,
}
impl QueryPlans {
    pub fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            data: Mutex::new(vec![]),
        }
    }
    pub async fn verify_mysql_hints(&self) {
        let statements = self.data.lock().unwrap().clone();
        let mut checked = std::collections::BTreeSet::new();
        for s in statements {
            for name in [
                "idx_agent_invoke_edge_out",
                "idx_agent_invoke_edge_in",
                "idx_agent_invoke_request_out",
                "idx_agent_invoke_request_in",
                "idx_agent_invoke_request_pair",
                "idx_agent_invoke_audit_time",
                "idx_agent_invoke_audit_action_time",
            ] {
                if !s.sql().contains(&format!("FORCE INDEX ({name})")) {
                    continue;
                }
                let rows = self
                    .inner
                    .query(DbStatement::with_params(
                        format!("EXPLAIN {}", s.sql()),
                        s.params().to_vec(),
                    ))
                    .await
                    .unwrap();
                assert!(
                    rows.iter()
                        .any(|r| r.get_string("key").unwrap().as_deref() == Some(name)),
                    "expected {name} in native plan: {rows:?}"
                );
                checked.insert(name);
            }
        }
        assert!(
            checked.len() == 7,
            "native query harness must actually exercise indexed data reads: {checked:?}"
        );
        println!(
            "agent-invoke-query-mysql-indexes={checked:?}; small contract fixture, not large-graph rows-examined proof"
        );
    }
}
#[async_trait::async_trait]
impl DbPlugin for QueryPlans {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(s).await
    }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(s).await
    }
    async fn transaction(
        &self,
        s: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(s).await
    }
    async fn transaction_with_options(
        &self,
        s: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        let results = self.inner.transaction_with_options(s.clone(), o).await?;
        let mut data = self.data.lock().unwrap();
        for (i, step) in s.iter().enumerate() {
            if let DbTransactionStep::Query(statement) = step {
                if statement.sql().starts_with("SELECT e.id,")
                    || statement.sql().starts_with("SELECT COUNT(")
                    || statement.sql().starts_with("SELECT id,action_id,")
                {
                    data.push(DbStatement::with_params(
                        statement.sql(),
                        statement.resolve_transaction_params(&results, i)?,
                    ));
                }
            }
        }
        Ok(results)
    }
    async fn transaction_with_outcome(
        &self,
        s: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbTransactionResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction_with_outcome(s, o).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}
