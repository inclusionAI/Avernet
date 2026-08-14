//! Shared Bot control-plane records and query values used by Core and repository contracts.

use std::collections::HashSet;

use bcs_domain::{ActorKind, ActorStatus, Skill};

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
