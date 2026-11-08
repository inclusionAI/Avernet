//! Shared Bot control-plane records and query values used by Core and repository contracts.

use std::collections::HashSet;

use bcs_domain::{ActorKind, ActorStatus, Skill};

use crate::application::v1::{BotInternalAttributes, FriendCheckInStrategy, UserVisibility};
use crate::types::bot_authority::BotAccessRelation;
use serde_json::{Map, Value};

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
    pub task_claim_mode: bool,
    pub task_dream_mode: bool,
    pub created_at: u64,
    pub updated_at: u64,
    pub user_visibility: UserVisibility,
    pub friend_ext: Map<String, Value>,
    pub friend_check_in_strategy: FriendCheckInStrategy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotCandidateVisibility {
    Discovery,
    Collaboration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BotSearchFriendshipFilter {
    #[default]
    All,
    Friends,
    NonFriends,
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
pub struct BotSearchCandidateQuery {
    pub acting_bot_id: String,
    pub env: String,
    pub visibility: BotCandidateVisibility,
    pub friend_ids: HashSet<String>,
    pub bot_uuids: Option<Vec<String>>,
    pub name: Option<String>,
    pub q: Option<String>,
    pub visibility_filter: Option<Vec<String>>,
    pub user_visibility: Option<Vec<String>>,
    pub status: Option<ActorStatus>,
    pub friendship: Option<BotSearchFriendshipFilter>,
    pub tc_bot: Option<bool>,
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

/// Query for the `list_controllable` candidate read of the mine
/// union (bot-authority spec §8.0 / plan §3 frozen contract).
///
/// `user_id` is the RAW User ID (the `created_by` convention of
/// this layer; the store matches `human_<user_id>` on the
/// authority edges). Only persistent conditions are applied:
/// one store snapshot returns the env/kind/name/status-filtered,
/// role-deduped (`owner|manager` union, owner wins), stably
/// sorted candidates INCLUDING the legal Human self entry.
///
/// There is deliberately NO reachability, offset or limit here:
/// candidate filtering and pagination are the application's §8.0
/// dynamic-algorithm concern, not a store concern.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControllableCandidateQuery {
    pub user_id: String,
    pub env: String,
    pub kind: Option<ActorKind>,
    pub name: Option<String>,
    pub status: Option<ActorStatus>,
    /// Migration-window compat (spec §10.2a): when enabled the
    /// candidate read also covers live physical Bots whose stored
    /// `created_by` byte-matches `user_id` and whose
    /// `ownership_version = 0` (uninitialized legacy rows the
    /// lazy sweep has not minted edges for yet). Strict
    /// post-migration posture turns this off so only the current
    /// edge state feeds the page.
    pub include_legacy_creator: bool,
}

/// One lightweight mine candidate: identity, bot kind, persisted
/// status and the access relation carried by the authority edges
/// (the Human self entry projects the Owner display tag without
/// any Human→Human owner edge existing; spec §8.0 point 3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControlledBotCandidate {
    pub id: String,
    pub kind: ActorKind,
    pub status: ActorStatus,
    pub access_relation: BotAccessRelation,
}

/// How to combine `task_claim_mode` / `task_dream_mode` filters in a task-mode roster query.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskModeMatch {
    /// A bot qualifies if any listed mode matches (OR).
    Any,
    /// A bot qualifies only if every listed mode matches (AND).
    All,
}

/// Roster query for physical bots by task modes and returned roster metadata.
/// Each filter is optional; omitted filters do not constrain the result. The
/// query is always scoped to the supplied environment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotTaskModesQuery {
    pub env: String,
    pub task_claim_mode: Option<bool>,
    pub task_dream_mode: Option<bool>,
    pub match_mode: TaskModeMatch,
    pub visibility: Option<String>,
    pub status: Option<ActorStatus>,
    pub user_visibility: Option<UserVisibility>,
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
    pub task_claim_mode: Option<bool>,
    pub task_dream_mode: Option<bool>,
    pub user_visibility: Option<UserVisibility>,
    pub friend_ext: Option<Map<String, Value>>,
    pub friend_check_in_strategy: Option<FriendCheckInStrategy>,
}

impl BotControlPlaneRecord {
    pub fn internal_attributes(&self) -> BotInternalAttributes {
        BotInternalAttributes {
            visibility: self.visibility.clone(),
            user_visibility: self.user_visibility,
            friend_ext: self.friend_ext.clone(),
            friend_check_in_strategy: self.friend_check_in_strategy,
        }
    }
}
