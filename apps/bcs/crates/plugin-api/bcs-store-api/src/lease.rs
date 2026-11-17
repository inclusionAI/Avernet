//! Strict registration lifetimes. Ordinary TTL or document CAS is insufficient.
use crate::*;
use async_trait::async_trait;
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseSnapshot {
    pub value: Vec<u8>,
    pub version: Version,
    /// Remaining validity at the provider's linearization point. Consumers must
    /// subtract request elapsed time; receipt time must not restart this duration.
    pub remaining: Duration,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseState {
    Absent,
    Alive(LeaseSnapshot),
    Terminated,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseRegistration {
    Active(LeaseSnapshot),
    ContentConflict,
    Terminated,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseWrite {
    Applied(LeaseSnapshot),
    ConditionNotMet,
    Terminated,
}

/// Each key identifies exactly one registration lifetime and is never reused.
/// Reliable provider time, expiry checks, mutations and termination are atomic.
/// A terminal lifetime cannot be resurrected by delayed register/update calls;
/// providers retain termination evidence. Read errors are NOT termination.
#[async_trait]
pub trait LeaseStorePlugin: StorePlugin {
    /// First registration establishes its deadline; identical live retries do
    /// not extend it. The initial payload remains the retry identity after edits.
    async fn register_lease(
        &self,
        key: &str,
        value: &[u8],
        ttl: Duration,
    ) -> StoreResult<LeaseRegistration>;
    async fn read_lease(&self, key: &str) -> StoreResult<LeaseState>;
    /// Requires this exact version AND an unexpired, non-retired lifetime at
    /// mutation time. None preserves the deadline; Some extends from provider
    /// time. Missing/expired never creates. Positive ttl; no version wrap.
    async fn update_lease(
        &self,
        key: &str,
        expected: Version,
        value: &[u8],
        ttl: Option<Duration>,
    ) -> StoreResult<LeaseWrite>;
    /// Idempotently terminate this exact key, even before a delayed registration
    /// arrives. Successful return proves stable termination; unknown does not.
    async fn retire_lease(&self, key: &str) -> StoreResult<()>;
}
