use super::*;
use async_trait::async_trait;
use bcs_store_api::kv::{KvStorePlugin, VersionedKvStorePlugin};

impl Model {
    fn write_kv(
        &self,
        key: &str,
        value: &[u8],
        condition: WriteCondition,
        e: Expiry,
    ) -> StoreResult<WriteOutcome> {
        expiry(None, e, self.time())?;
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        let condition = if self.broken_cas && matches!(condition, WriteCondition::Version(_)) {
            WriteCondition::Any
        } else {
            condition
        };
        let Some(v) = s.next(key, condition, s.kv.contains_key(key))? else {
            return Ok(WriteOutcome::ConditionNotMet);
        };
        let expires = expiry(s.kv.get(key).and_then(|x| x.expires), e, self.time())?;
        s.kv.insert(
            key.into(),
            Record {
                value: value.into(),
                expires,
            },
        );
        s.versions.insert(key.into(), v);
        self.finish(WriteOutcome::Applied { version: v })
    }
}
#[async_trait]
impl KvStorePlugin for Model {
    async fn get(&self, key: &str) -> StoreResult<Option<Vec<u8>>> {
        self.require(StoreCapability::Kv)?;
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        Ok(s.kv.get(key).map(|x| x.value.clone()))
    }
    async fn mget(&self, keys: &[String]) -> StoreResult<Vec<Option<Vec<u8>>>> {
        self.require(StoreCapability::Kv)?;
        let mut out = Vec::new();
        for k in keys {
            out.push(self.get(k).await?);
        }
        Ok(out)
    }
    async fn set(&self, key: &str, value: &[u8], e: Expiry) -> StoreResult<()> {
        self.require(StoreCapability::Kv)?;
        self.write_kv(key, value, WriteCondition::Any, e)?;
        Ok(())
    }
    async fn put_if_absent(&self, key: &str, value: &[u8], e: Expiry) -> StoreResult<bool> {
        self.require(StoreCapability::Kv)?;
        Ok(matches!(
            self.write_kv(key, value, WriteCondition::Absent, e)?,
            WriteOutcome::Applied { .. }
        ))
    }
    async fn delete(&self, key: &str) -> StoreResult<bool> {
        self.require(StoreCapability::Kv)?;
        self.remove_kv(key)
    }
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl> {
        self.require(StoreCapability::Kv)?;
        self.kv_ttl(key)
    }
    async fn expire(&self, key: &str, ttl: Duration) -> StoreResult<bool> {
        self.require(StoreCapability::Kv)?;
        let deadline = expiry(None, Expiry::After(ttl), self.time())?;
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        if !s.kv.contains_key(key) {
            return Ok(false);
        }
        let v = s.next(key, WriteCondition::Any, true)?.unwrap();
        s.kv.get_mut(key).unwrap().expires = deadline;
        s.versions.insert(key.into(), v);
        self.finish(true)
    }
}
impl Model {
    fn remove_kv(&self, key: &str) -> StoreResult<bool> {
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        if !s.kv.contains_key(key) {
            return Ok(false);
        }
        let v = s.next(key, WriteCondition::Any, true)?.unwrap();
        s.kv.remove(key);
        if self.reset_kv_history {
            s.versions.remove(key);
        } else {
            s.versions.insert(key.into(), v);
        }
        self.finish(true)
    }
    fn kv_ttl(&self, key: &str) -> StoreResult<KeyTtl> {
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        Ok(match s.kv.get(key) {
            None => KeyTtl::Missing,
            Some(r) => match r.expires {
                None => KeyTtl::Persistent,
                Some(t) => KeyTtl::ExpiresIn(Duration::from_millis(t - self.time())),
            },
        })
    }
}

#[async_trait]
impl VersionedKvStorePlugin for Model {
    async fn create_versioned(
        &self,
        key: &str,
        value: &[u8],
        e: Expiry,
    ) -> StoreResult<WriteOutcome> {
        self.require(StoreCapability::ConditionalKv)?;
        self.versioned_expiry(e)?;
        self.write_kv(key, value, WriteCondition::Absent, e)
    }
    async fn get_versioned(&self, key: &str) -> StoreResult<Option<Versioned<Vec<u8>>>> {
        self.require(StoreCapability::ConditionalKv)?;
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        Ok(s.kv.get(key).map(|x| Versioned {
            value: x.value.clone(),
            version: s.versions[key],
        }))
    }
    async fn compare_and_set(
        &self,
        key: &str,
        v: Version,
        value: &[u8],
        e: Expiry,
    ) -> StoreResult<WriteOutcome> {
        self.require(StoreCapability::ConditionalKv)?;
        self.versioned_expiry(e)?;
        self.write_kv(key, value, WriteCondition::Version(v), e)
    }
    async fn set_if_newer(
        &self,
        key: &str,
        v: Version,
        value: &[u8],
        e: Expiry,
    ) -> StoreResult<WriteOutcome> {
        self.require(StoreCapability::MonotonicKv)?;
        self.versioned_expiry(e)?;
        self.write_kv(key, value, WriteCondition::Newer(v), e)
    }
    async fn delete(&self, key: &str) -> StoreResult<bool> {
        self.require(StoreCapability::ConditionalKv)?;
        self.remove_kv(key)
    }
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl> {
        self.require(StoreCapability::ConditionalKv)?;
        self.kv_ttl(key)
    }
    async fn delete_if_version(&self, key: &str, expected: Version) -> StoreResult<bool> {
        self.require(StoreCapability::VersionedKvConditionalDelete)?;
        if expected.0 == 0 {
            return Err(invalid("zero version"));
        }
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        let condition = if self.broken_kv_delete {
            WriteCondition::Any
        } else {
            WriteCondition::Version(expected)
        };
        let Some(v) = s.next(key, condition, s.kv.contains_key(key))? else {
            return Ok(false);
        };
        if s.kv.remove(key).is_none() {
            return Ok(false);
        }
        if self.reset_kv_history {
            s.versions.remove(key);
        } else {
            s.versions.insert(key.into(), v);
        }
        self.finish(true)
    }
    async fn expire_if_version(
        &self,
        key: &str,
        expected: Version,
        ttl: Duration,
    ) -> StoreResult<WriteOutcome> {
        self.require(StoreCapability::VersionedKvTtl)?;
        let deadline = expiry(None, Expiry::After(ttl), self.time())?;
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        let Some(v) = s.next(
            key,
            WriteCondition::Version(expected),
            s.kv.contains_key(key),
        )?
        else {
            if self.broken_kv_ttl {
                if let Some(r) = s.kv.get_mut(key) {
                    r.expires = deadline;
                }
            }
            return Ok(WriteOutcome::ConditionNotMet);
        };
        s.kv.get_mut(key).unwrap().expires = deadline;
        s.versions.insert(key.into(), v);
        self.finish(WriteOutcome::Applied { version: v })
    }
}
impl Model {
    fn versioned_expiry(&self, expiry: Expiry) -> StoreResult<()> {
        if expiry != Expiry::Persistent {
            self.require(StoreCapability::VersionedKvTtl)?;
        }
        Ok(())
    }
}
