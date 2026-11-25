//! Detailed commit certainty is additive; legacy manager errors stay unchanged.
use super::*;
use bcs_db_api::{DbTransactionFailure, DbTransactionResult};
impl MysqlDbManager {
    pub(crate) async fn with_transaction_outcome<F, T>(
        &self,
        db: &str,
        f: F,
    ) -> DbTransactionResult<T>
    where
        F: for<'tx> FnOnce(
            &'tx mut MysqlTransaction<'_>,
        ) -> Pin<Box<dyn Future<Output = DbResult<T>> + Send + 'tx>>,
    {
        let pool = self
            .require_pool(db)
            .map_err(DbTransactionFailure::not_committed)?;
        let executor = self
            .statement_executor(db)
            .map_err(DbTransactionFailure::not_committed)?;
        let start = Instant::now();
        let mut conn = pool.get_conn().await.map_err(|e| {
            DbTransactionFailure::not_committed(DbError::Backend(format!(
                "get mysql connection for '{db}': {e}"
            )))
        })?;
        let tx = conn
            .start_transaction(TxOpts::default())
            .await
            .map_err(|e| {
                DbTransactionFailure::not_committed(DbError::Backend(format!(
                    "begin mysql transaction: {e}"
                )))
            })?;
        let mut tx_conn = MysqlTransaction {
            tx,
            db: db.into(),
            executor,
            statement_count: 0,
        };
        match f(&mut tx_conn).await {
            Ok(result) => {
                let statements = tx_conn.statement_count();
                tx_conn
                    .commit()
                    .await
                    .map_err(DbTransactionFailure::unknown)?;
                log_tx_ok(db, start.elapsed().as_millis(), statements);
                Ok(result)
            }
            Err(error) => {
                let statements = tx_conn.statement_count();
                let rollback = tx_conn.rollback().await;
                error!(db=%db,duration_ms=start.elapsed().as_millis(),statements,
                    rollback_confirmed=rollback.is_ok(),error=%error,"mysql transaction failed");
                match rollback {
                    Ok(()) => Err(DbTransactionFailure::not_committed(error)),
                    Err(_) => Err(DbTransactionFailure::unknown(error)),
                }
            }
        }
    }
}

#[cfg(test)]
#[allow(
    clippy::unwrap_used,
    reason = "outcome assertions on an absent test datasource"
)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn absent_datasource_never_runs_transaction_work() {
        let mut manager = MysqlDbManager {
            pools: Arc::new(HashMap::new()),
            statement_executors: Arc::new(HashMap::new()),
            config: MysqlDbConfig::new(),
            enabled: false,
        };
        for enabled in [false, true] {
            manager.enabled = enabled;
            let failed = manager
                .with_transaction_outcome::<_, ()>("absent", |_| {
                    Box::pin(async { panic!("work must not run without a datasource") })
                })
                .await
                .unwrap_err();
            assert_eq!(
                failed.outcome(),
                bcs_db_api::DbTransactionOutcome::NotCommitted
            );
        }
    }
}
