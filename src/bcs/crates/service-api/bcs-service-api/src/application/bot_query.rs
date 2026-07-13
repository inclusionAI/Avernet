//! Bot query and discovery use-case contracts.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{ActorKind, ActorStatus, BotCapabilities, DynamicStatusResponse, ServiceError};

use super::bot_management::BotUseCaseError;

/// Request for listing bots visible to a caller.
#[derive(Debug, Clone, Default)]
pub struct BotListCommand {
    pub caller_actor_id: Option<String>,
    pub offset: u64,
    pub limit: u64,
    pub onboarded: Option<bool>,
}

/// Bot summary shape returned by bot list endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotListEntry {
    pub bot_uuid: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub capabilities: BotCapabilities,
    pub status: ActorStatus,
    pub visibility: String,
    pub owner_actor_id: Option<String>,
    pub created_by: Option<String>,
}

/// Response payload for listing bots.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotListResult {
    pub bots: Vec<BotListEntry>,
    pub offset: u64,
    pub limit: u64,
    pub total: u64,
}

#[derive(Debug, Clone, Default)]
pub struct BotPagedListCommand {
    pub user_id: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct MyBotsCommand {
    pub staff_no: String,
    pub offset: u64,
    pub limit: u64,
    pub active_only: bool,
}

#[derive(Debug, Clone, Default)]
pub struct BotQueryByIdsCommand {
    pub bot_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotQueryEntry {
    pub bot_uuid: String,
    pub capabilities: BotCapabilities,
    pub visibility: String,
    pub status: ActorStatus,
    pub actor_kind: ActorKind,
    pub env: Option<String>,
    pub dynamic_status: DynamicStatusResponse,
    pub created_by: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotPagedListResult {
    pub items: Vec<BotQueryEntry>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotQueryByIdsResult {
    pub bots: Vec<BotQueryEntry>,
}

#[derive(Debug, Clone, Default)]
pub struct BotDiscoveryCommand {
    pub q: Option<String>,
    pub name: Option<String>,
    pub skills: Option<String>,
    pub domains: Option<String>,
    pub scopes: Option<String>,
    pub visibility: Option<String>,
    pub collaborate_bot: Option<String>,
    pub requester_bot_id: Option<String>,
    pub organization_code: Option<String>,
    pub role: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OrganizationMemberSummary {
    pub organization_code: String,
    pub role: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotDiscoveryEntry {
    pub bot_uuid: String,
    pub capabilities: BotCapabilities,
    pub visibility: String,
    pub is_friend: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_info: Option<BotDiscoveryProviderInfo>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub organization_member: Option<OrganizationMemberSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BotDiscoveryProviderInfo {
    pub provider_id: String,
    pub provider_name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotDiscoveryResult {
    pub bots: Vec<BotDiscoveryEntry>,
    pub count: usize,
}

/// Request for loading one bot detail view.
#[derive(Debug, Clone)]
pub struct BotDetailCommand {
    pub caller_actor_id: Option<String>,
    pub bot_id: String,
}

/// Bot detail shape returned by bot detail endpoints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotDetailResult {
    pub bot_uuid: String,
    pub capabilities: BotCapabilities,
    pub status: ActorStatus,
    pub visibility: String,
    pub owner_actor_id: Option<String>,
    pub created_by: Option<String>,
    pub actor_kind: ActorKind,
    pub env: Option<String>,
    pub dynamic_status: DynamicStatusResponse,
}

/// Request for loading a bot's visibility view.
#[derive(Debug, Clone)]
pub struct BotVisibilityQueryCommand {
    pub caller_actor_id: Option<String>,
    pub bot_id: String,
}

/// Response payload for a bot visibility query.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotVisibilityQueryResult {
    pub bot_uuid: String,
    pub visibility: String,
}

/// Bot query application service.
#[async_trait]
pub trait BotQueryService: Send + Sync {
    async fn list_bots(&self, command: BotListCommand) -> Result<BotListResult, BotUseCaseError>;

    async fn get_bot(&self, command: BotDetailCommand) -> Result<BotDetailResult, BotUseCaseError>;

    /// List every bot created by the given staff_no (owner). Used by delivery
    /// adapters to resolve a human caller's owned bots without touching the
    /// core registry directly.
    async fn list_bots_by_creator(
        &self,
        _staff_no: &str,
    ) -> Result<Vec<BotListEntry>, BotUseCaseError> {
        Err(bot_query_not_configured().into())
    }

    async fn get_visibility(
        &self,
        command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, BotUseCaseError>;

    async fn list_bots_paged(
        &self,
        _command: BotPagedListCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        Err(bot_query_not_configured().into())
    }

    async fn list_my_bots(
        &self,
        _command: MyBotsCommand,
    ) -> Result<BotPagedListResult, BotUseCaseError> {
        Err(bot_query_not_configured().into())
    }

    async fn query_bots_by_ids(
        &self,
        _command: BotQueryByIdsCommand,
    ) -> Result<BotQueryByIdsResult, BotUseCaseError> {
        Err(bot_query_not_configured().into())
    }
}

#[async_trait]
pub trait BotDiscoveryService: Send + Sync {
    async fn discover_bots(
        &self,
        command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, BotUseCaseError>;
}

fn bot_query_not_configured() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "bot query service is not configured".to_string(),
        request_id: None,
    }
}
