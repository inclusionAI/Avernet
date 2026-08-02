//! Narrow persistence contract for V1 Bot control-plane reads and updates.

use std::collections::HashSet;

use async_trait::async_trait;
use bcs_domain::{ActorKind, ActorStatus, Skill};

use crate::ServiceResult;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneDescriptor {
    pub summary: String,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneRecord {
    pub bot_id: String,
    pub kind: ActorKind,
    pub name: String,
    pub visibility: String,
    pub status: ActorStatus,
    pub env: String,
    pub created_by: Option<String>,
    pub descriptor: BotControlPlaneDescriptor,
    pub agent_code: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotCandidateVisibility {
    Discovery,
    Collaboration,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotCandidateReadQuery {
    pub acting_bot_id: String,
    pub env: String,
    pub visibility: BotCandidateVisibility,
    pub friend_ids: HashSet<String>,
    pub name: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotCandidateReadRecord {
    pub bot: BotControlPlaneRecord,
    pub is_friend: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneOwnedQuery {
    pub created_by: String,
    pub env: String,
    pub kind: Option<ActorKind>,
    pub name: Option<String>,
    pub status: Option<ActorStatus>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BotControlPlaneDescriptorPatch {
    pub summary: Option<String>,
    pub domains: Option<Vec<String>>,
    pub skills: Option<Vec<Skill>>,
    pub scopes: Option<Vec<String>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BotControlPlanePatch {
    pub name: Option<String>,
    pub visibility: Option<String>,
    pub status: Option<ActorStatus>,
    pub descriptor: Option<BotControlPlaneDescriptorPatch>,
}

#[async_trait]
pub trait BotControlPlaneRepoPort: Send + Sync {
    async fn get_control_plane(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;

    async fn get_control_plane_by_ids(
        &self,
        bot_ids: &[String],
        env: &str,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let mut seen = HashSet::new();
        let mut records = Vec::new();
        for bot_id in bot_ids {
            if seen.insert(bot_id.as_str()) {
                if let Some(record) = self.get_control_plane(bot_id, env).await? {
                    records.push(record);
                }
            }
        }
        Ok(records)
    }

    async fn list_control_plane_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)>;

    async fn list_control_plane_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>>;

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>>;
}
