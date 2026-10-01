//! Local cache plugin implementations for the `bcs-cache-api` contract.
//!
//! This crate contains dependency-light implementations for local development
//! and contract tests. Internal SDK backed implementations live in separate
//! crates so they can be excluded from open-source distributions.

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_cache_api::{CacheError, CachePlugin, CacheResult, CacheSetMode, CacheTtl};

#[derive(Clone)]
enum CacheRecordKind {
    Value(Vec<u8>),
    Hash(BTreeMap<String, Vec<u8>>),
}

#[derive(Clone)]
struct CacheRecord {
    kind: CacheRecordKind,
    expires_at: Option<Instant>,
}

impl CacheRecord {
    fn is_expired(&self) -> bool {
        self.expires_at
            .is_some_and(|expires_at| Instant::now() >= expires_at)
    }
}

#[derive(Clone, Default)]
pub struct InMemoryCachePlugin {
    inner: Arc<RwLock<HashMap<String, CacheRecord>>>,
}

impl InMemoryCachePlugin {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl CachePlugin for InMemoryCachePlugin {
    async fn get_value(&self, key: &str) -> CacheResult<Option<Vec<u8>>> {
        let map = self.inner.read().await;
        let Some(record) = map.get(key) else {
            return Ok(None);
        };
        if record.is_expired() {
            return Ok(None);
        }
        match &record.kind {
            CacheRecordKind::Value(value) => Ok(Some(value.clone())),
            CacheRecordKind::Hash(_) => Err(CacheError::WrongType(key.to_string())),
        }
    }

    async fn set_value(
        &self,
        key: &str,
        value: Vec<u8>,
        ttl: Option<Duration>,
        mode: CacheSetMode,
    ) -> CacheResult<bool> {
        let mut map = self.inner.write().await;
        let exists = map.get(key).is_some_and(|record| !record.is_expired());
        let should_write = match mode {
            CacheSetMode::Upsert => true,
            CacheSetMode::InsertOnly => !exists,
            CacheSetMode::UpdateOnly => exists,
        };
        if !should_write {
            return Ok(false);
        }
        let expires_at = ttl.map(|duration| Instant::now() + duration);
        map.insert(
            key.to_string(),
            CacheRecord {
                kind: CacheRecordKind::Value(value),
                expires_at,
            },
        );
        Ok(true)
    }

    async fn delete(&self, key: &str) -> CacheResult<bool> {
        let mut map = self.inner.write().await;
        Ok(map.remove(key).is_some())
    }

    async fn expire(&self, key: &str, ttl: Duration) -> CacheResult<bool> {
        let mut map = self.inner.write().await;
        let Some(record) = map.get_mut(key) else {
            return Ok(false);
        };
        if record.is_expired() {
            map.remove(key);
            return Ok(false);
        }
        record.expires_at = Some(Instant::now() + ttl);
        Ok(true)
    }

    async fn ttl(&self, key: &str) -> CacheResult<CacheTtl> {
        let map = self.inner.read().await;
        let Some(record) = map.get(key) else {
            return Ok(CacheTtl::Missing);
        };
        if record.is_expired() {
            return Ok(CacheTtl::Missing);
        }
        match record.expires_at {
            Some(expires_at) => Ok(CacheTtl::ExpiresIn(
                expires_at.saturating_duration_since(Instant::now()),
            )),
            None => Ok(CacheTtl::Persistent),
        }
    }

    async fn hash_get(&self, key: &str, field: &str) -> CacheResult<Option<Vec<u8>>> {
        let map = self.inner.read().await;
        let Some(record) = map.get(key) else {
            return Ok(None);
        };
        if record.is_expired() {
            return Ok(None);
        }
        match &record.kind {
            CacheRecordKind::Hash(fields) => Ok(fields.get(field).cloned()),
            CacheRecordKind::Value(_) => Err(CacheError::WrongType(key.to_string())),
        }
    }

    async fn hash_get_all(&self, key: &str) -> CacheResult<BTreeMap<String, Vec<u8>>> {
        let map = self.inner.read().await;
        let Some(record) = map.get(key) else {
            return Ok(BTreeMap::new());
        };
        if record.is_expired() {
            return Ok(BTreeMap::new());
        }
        match &record.kind {
            CacheRecordKind::Hash(fields) => Ok(fields.clone()),
            CacheRecordKind::Value(_) => Err(CacheError::WrongType(key.to_string())),
        }
    }

    async fn hash_set(&self, key: &str, field: &str, value: Vec<u8>) -> CacheResult<()> {
        let mut fields = BTreeMap::new();
        fields.insert(field.to_string(), value);
        self.hash_set_many(key, fields).await
    }

    async fn hash_set_many(&self, key: &str, fields: BTreeMap<String, Vec<u8>>) -> CacheResult<()> {
        let mut map = self.inner.write().await;
        match map.get_mut(key) {
            Some(record) if !record.is_expired() => match &mut record.kind {
                CacheRecordKind::Hash(existing) => {
                    existing.extend(fields);
                    Ok(())
                }
                CacheRecordKind::Value(_) => Err(CacheError::WrongType(key.to_string())),
            },
            _ => {
                map.insert(
                    key.to_string(),
                    CacheRecord {
                        kind: CacheRecordKind::Hash(fields),
                        expires_at: None,
                    },
                );
                Ok(())
            }
        }
    }

    async fn hash_delete(&self, key: &str, field: &str) -> CacheResult<bool> {
        let mut map = self.inner.write().await;
        let Some(record) = map.get_mut(key) else {
            return Ok(false);
        };
        if record.is_expired() {
            map.remove(key);
            return Ok(false);
        }
        match &mut record.kind {
            CacheRecordKind::Hash(fields) => Ok(fields.remove(field).is_some()),
            CacheRecordKind::Value(_) => Err(CacheError::WrongType(key.to_string())),
        }
    }
}
