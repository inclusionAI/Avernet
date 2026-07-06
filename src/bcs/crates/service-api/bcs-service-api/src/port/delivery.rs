use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::BcsFrame;

use crate::{ServiceError, ServiceResult};

pub use bcs_protocol::{BotDeliveryKind, FrontendDeliveryKind, FrontendDeliveryTarget};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProviderTransportPreference {
    Callback,
    CallbackSse,
}

impl Default for ProviderTransportPreference {
    fn default() -> Self {
        Self::Callback
    }
}

#[derive(Debug, Clone)]
pub struct BotDeliveryCommand {
    pub target: BotDeliveryTarget,
    pub run_id: String,
    pub frame: BcsFrame,
    pub delivery_kind: BotDeliveryKind,
    pub provider_transport: ProviderTransportPreference,
}

impl BotDeliveryCommand {
    pub fn target_bot_id(&self) -> &str {
        self.target.bot_id()
    }
}

#[derive(Debug)]
pub struct BotDeliveryResult {
    pub target_bot_id: String,
    pub delivered: bool,
    pub error: Option<ServiceError>,
}

#[async_trait]
pub trait BotDeliveryPort: Send + Sync {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool;
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult>;
}

#[derive(Debug, Clone)]
pub struct FrontendDeliveryCommand {
    pub target: FrontendDeliveryTarget,
    pub event_json: String,
    pub delivery_kind: FrontendDeliveryKind,
    pub run_fallback: Option<RunFallbackDelivery>,
    pub exclude_conn_id: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct RunFallbackDelivery {
    pub run_id: String,
    pub session_id: String,
    pub event_json: String,
}

#[derive(Debug, Clone)]
pub struct FrontendDeliveryResult {
    pub target: FrontendDeliveryTarget,
    pub delivered: usize,
}

#[async_trait]
pub trait FrontendDeliveryPort: Send + Sync {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult>;

    async fn unregister_run(&self, run_id: &str) -> ServiceResult<()>;
}
