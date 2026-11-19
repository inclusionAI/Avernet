use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use std::time::Duration;
use serde::{Serialize, Deserialize};

/// Claim identity, not a bearer credential. Consumers verify shard, instance,
/// epoch and claim validity against current ownership records.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShardClaim {
    pub shard: ShardId,
    pub owner: ShardOwner,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaimShard {
    pub shard: ShardId,
    pub me: Instance,
    /// None requires a never-owned shard; Some requires that exact current epoch.
    pub expected_epoch: Option<WorkerEpoch>,
    pub request_id: RequestId,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseClaim {
    Claimed(ShardClaim),
    AlreadyClaimed(ShardClaim),
    Conflict,
}

#[async_trait]
pub trait ShardLeasePort: Send + Sync {
    /// Atomically validate takeover eligibility and expected_epoch, then assign
    /// the next epoch (1 for a never-owned shard). Never delete/reset epoch history;
    /// exhaustion is Invalid, not wraparound. Unknown liveness is not eligibility.
    /// Retrying while the same claim record is retained confirms it without
    /// advancing epoch. Once superseded, stale requests conflict; historical
    /// receipts are not retained indefinitely.
    /// Claim does not atomically seal every session in the shard.
    async fn claim(&self, request: ClaimShard) -> Result<LeaseClaim, StoreError>;
    async fn current(&self, shard: ShardId) -> Result<Option<ShardRecord>, StoreError>;
    /// Receiver commits the exact source -> target transition. Same ID with
    /// different content is invalid. Existing handoff must be cleared first.
    async fn handoff(&self, request: HandoffRequest) -> Result<HandoffOutcome, StoreError>;
    /// Record only for the exact current target claim and handoff identity.
    async fn source_done(&self, target: &ShardClaim, id: &RequestId) -> Result<bool, StoreError>;
    /// Current owner only; source_done, stable Dead, or its original local timer
    /// must allow cleanup. Lost local timer is expired, never restarted.
    async fn clear_handoff(&self, target: &ShardClaim, id: &RequestId) -> Result<bool, StoreError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RuntimeRole {
    Gateway,
    Worker,
    Connector,
}

/// Identifies one registration lifetime. Validity is checked against stored
/// membership; constructing this value does not register or revive an instance.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MembershipLease {
    pub instance: Instance,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Liveness {
    Alive,
    Dead,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Renewal {
    Renewed,
    Lost,
}

#[async_trait]
pub trait MembershipPort: Send + Sync {
    /// Bind the exact instance and role to a live registration. An identical
    /// active-registration retry is idempotent; changing that instance's role is
    /// invalid. Never reuse an expired/retired incarnation: registration after
    /// loss requires a fresh instance identity.
    async fn register(
        &self,
        instance: Instance,
        role: RuntimeRole,
        control_address: String,
        ttl: Duration,
    ) -> Result<MembershipLease, StoreError>;
    /// Atomically require this exact current, unexpired registration. Lost leases
    /// return Lost without creating/reviving membership or touching a newer incarnation.
    async fn renew(&self, lease: &MembershipLease, ttl: Duration) -> Result<Renewal, StoreError>;
    /// Same exact-instance/valid-lease check as renew; never mutate a newer incarnation.
    async fn set_draining(&self, lease: &MembershipLease) -> Result<Renewal, StoreError>;
    /// Uncertain reads produce Unknown, never Dead.
    /// Stable terminal evidence for this exact incarnation, including a pending
    /// registration; delayed register/renew cannot undo a successful retirement.
    async fn retire(&self, lease: &MembershipLease) -> Result<(), StoreError>;
    async fn liveness(&self, instance: &Instance) -> Result<Liveness, StoreError>;
    async fn members(
        &self,
        role: RuntimeRole,
        page: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError>;
}


#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemberRecord {
    pub instance: Instance,
    pub role: RuntimeRole,
    pub control_address: String,
    pub draining: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShardHandoff {
    pub request_id: RequestId,
    pub from: ShardClaim,
    /// Diagnostic wall time only; never used to authorize takeover.
    pub deadline: TimestampMillis,
    pub source_done: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ShardRecord {
    pub claim: ShardClaim,
    pub handoff: Option<ShardHandoff>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HandoffRequest {
    pub request_id: RequestId,
    pub from: ShardClaim,
    pub target: Instance,
    /// Remaining source budget, not a remote wall-clock deadline.
    pub grace: Duration,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum HandoffOutcome { Accepted(ShardRecord), Rejected, Fenced }
