use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::BcsFrame;

use crate::{ServiceError, ServiceResult};

pub use bcs_protocol::{BotDeliveryKind, FrontendDeliveryKind, FrontendDeliveryTarget};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ProviderTransportPreference {
    #[default]
    SseFirst,
    Callback,
}

#[derive(Debug, Clone)]
pub struct BotDeliveryCommand {
    pub target: BotDeliveryTarget,
    pub run_id: String,
    pub frame: BcsFrame,
    pub delivery_kind: BotDeliveryKind,
    /// Request-scoped transport preference for HTTP Provider `chat.send`.
    /// Non-Provider transports ignore this field.
    pub provider_transport: ProviderTransportPreference,
    /// Opaque inbound HTTP headers explicitly allowlisted by BCS configuration
    /// for forwarding to HTTP provider webhooks. Empty for non-HTTP ingress.
    pub provider_bypass_headers: Vec<(String, String)>,
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

#[derive(Debug, Clone)]
pub struct BotAbortDeliveryCommand {
    pub target: BotDeliveryTarget,
    pub command_id: String,
    pub group_id: String,
    pub session_id: String,
    /// Exact downstream run id for Bot WebSocket/plugin delivery. `None`
    /// denotes one Provider Bot/Session scope request.
    pub run_id: Option<String>,
    /// Routing headers captured from the original Provider `chat.send`.
    /// Non-Provider transports ignore this field.
    pub provider_bypass_headers: Vec<(String, String)>,
    pub timeout_ms: u64,
}

impl BotAbortDeliveryCommand {
    pub fn target_bot_id(&self) -> &str {
        self.target.bot_id()
    }
}

#[derive(Debug)]
pub struct BotAbortDeliveryResult {
    pub target_bot_id: String,
    pub aborted_run_ids: Vec<String>,
}

#[async_trait]
pub trait BotDeliveryPort: Send + Sync {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool;
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult>;

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        Err(ServiceError::InvalidOperation {
            message: "typed chat.abort delivery is not supported by this transport".to_string(),
            request_id: Some(cmd.command_id),
        })
    }
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
