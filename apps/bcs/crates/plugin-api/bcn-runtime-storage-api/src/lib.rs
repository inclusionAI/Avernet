//! Infrastructure Plugin API v1. Implementations must pass `conformance`.
use std::sync::Arc;
use async_trait::async_trait;
use bcn_runtime_model::*;
pub mod conformance;
pub mod validation;

/// Linearizable single-partition operations. Never emulate transactions with read/write loops.
#[async_trait]
pub trait AtomicStore: Send + Sync {
    /// Results match input order, including duplicates and absent entries.
    async fn read_many(&self, scope: &Scope, partition: &str, keys: &[String]) -> StorageResult<Vec<Option<VersionedValue>>>;
    /// Validate the entire batch, then check ALL conditions before ANY mutation.
    /// Delete also allocates a fresh revision; no revision may repeat after recreation.
    async fn transact(&self, scope: &Scope, partition: &str, batch: AtomicBatch) -> StorageResult<TransactionReceipt>;
    /// Lexical key order, exclusive cursor, <= limit entries; no snapshot across pages.
    /// A page must inspect at most limit + 1 matching entries, never enumerate a partition.
    async fn scan_page(&self, scope: &Scope, partition: &str, prefix: &str, cursor: Option<ScanCursor>, limit: usize) -> StorageResult<ScanPage>;
    async fn server_time_ms(&self) -> StorageResult<u64>;
}
/// Local test clock. Production stores return authoritative backend server time.
pub trait ServerClock: Send + Sync { fn now_ms(&self) -> u64; }
pub type SharedAtomicStore = Arc<dyn AtomicStore>;

/// Failure discovery only. TTL expiry does not revoke ownership or fence writes.
/// Storage read failures produce Unknown observations, never fabricated Dead results.
#[async_trait]
pub trait NodeLivenessPort: Send + Sync {
    async fn register(&self, node: NodeIdentity, ttl_ms: u64) -> StorageResult<()>;
    async fn renew(&self, node: &NodeIdentity, ttl_ms: u64) -> StorageResult<()>;
    async fn inspect_batch(&self, nodes: &[NodeIdentity]) -> StorageResult<Vec<NodeObservation>>;
    async fn list_members(&self, scope: &Scope, cursor: Option<String>, limit: u32) -> StorageResult<Page<NodeObservation>>;
    async fn mark_draining(&self, node: &NodeIdentity) -> StorageResult<()>;
}
