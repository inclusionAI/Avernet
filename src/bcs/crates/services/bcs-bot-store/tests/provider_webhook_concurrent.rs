//! Both requests read the same Bot snapshot; only real SQLite writes are delayed.
use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_bot_store::DbProviderStore;
use bcs_db_api::{DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{BotCapabilities, ProviderBotBindingRepoPort};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use tokio::sync::Barrier;

#[path = "common/bot_provider.rs"]
mod database;

struct SharedSnapshotDb {
    inner: Arc<LocalSqliteDbPlugin>,
    remaining_reads: AtomicUsize,
    both_read: Barrier,
}

#[async_trait]
impl DbPlugin for SharedSnapshotDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let metadata_read = statement.sql().starts_with("SELECT bot_uuid, provider_id, provider_bot_ref, connection_mode");
        let rows = self.inner.query(statement).await?;
        if metadata_read && self.remaining_reads.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| n.checked_sub(1)).is_ok() {
            self.both_read.wait().await;
        }
        Ok(rows)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(statement).await }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction(steps).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn concurrent_webhook_writes_do_not_require_a_metadata_version() {
    let db = database::sqlite().await;
    let setup = DbProviderStore::sqlite(db.clone());
    setup.create_provider_bot(BotProviderRecord {
        bot_uuid: "concurrent".into(), provider_id: "provider-a".into(), provider_bot_ref: "concurrent".into(),
        connection_mode: BotConnectionMode::Gateway, webhook_url: None, is_deleted: false,
    }, BotCapabilities::default(), "owner", "test-concurrent-runtime").await.unwrap();
    let store = DbProviderStore::sqlite(Arc::new(SharedSnapshotDb {
        inner: db, remaining_reads: AtomicUsize::new(2), both_read: Barrier::new(2),
    }));
    let (first, second) = tokio::time::timeout(std::time::Duration::from_secs(5), async {
        tokio::join!(
            store.update_provider_webhook("provider-a", "concurrent", Some("https://example.org/first".into()), 1000),
            store.update_provider_webhook("provider-a", "concurrent", Some("https://example.org/second".into()), 1000),
        )
    }).await.expect("both requests must finish without waiting for another metadata version");
    first.unwrap();
    second.unwrap();
    let record = store.get_provider_bot("concurrent").await.unwrap().unwrap();
    assert!(matches!(record.webhook_url.as_deref(), Some("https://example.org/first" | "https://example.org/second")));
    assert_eq!(record.webhook_url, store.get_binding_by_bot_uuid("concurrent").await.unwrap().unwrap().webhook_url);
}
