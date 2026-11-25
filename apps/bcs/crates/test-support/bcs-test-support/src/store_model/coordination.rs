use super::*;
use async_trait::async_trait;
use bcs_store_api::{
    document::DocumentStorePlugin,
    json::{JsonMutation, JsonStorePlugin},
    lease::*,
};

#[async_trait]
impl DocumentStorePlugin for Model {
    // Inherit projection and patch to exercise the portable implementation on
    // this model's consistent read and atomic whole-document replacement.
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
    retained_until: u64,
}
impl LeaseRecord {
    fn expire(&mut self, now: u64) {
        if !self.terminated && now >= self.deadline {
            self.terminated = true;
            self.version.0 += 1;
            self.retained_until = now + LEASE_RETENTION.as_millis() as u64;
        }
    }
    fn snapshot(&self, now: u64) -> LeaseSnapshot {
        LeaseSnapshot {
            value: self.value.clone(),
            version: self.version,
            remaining: Duration::from_millis(self.deadline.saturating_sub(now)),
        }
    }
}
fn collect_lease(state: &mut State, key: &str, now: u64) {
    if state.leases.get(key).is_some_and(|r| now >= r.retained_until) {
        state.leases.remove(key);
    }
}
fn deadline(ttl: Duration, now: u64) -> StoreResult<u64> {
    if ttl > LEASE_RETENTION { return Err(invalid("lease duration exceeds retention")); }
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
        collect_lease(&mut state, key, now);
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
                retained_until: now + LEASE_RETENTION.as_millis() as u64,
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
        collect_lease(&mut state, key, now);
        let mut wrote = false;
        let result = match state.leases.get_mut(key) {
            None => LeaseState::Absent,
            Some(record) => {
                wrote = !record.terminated && now >= record.deadline;
                record.expire(now);
                if record.terminated {
                    LeaseState::Terminated
                } else {
                    LeaseState::Alive(record.snapshot(now))
                }
            }
        };
        if wrote { self.finish(result) } else { Ok(result) }
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
        collect_lease(&mut state, key, now);
        let end = ttl.map(|ttl| deadline(ttl, now)).transpose()?;
        let Some(record) = state.leases.get_mut(key) else {
            return Ok(LeaseWrite::ConditionNotMet);
        };
        if record.terminated {
            return Ok(LeaseWrite::Terminated);
        }
        if record.version != expected {
            return Ok(LeaseWrite::ConditionNotMet);
        }
        if record.version.0 >= u64::MAX - 1 { return Err(StoreError::VersionExhausted); }
        let version = record
            .version
            .0
            .checked_add(1)
            .ok_or(StoreError::VersionExhausted)?;
        record.version = Version(version);
        record.value = value.into();
        record.retained_until = now + LEASE_RETENTION.as_millis() as u64;
        if let Some(end) = end {
            record.deadline = end;
        }
        self.finish(LeaseWrite::Applied(record.snapshot(now)))
    }
    async fn retire_lease(&self, key: &str) -> StoreResult<()> {
        let mut state = self.state.lock().unwrap();
        let now = self.time();
        collect_lease(&mut state, key, now);
        let record = state.leases.entry(key.into()).or_insert_with(|| LeaseRecord {
            initial: vec![], value: vec![], version: Version(1), deadline: 0,
            terminated: true, retained_until: now + LEASE_RETENTION.as_millis() as u64,
        });
        if !record.terminated {
            record.version.0 += 1;
            record.terminated = true;
            record.retained_until = now + LEASE_RETENTION.as_millis() as u64;
        }
        self.finish(())
    }
}

impl Model {
    /// Test fixture injection only: expose the provider's final usable version
    /// without adding a version setter to the production DocumentCas contract.
    pub fn exhaust_document_version(&self, key: &str) {
        let mut state = self.state.lock().unwrap();
        assert!(state.json.contains_key(key), "seed document before exhausting its version");
        state.versions.insert(key.into(), Version(u64::MAX));
    }
    /// Test fixture injection for RetainedLease failure atomicity.
    pub fn exhaust_lease_version(&self, key: &str) {
        self.state.lock().unwrap().leases.get_mut(key)
            .expect("seed lease before exhausting its version").version = Version(u64::MAX - 1);
    }
}

impl Model {
    /// Fault injection for negative lease conformance fixtures. Does not change
    /// the version or payload, so the suite must check actual expiry behavior.
    pub fn shorten_lease_deadline(&self, key: &str, remaining: Duration) {
        let end = self.time().checked_add(remaining.as_millis() as u64).unwrap();
        let mut state = self.state.lock().unwrap();
        let lease = state.leases.get_mut(key).expect("seed lease before shortening it");
        lease.deadline = lease.deadline.min(end);
    }
}
