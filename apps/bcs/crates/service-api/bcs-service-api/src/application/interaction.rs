use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use serde_json::Value;
use thiserror::Error;

use crate::{InteractionFrontendEvent, InteractionKind, InteractionStatus, ServiceResult};

#[derive(Debug, Clone)]
pub struct ProviderInteractionRequestedCommand {
    pub bcs_run_id: String,
    pub provider_run_id: String,
    pub interaction_id: String,
    pub kind: InteractionKind,
    pub bcs_session_id: String,
    pub group_id: String,
    pub bot_id: String,
    pub run_deadline_ms: u64,
    pub provider_target: BotDeliveryTarget,
    pub provider_bypass_headers: Vec<(String, String)>,
    pub payload: Value,
    pub received_at_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InteractionRequestedOutcome {
    Stored,
    Duplicate,
    ConflictPreserved,
    TerminalPreserved,
    CapacityRejected,
}

#[derive(Debug, Clone)]
pub struct ProviderInteractionResolvedCommand {
    pub bcs_run_id: String,
    pub provider_run_id: String,
    pub interaction_id: String,
    pub kind: InteractionKind,
    pub payload: Value,
    pub received_at_ms: u64,
}

#[derive(Debug, Clone)]
pub struct ResolveInteractionCommand {
    pub bcs_run_id: String,
    pub interaction_id: String,
    pub idempotency_key: String,
    pub resolver_actor_id: String,
    /// Immutable scope from a session-bound connection token. User-bound
    /// connections leave these unset and rely on real-time authorization.
    pub expected_bcs_session_id: Option<String>,
    pub expected_group_id: Option<String>,
    /// Kind-specific resolution fields with the common correlation fields
    /// removed by the delivery adapter.
    pub resolution: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolveInteractionResult {
    pub accepted: bool,
    pub interaction_id: String,
    pub status: InteractionStatus,
    pub idempotency_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum InteractionServiceError {
    #[error("invalid interaction request: {0}")]
    InvalidRequest(String),
    #[error("interaction caller is not authorized")]
    Unauthorized,
    #[error("interaction was not found")]
    NotFound,
    #[error("{message}")]
    ResolveFailed {
        message: String,
        retryable: bool,
        status: InteractionStatus,
    },
    #[error("interaction service failed: {0}")]
    Internal(String),
}

#[async_trait]
pub trait InteractionService: Send + Sync {
    async fn on_provider_requested(
        &self,
        command: ProviderInteractionRequestedCommand,
    ) -> ServiceResult<InteractionRequestedOutcome>;

    async fn on_provider_resolved(
        &self,
        command: ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()>;

    async fn resolve(
        &self,
        command: ResolveInteractionCommand,
    ) -> Result<ResolveInteractionResult, InteractionServiceError>;

    async fn list_pending(
        &self,
        bcs_session_id: &str,
    ) -> ServiceResult<Vec<InteractionFrontendEvent>>;

    async fn invalidate_run(
        &self,
        bcs_run_id: &str,
        reason: &str,
        invalidated_at_ms: u64,
    ) -> ServiceResult<usize>;

    async fn cleanup_terminal(&self, terminal_before_ms: u64) -> ServiceResult<usize>;
}
