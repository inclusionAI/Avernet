use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use serde_json::Value;

use crate::{InteractionKey, InteractionKind, InteractionStatus, ServiceResult};

#[derive(Debug, Clone, PartialEq)]
pub struct InteractionFrontendEvent {
    pub bcs_run_id: String,
    pub bcs_session_id: String,
    pub group_id: String,
    pub bot_id: String,
    pub payload: Value,
}

/// Typed frontend boundary for interaction events.
///
/// Delivery adapters own their concrete wire envelope; the interaction
/// Application service only publishes transport-neutral events.
#[async_trait]
pub trait InteractionFrontendPort: Send + Sync {
    async fn publish_interaction(&self, event: InteractionFrontendEvent) -> ServiceResult<()>;
}

#[derive(Debug, Clone, PartialEq)]
pub struct InteractionRecord {
    pub key: InteractionKey,
    pub provider_run_id: String,
    pub kind: InteractionKind,
    pub bcs_session_id: String,
    pub group_id: String,
    pub bot_id: String,
    pub run_deadline_ms: u64,
    pub provider_target: BotDeliveryTarget,
    pub provider_bypass_headers: Vec<(String, String)>,
    pub requested_payload: Value,
    pub status: InteractionStatus,
    pub in_flight: bool,
    pub accepted_idempotency_key: Option<String>,
    pub accepted_resolution_fingerprint: Option<String>,
    pub resolved_by_actor_id: Option<String>,
    pub requested_at_ms: u64,
    pub accepted_at_ms: Option<u64>,
    pub terminal_at_ms: Option<u64>,
    pub invalidation_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InteractionInsertResult {
    Stored,
    IdenticalDuplicate,
    ConflictingDuplicate,
    TerminalPreserved,
    CapacityExceeded,
}

#[derive(Debug, Clone, PartialEq)]
pub enum InteractionResolveClaim {
    Acquired(InteractionRecord),
    InFlight(InteractionStatus),
    AlreadyAccepted(InteractionRecord),
    AcceptedDifferent(InteractionRecord),
    Terminal(InteractionRecord),
    NotFound,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InteractionResolveCommit {
    Accepted {
        idempotency_key: String,
        resolution_fingerprint: String,
        resolver_actor_id: String,
        accepted_at_ms: u64,
    },
    RetryableFailure,
    Invalidated {
        resolver_actor_id: String,
        reason: String,
        invalidated_at_ms: u64,
    },
}

#[async_trait]
pub trait InteractionStorePort: Send + Sync {
    async fn insert_requested(
        &self,
        record: InteractionRecord,
    ) -> ServiceResult<InteractionInsertResult>;

    async fn get(&self, key: &InteractionKey) -> ServiceResult<Option<InteractionRecord>>;

    async fn list_pending(&self, bcs_session_id: &str) -> ServiceResult<Vec<InteractionRecord>>;

    async fn claim_resolution(
        &self,
        key: &InteractionKey,
        idempotency_key: &str,
        resolution_fingerprint: &str,
    ) -> ServiceResult<InteractionResolveClaim>;

    async fn finish_resolution(
        &self,
        key: &InteractionKey,
        commit: InteractionResolveCommit,
    ) -> ServiceResult<Option<InteractionRecord>>;

    async fn mark_resolved(
        &self,
        key: &InteractionKey,
        resolved_at_ms: u64,
    ) -> ServiceResult<Option<InteractionRecord>>;

    async fn invalidate_run(
        &self,
        bcs_run_id: &str,
        reason: &str,
        invalidated_at_ms: u64,
    ) -> ServiceResult<Vec<InteractionRecord>>;

    async fn cleanup_terminal(&self, terminal_before_ms: u64) -> ServiceResult<usize>;
}

#[derive(Debug, Clone)]
pub struct CanResolveInteractionCommand {
    pub actor_id: String,
    pub bcs_session_id: String,
    pub group_id: String,
}

#[async_trait]
pub trait CanResolveInteractionPort: Send + Sync {
    async fn can_resolve(&self, command: CanResolveInteractionCommand) -> ServiceResult<bool>;
}

/// Product-facing policy name retained from the approved design. The declared
/// port trait still follows the repository `*Port` boundary naming rule.
pub use CanResolveInteractionPort as CanResolveInteraction;

#[derive(Debug, Clone)]
pub struct InteractionProviderCommand {
    pub target: BotDeliveryTarget,
    pub provider_bypass_headers: Vec<(String, String)>,
    pub bcs_run_id: String,
    pub provider_run_id: String,
    pub bcs_session_id: String,
    pub group_id: String,
    pub bot_id: String,
    pub interaction_id: String,
    pub kind: InteractionKind,
    pub idempotency_key: String,
    pub resolution: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InteractionProviderAck {
    pub ok: bool,
    pub retryable: Option<bool>,
    pub error: Option<String>,
}

#[async_trait]
pub trait InteractionProviderPort: Send + Sync {
    async fn resolve_interaction(
        &self,
        command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck>;
}
