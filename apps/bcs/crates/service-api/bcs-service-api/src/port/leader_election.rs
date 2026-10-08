use async_trait::async_trait;

use crate::ServiceResult;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LeaderStatus {
    Leader,
    Follower,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaderInfo {
    pub node_id: String,
    pub elected_at_ms: u64,
}

#[async_trait]
pub trait LeaderElectionPort: Send + Sync {
    async fn campaign(&self) -> ServiceResult<LeaderStatus>;
    async fn is_leader(&self) -> ServiceResult<bool>;
    async fn current_leader(&self) -> ServiceResult<Option<LeaderInfo>>;
}
