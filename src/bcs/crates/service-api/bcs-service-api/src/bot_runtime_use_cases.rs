//! Bot runtime application service contracts.
//!
//! Delivery adapters should depend on these use-case contracts instead of
//! reaching into core registry traits directly.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::{BotConnectResult, BotDeliveryTarget, BotDynamicStatus, BotUseCaseError, ServiceResult};

#[derive(Debug, Clone)]
pub struct BotRuntimeConnectCommand {
    pub caller_actor_id: Option<String>,
    pub token: Option<String>,
    pub bot_id: Option<String>,
    pub protocol_version: Option<u32>,
    pub client_kind: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotRuntimeConnectOutcome {
    pub is_new: bool,
    pub bot_uuid: String,
    pub token: String,
}

impl BotRuntimeConnectOutcome {
    pub fn from_connect_result(result: BotConnectResult) -> Self {
        Self {
            is_new: result.is_new,
            bot_uuid: result.bot_uuid,
            token: result.token,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BotRuntimeStatusCommand {
    pub caller_actor_id: Option<String>,
    pub bot_id: String,
    pub status: BotDynamicStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotRuntimeStatusOutcome {
    pub updated: bool,
    pub bot_uuid: String,
    pub status: BotDynamicStatus,
}

#[derive(Debug, Clone)]
pub struct BotRuntimeDisconnectCommand {
    pub bot_id: String,
}

#[async_trait]
pub trait BotRuntimeConnectionService: Send + Sync {
    async fn connect_streaming(
        &self,
        command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError>;

    async fn update_runtime_status(
        &self,
        command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError>;

    async fn disconnect_streaming(
        &self,
        command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError>;

    /// Return true when the bot is configured for provider downlink
    /// delivery and should no longer accept a WebSocket uplink.
    async fn is_provider_downlink_bot(&self, bot_id: &str) -> ServiceResult<bool> {
        Ok(self.resolve_delivery_target(bot_id).await?.is_http_provider())
    }

    /// Resolve the current delivery target for a bot (WebSocket or HttpProvider).
    /// Used by the WS adapter to reject reconnects after a delivery switch.
    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget>;
}
