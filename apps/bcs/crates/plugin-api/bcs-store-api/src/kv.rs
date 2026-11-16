use crate::*;
use async_trait::async_trait;
use std::time::Duration;

/// Byte-valued keys. Strings are opaque UTF-8 keys, not business identifiers.
/// Missing and expired values return None; errors never become cache misses.
/// Every ordinary mutation participates in version history when ConditionalKv
/// is advertised, even if the caller does not request a version. No eviction of
/// live data is allowed. Single-key writes are atomic; mget is NOT a snapshot.
#[async_trait]
pub trait KvStorePlugin: StorePlugin {
    async fn get(&self, key: &str) -> StoreResult<Option<Vec<u8>>>;
    /// Same length/order as keys, including duplicates and missing entries.
    /// Bounded by provider limits. Cross-partition batching may use several calls.
    async fn mget(&self, keys: &[String]) -> StoreResult<Vec<Option<Vec<u8>>>>;
    async fn set(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<()>;
    /// Atomic existence check + value + expiry; true only for this creation.
    async fn put_if_absent(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<bool>;
    /// Content and version from one consistent read; requires ConditionalKv.
    async fn get_versioned(&self, key: &str) -> StoreResult<Option<Versioned<Vec<u8>>>>;
    /// Requires a live exact version; generated next version advances atomically.
    async fn compare_and_set(
        &self,
        key: &str,
        expected: Version,
        value: &[u8],
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome>;
    /// Uses caller's logical version, strictly greater than retained history.
    async fn set_if_newer(
        &self,
        key: &str,
        version: Version,
        value: &[u8],
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome>;
    /// Atomic value deletion; keep version history when conditional writes exist.
    async fn delete(&self, key: &str) -> StoreResult<bool>;
    /// ConditionalKv. A stale delete cannot remove a recreated key.
    async fn delete_if_version(&self, key: &str, expected: Version) -> StoreResult<WriteOutcome>;
    /// Positive TTL; missing is false. Changes version when ConditionalKv exists.
    /// Lease renewal requiring an exact holder uses CAS with Expiry::After instead.
    async fn expire(&self, key: &str, ttl: Duration) -> StoreResult<bool>;
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl>;
}
