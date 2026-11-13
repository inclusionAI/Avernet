use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_storage_api::{AtomicStore, SharedAtomicStore};
#[derive(Default)]
pub struct Counts { pub reads: Vec<usize>, pub writes: Vec<(usize, usize)>, pub scans: Vec<usize>, pub clocks: usize }
pub struct Observed { pub inner: SharedAtomicStore, pub counts: Mutex<Counts>, pub fail_read: AtomicBool, pub fail_write: AtomicBool }
impl Observed {
    pub fn new(inner: SharedAtomicStore) -> Arc<Self> { Arc::new(Self { inner, counts: Mutex::new(Counts::default()), fail_read: AtomicBool::new(false), fail_write: AtomicBool::new(false) }) }
    pub fn reset(&self) { *self.counts.lock().unwrap() = Counts::default(); }
}
#[async_trait]
impl AtomicStore for Observed {
    async fn read_many(&self, scope: &Scope, partition: &str, keys: &[String]) -> StorageResult<Vec<Option<VersionedValue>>> {
        self.counts.lock().unwrap().reads.push(keys.len());
        if self.fail_read.load(Ordering::SeqCst) { return Err(StorageError::Unavailable("injected read failure".into())); }
        self.inner.read_many(scope, partition, keys).await
    }
    async fn transact(&self, scope: &Scope, partition: &str, batch: AtomicBatch) -> StorageResult<TransactionReceipt> {
        self.counts.lock().unwrap().writes.push((batch.conditions.len(), batch.mutations.len()));
        if self.fail_write.swap(false, Ordering::SeqCst) { return Err(StorageError::Unavailable("injected transaction failure".into())); }
        self.inner.transact(scope, partition, batch).await
    }
    async fn scan_page(&self, scope: &Scope, partition: &str, prefix: &str, cursor: Option<ScanCursor>, limit: usize) -> StorageResult<ScanPage> {
        self.counts.lock().unwrap().scans.push(limit);
        self.inner.scan_page(scope, partition, prefix, cursor, limit).await
    }
    async fn server_time_ms(&self) -> StorageResult<u64> { self.counts.lock().unwrap().clocks += 1; self.inner.server_time_ms().await }
}
