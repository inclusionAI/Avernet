use super::*;
use async_trait::async_trait;
use bcs_store_api::{
    document::DocumentStorePlugin,
    json::{JsonMutation, JsonStorePlugin},
    lease::*,
};

#[async_trait]
impl DocumentStorePlugin for Model {
    async fn read_document(&self, key: &str) -> StoreResult<Option<Versioned<Value>>> {
        self.get_json(key).await
    }
    async fn create_document(&self, key: &str, value: &Value) -> StoreResult<WriteOutcome> {
        self.mutate_json(
            key,
            WriteCondition::Absent,
            JsonMutation::Replace(value.clone()),
            Expiry::Persistent,
        )
        .await
    }
    async fn replace_document(
        &self,
        key: &str,
        expected: Version,
        value: &Value,
    ) -> StoreResult<WriteOutcome> {
        self.mutate_json(
            key,
            WriteCondition::Version(expected),
            JsonMutation::Replace(value.clone()),
            Expiry::Persistent,
        )
        .await
    }
}
#[derive(Clone)]
pub(super) struct LeaseRecord {
    initial: Vec<u8>,
    value: Vec<u8>,
    version: Version,
    deadline: u64,
    terminated: bool,
}
impl LeaseRecord {
    fn expire(&mut self, now: u64) {
        self.terminated |= now >= self.deadline;
    }
    fn snapshot(&self, now: u64) -> LeaseSnapshot {
        LeaseSnapshot {
            value: self.value.clone(),
            version: self.version,
            remaining: Duration::from_millis(self.deadline.saturating_sub(now)),
        }
    }
}
fn deadline(ttl: Duration, now: u64) -> StoreResult<u64> {
    expiry(None, Expiry::After(ttl), now)?.ok_or_else(|| invalid("missing deadline"))
}
#[async_trait]
impl LeaseStorePlugin for Model {
    async fn register_lease(
        &self,
        key: &str,
        value: &[u8],
        ttl: Duration,
    ) -> StoreResult<LeaseRegistration> {
        let mut state = self.state.lock().unwrap();
        let now = self.time();
        let end = deadline(ttl, now)?;
        let record = state
            .leases
            .entry(key.into())
            .or_insert_with(|| LeaseRecord {
                initial: value.into(),
                value: value.into(),
                version: Version(1),
                deadline: end,
                terminated: false,
            });
        record.expire(now);
        self.finish(if record.terminated {
            LeaseRegistration::Terminated
        } else if record.initial != value {
            LeaseRegistration::ContentConflict
        } else {
            LeaseRegistration::Active(record.snapshot(now))
        })
    }
    async fn read_lease(&self, key: &str) -> StoreResult<LeaseState> {
        let mut state = self.state.lock().unwrap();
        let now = self.time();
        Ok(match state.leases.get_mut(key) {
            None => LeaseState::Absent,
            Some(record) => {
                record.expire(now);
                if record.terminated {
                    LeaseState::Terminated
                } else {
                    LeaseState::Alive(record.snapshot(now))
                }
            }
        })
    }
    async fn update_lease(
        &self,
        key: &str,
        expected: Version,
        value: &[u8],
        ttl: Option<Duration>,
    ) -> StoreResult<LeaseWrite> {
        let mut state = self.state.lock().unwrap();
        let now = self.time();
        let end = ttl.map(|ttl| deadline(ttl, now)).transpose()?;
        let Some(record) = state.leases.get_mut(key) else {
            return Ok(LeaseWrite::ConditionNotMet);
        };
        record.expire(now);
        if record.terminated {
            return Ok(LeaseWrite::Terminated);
        }
        if record.version != expected {
            return Ok(LeaseWrite::ConditionNotMet);
        }
        let version = record
            .version
            .0
            .checked_add(1)
            .ok_or(StoreError::VersionExhausted)?;
        record.version = Version(version);
        record.value = value.into();
        if let Some(end) = end {
            record.deadline = end;
        }
        self.finish(LeaseWrite::Applied(record.snapshot(now)))
    }
    async fn retire_lease(&self, key: &str) -> StoreResult<()> {
        let mut state = self.state.lock().unwrap();
        state
            .leases
            .entry(key.into())
            .or_insert_with(|| LeaseRecord {
                initial: vec![],
                value: vec![],
                version: Version(1),
                deadline: 0,
                terminated: true,
            })
            .terminated = true;
        self.finish(())
    }
}
