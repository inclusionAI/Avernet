//! Versioned Bot control-plane application contract for BCN OpenAPI v1.

use std::collections::BTreeMap;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use super::{ApplicationError, AuthenticatedCaller, FriendCheckInStrategy, Page, UserVisibility};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotKind {
    Bot,
    Human,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotVisibility {
    Public,
    Protected,
    Private,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotStatus {
    Online,
    Hidden,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotReachability {
    Reachable,
    Unreachable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BotCandidatePurpose {
    #[default]
    Discovery,
    Collaboration,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotSkill {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotDescriptor {
    pub summary: String,
    pub domains: Vec<String>,
    pub skills: Vec<BotSkill>,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotProvider {
    pub provider_id: String,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PhysicalBot {
    pub bot_id: String,
    pub kind: BotKind,
    pub name: String,
    pub visibility: BotVisibility,
    pub status: BotStatus,
    pub env: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,
    pub descriptor: BotDescriptor,
    pub reachability: BotReachability,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider: Option<BotProvider>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,
    pub task_claim_mode: bool,
    pub task_dream_mode: bool,
    pub user_visibility: UserVisibility,
    #[serde(default)]
    pub friend_ext: Map<String, Value>,
    pub friend_check_in_strategy: FriendCheckInStrategy,
    pub created_at: u64,
    pub updated_at: u64,
    /// Mine-page sampled authority relation (spec §8
    /// mine row: 逐项 `access_relation=owner|manager`) of
    /// the calling Human on THIS row at the sampling
    /// moment: `owner` | `manager`. Present only on the
    /// mine projection; every other projection omits the
    /// field, and the sampled value is not an
    /// authorization result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access_relation: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HumanBot {
    pub bot_id: String,
    pub kind: BotKind,
    pub name: String,
    pub visibility: BotVisibility,
    pub status: BotStatus,
    pub env: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,
    pub user_visibility: UserVisibility,
    #[serde(default)]
    pub friend_ext: Map<String, Value>,
    pub friend_check_in_strategy: FriendCheckInStrategy,
    pub created_at: u64,
    pub updated_at: u64,
    /// Mine-page relation of the calling Human's OWN
    /// self row (spec §8 mine row: "Human 自身条目保持
    /// self 语义"): always `self` when present — a
    /// Human→Human owner edge never exists. Present
    /// only on the mine projection.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access_relation: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Bot {
    Physical(PhysicalBot),
    Human(HumanBot),
}

impl Bot {
    pub fn bot_id(&self) -> &str {
        match self {
            Self::Physical(bot) => &bot.bot_id,
            Self::Human(bot) => &bot.bot_id,
        }
    }

    pub fn kind(&self) -> BotKind {
        match self {
            Self::Physical(_) => BotKind::Bot,
            Self::Human(_) => BotKind::Human,
        }
    }

    /// Stamps the mine-page relation (spec §8) onto
    /// either variant; the field stays absent on every
    /// other projection.
    pub fn with_mine_access_relation(
        mut self,
        access_relation: Option<String>,
    ) -> Self {
        match &mut self {
            Self::Physical(physical) => {
                physical.access_relation = access_relation;
            }
            Self::Human(human) => {
                human.access_relation = access_relation;
            }
        }
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotCandidate {
    pub bot: PhysicalBot,
    pub is_friend: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BotCandidateSearchItem {
    pub bot: PhysicalBot,
    pub is_friend: bool,
    pub tags: BTreeMap<String, Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub score: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub short_profile: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotCandidateSearchMode {
    EmptyQuery,
    Semantic,
    NameFallback,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BotCandidateSearchResult {
    pub items: Vec<BotCandidateSearchItem>,
    pub search_mode: BotCandidateSearchMode,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotList {
    pub items: Vec<Bot>,
}

#[derive(Debug, Clone)]
pub struct ListBotCandidates {
    pub caller: AuthenticatedCaller,
    pub bot_id: String,
    pub purpose: BotCandidatePurpose,
    pub name: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct SearchBotCandidates {
    pub caller: AuthenticatedCaller,
    pub bot_id: String,
    pub purpose: BotCandidatePurpose,
    pub query: Option<String>,
}

#[derive(Debug, Clone)]
pub struct QueryBots {
    pub caller: AuthenticatedCaller,
    pub bot_ids: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct GetBot {
    pub caller: AuthenticatedCaller,
    pub bot_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BotDescriptorPatch {
    pub summary: Option<String>,
    pub domains: Option<Vec<String>>,
    pub skills: Option<Vec<BotSkill>>,
    pub scopes: Option<Vec<String>>,
}

impl BotDescriptorPatch {
    pub fn is_empty(&self) -> bool {
        self.summary.is_none()
            && self.domains.is_none()
            && self.skills.is_none()
            && self.scopes.is_none()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct BotPatch {
    pub name: Option<String>,
    pub visibility: Option<BotVisibility>,
    pub status: Option<BotStatus>,
    pub descriptor: Option<BotDescriptorPatch>,
    pub task_claim_mode: Option<bool>,
    pub task_dream_mode: Option<bool>,
    pub user_visibility: Option<UserVisibility>,
    pub friend_ext: Option<Map<String, Value>>,
    pub friend_check_in_strategy: Option<FriendCheckInStrategy>,
}

impl BotPatch {
    pub fn is_empty(&self) -> bool {
        self.name.is_none()
            && self.visibility.is_none()
            && self.status.is_none()
            && self.descriptor.is_none()
            && self.task_claim_mode.is_none()
            && self.task_dream_mode.is_none()
            && self.user_visibility.is_none()
            && self.friend_ext.is_none()
            && self.friend_check_in_strategy.is_none()
    }
}

#[derive(Debug, Clone)]
pub struct UpdateBot {
    pub caller: AuthenticatedCaller,
    pub bot_id: String,
    pub patch: BotPatch,
}

#[derive(Debug, Clone)]
pub struct ListMyBots {
    pub caller: AuthenticatedCaller,
    pub kind: Option<BotKind>,
    pub name: Option<String>,
    pub status: Option<BotStatus>,
    pub reachability: Option<BotReachability>,
    pub offset: u64,
    pub limit: u64,
}

#[async_trait]
pub trait BotService: Send + Sync {
    async fn list_candidates(
        &self,
        command: ListBotCandidates,
    ) -> Result<Page<BotCandidate>, ApplicationError>;

    async fn list_eligible_candidates(
        &self,
        command: ListBotCandidates,
    ) -> Result<Page<BotCandidate>, ApplicationError>;

    async fn search_candidates(
        &self,
        command: SearchBotCandidates,
    ) -> Result<BotCandidateSearchResult, ApplicationError>;

    async fn query(&self, command: QueryBots) -> Result<Vec<Bot>, ApplicationError>;

    async fn get(&self, query: GetBot) -> Result<Bot, ApplicationError>;

    async fn update(&self, command: UpdateBot) -> Result<Bot, ApplicationError>;

    /// Materializes the authenticated Human Actor, then lists actors owned by it.
    /// Human materialization is idempotent and does not bind physical Bot ownership.
    async fn list_mine(&self, command: ListMyBots) -> Result<Page<Bot>, ApplicationError>;
}
