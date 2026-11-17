use crate::*;
use async_trait::async_trait;
use std::time::Duration;

/// Byte-valued keys. Strings are opaque UTF-8 keys, not business identifiers.
/// Missing and expired values return None; errors never become cache misses.
/// Do not mix ordinary and versioned operations on one logical key. Separate
/// traits restrict injected operations, not physical key namespaces. No eviction
/// of live data is allowed. Single-key writes are atomic; mget is NOT a snapshot.
#[async_trait]
pub trait KvStorePlugin: StorePlugin {
    async fn get(&self, key: &str) -> StoreResult<Option<Vec<u8>>>;
    /// Same length/order as keys, including duplicates and missing entries.
    /// Bounded by provider limits. Cross-partition batching may use several calls.
    async fn mget(&self, keys: &[String]) -> StoreResult<Vec<Option<Vec<u8>>>>;
    async fn set(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<()>;
    /// Atomic existence check + value + expiry; true only for this creation.
    async fn put_if_absent(&self, key: &str, value: &[u8], expiry: Expiry) -> StoreResult<bool>;
    async fn delete(&self, key: &str) -> StoreResult<bool>;
    /// Positive TTL; missing is false.
    async fn expire(&self, key: &str, ttl: Duration) -> StoreResult<bool>;
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl>;
}

/// Independently injectable versioned KV. ConditionalKv covers creation, reads,
/// CAS, unconditional deletion and TTL queries. Without VersionedKvTtl only
/// Expiry::Persistent is supported; other modes fail Unsupported before I/O.
/// With it, the condition, content, version and expiry change are one atomic
/// operation. A failed condition changes neither content nor expiry.
/// Missing/expired reads return None; errors never become misses. Live values
/// must not be evicted. Uncertain deletes/renewals use OutcomeUnknown, not false.
///
/// A key missing/expired at the atomic check is never recreated by CAS or renew.
/// Without MonotonicKv, deletion/recreation/expiry can reuse versions: callers
/// must prevent old requests from acting on a reused key. Even conditional
/// deletion alone does not prevent this cross-incarnation ABA problem.
///
/// Ordinary writes are intentionally unavailable through this interface:
/// ```compile_fail
/// use bcs_store_api::{kv::{KvStorePlugin, VersionedKvStorePlugin}, Expiry};
/// fn overwrite(store: &dyn VersionedKvStorePlugin) {
///     store.set("key", b"value", Expiry::Persistent);
/// }
/// ```
#[async_trait]
pub trait VersionedKvStorePlugin: StorePlugin {
    /// Atomically create only if absent. Never overwrite existing data/expiry.
    async fn create_versioned(
        &self,
        key: &str,
        value: &[u8],
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome>;
    /// Content and version from one consistent read; requires ConditionalKv.
    async fn get_versioned(&self, key: &str) -> StoreResult<Option<Versioned<Vec<u8>>>>;
    /// Update a live key only at expected. Successful identical writes also
    /// advance version. Zero versions and invalid expiry fail before mutation.
    async fn compare_and_set(
        &self,
        key: &str,
        expected: Version,
        value: &[u8],
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome>;
    /// MonotonicKv: caller's logical version, strictly above retained history.
    async fn set_if_newer(
        &self,
        key: &str,
        version: Version,
        value: &[u8],
        expiry: Expiry,
    ) -> StoreResult<WriteOutcome>;
    /// Unconditional deletion: true only when THIS attempt removed live data.
    /// Missing is false. Not an ownership fence. MonotonicKv retains history
    /// even on unconditional deletion; otherwise recreation may reuse versions.
    async fn delete(&self, key: &str) -> StoreResult<bool>;
    /// VersionedKvConditionalDelete: atomically check a positive version and
    /// delete. Missing/mismatch is false, not proof of a prior deletion. No new
    /// live-value version is returned. MonotonicKv retains history when present.
    async fn delete_if_version(&self, key: &str, expected: Version) -> StoreResult<bool>;
    /// VersionedKvTtl: atomically check expected, retain content, set a positive
    /// TTL and advance version (even for identical TTL). Missing/mismatch returns
    /// ConditionNotMet. Never implement as an unguarded expiry after CAS.
    async fn expire_if_version(
        &self,
        key: &str,
        expected: Version,
        ttl: Duration,
    ) -> StoreResult<WriteOutcome>;
    /// Read-only; available with ConditionalKv even without VersionedKvTtl.
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl>;
}
