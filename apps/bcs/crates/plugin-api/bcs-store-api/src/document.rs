//! Minimal persistent document CAS, independently injectable from AtomicJson.
use crate::*;
use async_trait::async_trait;
use serde_json::Value;

/// A namespace of persistent documents. All writers must use this contract;
/// deletion, expiry, eviction and rollback are forbidden for its lifetime.
/// Storage metadata is private, never reserved in the logical business value.
/// No cross-key atomicity is implied. Unsupported providers reject before I/O.
#[async_trait]
pub trait DocumentStorePlugin: StorePlugin {
    /// Consistent value and version from one document. Missing is not proof of
    /// never having existed; lifecycle violations need incident recovery.
    async fn read_document(&self, key: &str) -> StoreResult<Option<Versioned<Value>>>;
    /// Atomically create only if absent, with a provider-chosen positive version.
    /// Existing content is never overwritten, even if logically identical.
    async fn create_document(&self, key: &str, value: &Value) -> StoreResult<WriteOutcome>;
    /// Require an existing document at exactly expected, replace its entire
    /// logical value and advance the private version in ONE atomic operation.
    /// Field removal, real JSON null, arrays and scalars preserve normal JSON
    /// semantics. A rejected attempt changes neither value, version nor TTL.
    /// Missing is ConditionNotMet, never creation. Exhaustion cannot wrap.
    async fn replace_document(
        &self,
        key: &str,
        expected: Version,
        value: &Value,
    ) -> StoreResult<WriteOutcome>;
}
