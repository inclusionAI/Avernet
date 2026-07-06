//! Bot management use-case contracts.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{BotConnectResult, BotDynamicStatus, ConnectError, ServiceError};

/// Request for connecting a bot through an application use case.
#[derive(Debug, Clone)]
pub struct BotConnectCommand {
    pub caller_actor_id: Option<String>,
    pub token: Option<String>,
    pub bot_id: Option<String>,
    pub protocol_version: Option<u32>,
}

/// Request for updating a bot's dynamic status.
#[derive(Debug, Clone)]
pub struct BotStatusUpdateCommand {
    pub caller_actor_id: Option<String>,
    pub bot_id: String,
    pub status: BotDynamicStatus,
}

/// Response payload for a successful bot status update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotStatusUpdateResult {
    pub updated: bool,
    pub bot_uuid: String,
    pub status: BotDynamicStatus,
}

/// Request for updating bot visibility.
#[derive(Debug, Clone)]
pub struct BotVisibilityCommand {
    pub caller_actor_id: Option<String>,
    pub bot_id: String,
    pub visibility: String,
}

/// Response payload for a successful bot visibility update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotVisibilityResult {
    pub bot_uuid: String,
    pub visibility: String,
}

/// Request for unregistering a bot from the registry.
#[derive(Debug, Clone)]
pub struct BotLeaveCommand {
    pub caller_actor_id: Option<String>,
    pub human_actor_id: Option<String>,
    pub bot_id: String,
}

/// Response payload for a bot leave operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotLeaveResult {
    pub left: bool,
    pub bot_uuid: String,
}

/// Use-case level error with enough detail for delivery adapters to map status.
#[derive(Debug, thiserror::Error)]
pub enum BotUseCaseError {
    #[error("Unauthorized: {0}")]
    Unauthorized(String),
    #[error("Forbidden: {0}")]
    Forbidden(String),
    #[error("Invalid visibility: {0}")]
    InvalidVisibility(String),
    #[error("Invalid bot ID: {0}")]
    InvalidBotId(String),
    #[error("Invalid provider_bot_ref: {0}")]
    InvalidProviderBotRef(String),
    #[error("Provider '{0}' not found")]
    ProviderNotFound(String),
    #[error("Provider '{provider_id}' downlink not ready: {reason}")]
    ProviderNotReadyForDownlink {
        provider_id: String,
        reason: String,
    },
    #[error(
        "Bot '{bot_id}' already bound to provider '{existing_provider_id}' \
         (ref '{existing_provider_bot_ref}')"
    )]
    BotAlreadyBound {
        bot_id: String,
        existing_provider_id: String,
        existing_provider_bot_ref: String,
    },
    #[error(transparent)]
    Connect(#[from] ConnectError),
    #[error(transparent)]
    Service(#[from] ServiceError),
}

/// Request for switching a bot's delivery channel from WebSocket to
/// HttpProvider downlink.
#[derive(Debug, Clone)]
pub struct SwitchDeliveryToProviderCommand {
    pub bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub name: Option<String>,
    pub summary: Option<String>,
}

/// Response payload for a successful delivery switch.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SwitchDeliveryToProviderResult {
    pub bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub binding_created_at: u64,
    pub idempotent_replay: bool,
    pub websocket_kicked: bool,
}

/// Bot management application service.
#[async_trait]
pub trait BotManagementService: Send + Sync {
    async fn connect_bot(
        &self,
        command: BotConnectCommand,
    ) -> Result<BotConnectResult, BotUseCaseError>;

    async fn update_status(
        &self,
        command: BotStatusUpdateCommand,
    ) -> Result<BotStatusUpdateResult, BotUseCaseError>;

    async fn set_visibility(
        &self,
        command: BotVisibilityCommand,
    ) -> Result<BotVisibilityResult, BotUseCaseError>;

    async fn leave_bot(&self, command: BotLeaveCommand) -> Result<BotLeaveResult, BotUseCaseError>;

    async fn switch_delivery_to_provider(
        &self,
        command: SwitchDeliveryToProviderCommand,
    ) -> Result<SwitchDeliveryToProviderResult, BotUseCaseError>;
}
