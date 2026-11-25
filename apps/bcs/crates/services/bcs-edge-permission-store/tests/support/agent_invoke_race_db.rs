//! Test-only, one-shot transaction scheduling around the REAL SQL plugin.
//! No SQL outcomes are forged except explicit lost acknowledgement injection.
use async_trait::async_trait;
use bcs_db_api::*;
use std::sync::{Arc, atomic::{AtomicBool, AtomicUsize, Ordering}};
use tokio::sync::Notify;

#[derive(Clone, Copy)]
pub enum Pause { BeforeTransaction, AfterCommit, None }

pub struct ControlledDb {
    inner: Arc<dyn DbPlugin>,
    pause: Pause,
    armed: AtomicBool,
    pub entered: Notify,
    pub resume: Notify,
    pub mutations: AtomicUsize,
    pub rollbacks: AtomicUsize,
    lose_ack: bool,
    fail_audit: bool,
}
impl ControlledDb {
    pub fn new(inner: Arc<dyn DbPlugin>, pause: Pause, lose_ack: bool, fail_audit: bool) -> Self {
        Self { inner, pause, armed: AtomicBool::new(true), entered: Notify::new(),
            resume: Notify::new(), mutations: AtomicUsize::new(0), rollbacks: AtomicUsize::new(0),
            lose_ack, fail_audit }
    }
    pub async fn wait(&self) {
        tokio::time::timeout(std::time::Duration::from_secs(30), self.entered.notified())
            .await.expect("actual transaction reached controlled boundary");
    }
    async fn pause(&self) {
        self.entered.notify_one();
        tokio::time::timeout(std::time::Duration::from_secs(30), self.resume.notified())
            .await.expect("test released the live transaction");
    }
}
fn is_command(steps: &[DbTransactionStep]) -> bool {
    steps.iter().any(|step| matches!(step, DbTransactionStep::ExecuteChecked { statement, .. }
        if statement.sql().starts_with("INSERT INTO agent_invoke_receipts")))
}
#[async_trait]
impl DbPlugin for ControlledDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> { self.inner.query(s).await }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(s).await }
    async fn transaction(&self, s: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(s).await
    }
    async fn transaction_with_options(&self, s: Vec<DbTransactionStep>, o: DbTransactionOptions)
        -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction_with_options(s, o).await }
    async fn transaction_with_outcome(&self, mut steps: Vec<DbTransactionStep>, o: DbTransactionOptions)
        -> DbTransactionResult<Vec<DbTransactionStepResult>> {
        if !is_command(&steps) { return self.inner.transaction_with_outcome(steps, o).await; }
        self.mutations.fetch_add(1, Ordering::SeqCst);
        assert!(steps.len() <= 32, "fixed bounded command plan");
        let first = self.armed.swap(false, Ordering::SeqCst);
        if first && matches!(self.pause, Pause::BeforeTransaction) { self.pause().await; }
        if first && self.fail_audit {
            let mut changed = false;
            for step in &mut steps {
                if let DbTransactionStep::ExecuteChecked { statement, .. } = step {
                    if statement.sql().starts_with("INSERT INTO agent_invoke_audit") {
                        *statement = DbStatement::new("INSERT INTO missing_race_audit_table (id) VALUES (1)");
                        changed = true;
                        break;
                    }
                }
            }
            assert!(changed, "injection reaches actual audit step, not preflight");
        }
        let result = self.inner.transaction_with_outcome(steps, o).await;
        if let Err(error) = &result {
            if error.outcome() == DbTransactionOutcome::NotCommitted {
                self.rollbacks.fetch_add(1, Ordering::SeqCst);
            }
        }
        if first && result.is_ok() {
            if matches!(self.pause, Pause::AfterCommit) { self.pause().await; }
            if self.lose_ack {
                return Err(DbTransactionFailure::unknown(DbError::Backend("lost acknowledged result in test transport".into())));
            }
        }
        result
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}
