//! CAS-confirmed membership termination with a bounded garbage-retention window.
use crate::*;
use async_trait::async_trait;
use std::time::Duration;

/// Storage garbage-retention period, independent of the short heartbeat timeout.
/// Successful registration, updates and first termination refresh this period.
/// Old requests/evidence outside this window are unsupported; return with a new
/// incarnation and acquire fresh ownership. Reads do not keep garbage alive.
pub const LEASE_RETENTION: Duration = Duration::from_secs(30 * 24 * 60 * 60);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseSnapshot {
    pub value: Vec<u8>,
    pub version: Version,
    /// Remaining heartbeat budget at the provider's time sample, not an atomic
    /// time-and-write guarantee. Subtract the full request duration; zero grants
    /// no local execution budget. Only committed termination proves Dead.
    pub remaining: Duration,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseState {
    /// No retained record: never registered, still in flight, or garbage-collected
    /// after the retention window. Absence alone is not evidence of death.
    Absent,
    Alive(LeaseSnapshot),
    /// Confirmed terminal evidence after a conditional timeout decision or
    /// explicit retirement, including retire-before-register. It cannot be
    /// reversed during LEASE_RETENTION; later GC may return Absent.
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

/// Each key identifies one incarnation. Returning workers use fresh keys.
/// A heartbeat timeout permits an exact-version termination attempt; it is NOT
/// itself Dead. Heartbeat and termination updates share one atomic record/version.
/// A heartbeat may win after timeout but before termination commits. Once terminal,
/// register/update cannot revive the record inside the 30-day retention window.
/// A synchronized client wall clock may supply the time sample; deployments must
/// budget clock skew/steps in heartbeat timeouts. Local elapsed-time budgets use
/// a monotonic clock. No server TIME, script or atomic time predicate is required. GC TTL refresh may be separate from CAS;
/// partial success is OutcomeUnknown, and missing retention is repaired on access.
/// Missing/failed reads are NOT death. Old requests/evidence after retention are
/// outside the recovery guarantee. No ordinary writes or premature deletion are
/// allowed inside this window. Providers bound contention retries.
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
    /// Observe the record; if timed out, CAS its observed version to Terminated.
    /// Re-read on a heartbeat conflict; never return Dead from elapsed time alone.
    /// Uncertain termination writes remain errors, not Absent or Terminated.
    async fn read_lease(&self, key: &str) -> StoreResult<LeaseState>;
    /// Requires this exact version AND a non-terminated retained record. A timed-out
    /// record may still renew until a competing termination commits. None preserves
    /// the heartbeat deadline; Some sets it from sampled provider time. Never creates.
    /// Positive ttl <= LEASE_RETENTION; no version wrap. Reserve termination capacity
    /// at version exhaustion. Successful changes also refresh the GC retention.
    async fn update_lease(
        &self,
        key: &str,
        expected: Version,
        value: &[u8],
        ttl: Option<Duration>,
    ) -> StoreResult<LeaseWrite>;
    /// Idempotently terminate this exact key, even before a delayed registration
    /// arrives. Success proves termination within retention; unknown does not.
    /// Repeated retirement does not restart an already established retention period.
    async fn retire_lease(&self, key: &str) -> StoreResult<()>;
}
