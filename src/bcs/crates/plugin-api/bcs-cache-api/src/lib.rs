//! BCS cache plugin contract.
//!
//! This crate defines the infrastructure-facing cache extension point used by
//! BCS services. The contract intentionally stays at cache primitives:
//! byte values, hash fields, TTL, and conditional writes. Business cache keys,
//! serialization, retry policy, and fallback behavior belong to the consuming
//! service or the composition root.
//!
//! Expired keys are treated as missing. Hash writes create a persistent hash
//! when the key is missing or expired; hash TTL is managed separately through
//! [`CachePlugin::expire`].

use std::collections::BTreeMap;
use std::time::Duration;

use async_trait::async_trait;
use thiserror::Error;

/// Result type for cache plugin operations.
pub type CacheResult<T> = Result<T, CacheError>;

/// Cache plugin failures.
#[derive(Debug, Error)]
pub enum CacheError {
    /// The caller provided an invalid key, field, value, or TTL.
    #[error("invalid cache input: {0}")]
    InvalidInput(String),

    /// The operation is valid for the contract but unsupported by this backend.
    #[error("unsupported cache operation: {0}")]
    Unsupported(String),

    /// A key exists with a different value kind.
    #[error("cache key has incompatible value kind: {0}")]
    WrongType(String),

    /// Backend-specific failure.
    #[error("cache backend error: {0}")]
    Backend(String),
}

/// Result of a TTL lookup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheTtl {
    /// The key does not exist.
    Missing,
    /// The key exists and has no expiry.
    Persistent,
    /// The key exists and will expire after the duration.
    ExpiresIn(Duration),
}

/// Conditional set behavior for value writes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheSetMode {
    /// Always write the value, replacing any existing value or hash.
    Upsert,
    /// Write only when the key does not already exist.
    InsertOnly,
    /// Write only when the key already exists.
    UpdateOnly,
}

/// Cache plugin contract.
///
/// Semantics:
/// - Keys and hash fields are opaque UTF-8 strings.
/// - Values are opaque bytes; serialization is owned by the caller.
/// - Expired keys are observed as missing.
/// - `set_value` replaces any existing value or hash at the same key.
/// - Backends may round TTL values up to their supported precision. Callers
///   needing sub-second expiry should not rely on exact expiration timing.
/// - Hash methods operate on a single hash stored at `key`; using a value key
///   as a hash returns [`CacheError::WrongType`].
#[async_trait]
pub trait CachePlugin: Send + Sync + 'static {
    /// Return the value stored at `key`, or `None` when it is missing/expired.
    async fn get_value(&self, key: &str) -> CacheResult<Option<Vec<u8>>>;

    /// Return values in the same order as `keys`.
    ///
    /// The default implementation is a sequential reference implementation.
    /// Backends with native batch fetch support should override it.
    async fn get_values(&self, keys: &[String]) -> CacheResult<Vec<Option<Vec<u8>>>> {
        let mut values = Vec::with_capacity(keys.len());
        for key in keys {
            values.push(self.get_value(key).await?);
        }
        Ok(values)
    }

    /// Store a value with optional TTL and conditional mode.
    ///
    /// Returns `true` when the write happened and `false` when the condition
    /// was not met. Conditional writes should be treated as backend capability:
    /// some implementations cannot provide atomic insert/update-only writes
    /// without a TTL and may return [`CacheError::Unsupported`] or document a
    /// weaker best-effort behavior.
    async fn set_value(
        &self,
        key: &str,
        value: Vec<u8>,
        ttl: Option<Duration>,
        mode: CacheSetMode,
    ) -> CacheResult<bool>;

    /// Delete a key. Returns `true` when a key was removed.
    async fn delete(&self, key: &str) -> CacheResult<bool>;

    /// Set or replace a key TTL. Returns `false` if the key is missing.
    async fn expire(&self, key: &str, ttl: Duration) -> CacheResult<bool>;

    /// Return the current TTL state for a key.
    async fn ttl(&self, key: &str) -> CacheResult<CacheTtl>;

    /// Return one hash field from `key`.
    async fn hash_get(&self, key: &str, field: &str) -> CacheResult<Option<Vec<u8>>>;

    /// Return all hash fields from `key`.
    async fn hash_get_all(&self, key: &str) -> CacheResult<BTreeMap<String, Vec<u8>>>;

    /// Set one hash field. Creates a persistent hash when the key is missing
    /// or expired. Use [`Self::expire`] to manage hash TTL.
    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> CacheResult<()>;

    /// Set multiple hash fields. Creates a persistent hash when the key is
    /// missing or expired. Use [`Self::expire`] to manage hash TTL.
    ///
    /// Fields are written best-effort; implementations may write them one by
    /// one. Callers must not rely on multi-field atomic commit semantics.
    async fn hash_set_many(&self, key: &str, fields: BTreeMap<String, Vec<u8>>) -> CacheResult<()>;

    /// Delete one hash field. Returns `true` when a field was removed.
    async fn hash_delete(&self, key: &str, field: &str) -> CacheResult<bool>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn cache_plugin_is_object_safe() {
        fn _assert<T: CachePlugin>() {}
        fn _assert_dyn(_: Arc<dyn CachePlugin>) {}
    }
}
