use super::*;
use async_trait::async_trait;
use bcs_store_api::kv::KvStorePlugin;

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
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        Ok(s.kv.get(key).map(|x| x.value.clone()))
    }
    async fn mget(&self, keys: &[String]) -> StoreResult<Vec<Option<Vec<u8>>>> {
        let mut out = Vec::new();
        for k in keys {
            out.push(self.get(k).await?);
        }
        Ok(out)
    }
    async fn set(&self, key: &str, value: &[u8], e: Expiry) -> StoreResult<()> {
        self.write_kv(key, value, WriteCondition::Any, e)?;
        Ok(())
    }
    async fn put_if_absent(&self, key: &str, value: &[u8], e: Expiry) -> StoreResult<bool> {
        Ok(matches!(
            self.write_kv(key, value, WriteCondition::Absent, e)?,
            WriteOutcome::Applied { .. }
        ))
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
        self.write_kv(key, value, WriteCondition::Version(v), e)
    }
    async fn set_if_newer(
        &self,
        key: &str,
        v: Version,
        value: &[u8],
        e: Expiry,
    ) -> StoreResult<WriteOutcome> {
        self.require(StoreCapability::ConditionalKv)?;
        self.write_kv(key, value, WriteCondition::Newer(v), e)
    }
    async fn delete(&self, key: &str) -> StoreResult<bool> {
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        if !s.kv.contains_key(key) {
            return Ok(false);
        }
        let v = s.next(key, WriteCondition::Any, true)?.unwrap();
        s.kv.remove(key);
        s.versions.insert(key.into(), v);
        Ok(true)
    }
    async fn delete_if_version(&self, key: &str, expected: Version) -> StoreResult<WriteOutcome> {
        self.require(StoreCapability::ConditionalKv)?;
        let mut s = self.state.lock().unwrap();
        s.expire_values(key, self.time());
        s.kind(key, "kv")?;
        let Some(v) = s.next(
            key,
            WriteCondition::Version(expected),
            s.kv.contains_key(key),
        )?
        else {
            return Ok(WriteOutcome::ConditionNotMet);
        };
        s.kv.remove(key);
        s.versions.insert(key.into(), v);
        Ok(WriteOutcome::Applied { version: v })
    }
    async fn expire(&self, key: &str, ttl: Duration) -> StoreResult<bool> {
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
        Ok(true)
    }
    async fn ttl(&self, key: &str) -> StoreResult<KeyTtl> {
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
