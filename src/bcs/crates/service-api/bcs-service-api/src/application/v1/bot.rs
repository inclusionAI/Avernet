//! Versioned Bot control-plane application contract for BCN OpenAPI v1.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, Page, Principal};

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
    pub created_at: u64,
    pub updated_at: u64,
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
    pub created_at: u64,
    pub updated_at: u64,
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
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotCandidate {
    pub bot: PhysicalBot,
    pub is_friend: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotList {
    pub items: Vec<Bot>,
}

#[derive(Debug, Clone)]
pub struct ListBotCandidates {
    pub principal: Principal,
    pub bot_id: String,
    pub purpose: BotCandidatePurpose,
    pub name: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct QueryBots {
    pub principal: Principal,
    pub bot_ids: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct GetBot {
    pub principal: Principal,
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
}

impl BotPatch {
    pub fn is_empty(&self) -> bool {
        self.name.is_none()
            && self.visibility.is_none()
            && self.status.is_none()
            && self.descriptor.is_none()
    }
}

#[derive(Debug, Clone)]
pub struct UpdateBot {
    pub principal: Principal,
    pub bot_id: String,
    pub patch: BotPatch,
}

#[derive(Debug, Clone)]
pub struct ListMyBots {
    pub principal: Principal,
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

    async fn query(&self, command: QueryBots) -> Result<Vec<Bot>, ApplicationError>;

    async fn get(&self, query: GetBot) -> Result<Bot, ApplicationError>;

    async fn update(&self, command: UpdateBot) -> Result<Bot, ApplicationError>;

    async fn list_mine(&self, command: ListMyBots) -> Result<Page<Bot>, ApplicationError>;
}
