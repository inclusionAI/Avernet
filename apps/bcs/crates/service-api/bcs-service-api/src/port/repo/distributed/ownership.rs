use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShardClaim {
    pub shard: ShardId,
    pub owner: SessionOwner,
    pub revision: Revision,
    pub authority: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaimShard {
    pub shard: ShardId,
    pub me: Instance,
    pub expected: Option<Revision>,
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
    /// Validate claim eligibility and expected revision; retries preserve request ID.
    /// Claim does not atomically seal every session in the shard.
    async fn claim(&self, request: ClaimShard) -> Result<LeaseClaim, StoreError>;
    async fn current(&self, shard: ShardId) -> Result<Option<ShardClaim>, StoreError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuntimeRole {
    Gateway,
    Worker,
    Connector,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MembershipLease {
    pub instance: Instance,
    pub authority: String,
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
    async fn register(
        &self,
        instance: Instance,
        role: RuntimeRole,
        ttl: Duration,
    ) -> Result<MembershipLease, StoreError>;
    async fn renew(&self, lease: &MembershipLease, ttl: Duration) -> Result<Renewal, StoreError>;
    async fn set_draining(&self, lease: &MembershipLease) -> Result<Renewal, StoreError>;
    /// Uncertain reads produce Unknown, never Dead.
    async fn liveness(&self, instance: &Instance) -> Result<Liveness, StoreError>;
    async fn members(
        &self,
        role: RuntimeRole,
        page: PageRequest,
    ) -> Result<Page<Instance>, StoreError>;
}
