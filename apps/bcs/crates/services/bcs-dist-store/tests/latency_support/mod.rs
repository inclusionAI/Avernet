use async_trait::async_trait;
use bcs_store_api::{document::DocumentStorePlugin, *};
use serde_json::Value;
use std::{
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};
pub struct DelayedDocuments {
    pub store: Arc<dyn DocumentStorePlugin>,
    pub millis: AtomicU64,
    pub slow_key: std::sync::Mutex<Option<(String, u64)>>,
}
impl DelayedDocuments {
    async fn delay(&self, key: &str) {
        let millis = self
            .slow_key
            .lock()
            .expect("delay lock")
            .as_ref()
            .filter(|(pattern, _)| key.contains(pattern))
            .map_or_else(
                || self.millis.load(Ordering::Relaxed),
                |(_, millis)| *millis,
            );
        if millis > 0 {
            tokio::time::sleep(Duration::from_millis(millis)).await;
        }
    }
}
impl StorePlugin for DelayedDocuments {
    fn supports(&self, capability: StoreCapability) -> bool {
        self.store.supports(capability)
    }
}
#[async_trait]
impl DocumentStorePlugin for DelayedDocuments {
    async fn read_document(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        self.delay(key).await;
        self.store.read_document(key).await
    }
    async fn create_document(&self, key: &str, value: &Value) -> StoreResult<WriteOutcome> {
        self.delay(key).await;
        self.store.create_document(key, value).await
    }
    async fn replace_document(
        &self,
        key: &str,
        expected: Version,
        value: &Value,
    ) -> StoreResult<WriteOutcome> {
        self.delay(key).await;
        self.store.replace_document(key, expected, value).await
    }
}
