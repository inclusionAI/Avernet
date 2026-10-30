use async_trait::async_trait;

use crate::ServiceResult;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineResultPublishCommand {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    pub sender_bot_id: String,
    pub content: String,
    pub created_at_ms: u64,
}

/// Publishes a completed one-shot state-machine result back into its chat
/// session without coupling the collaboration runtime to routing or delivery.
/// Success acknowledges durable Group queue acceptance for managed recipients
/// and synchronous delivery acceptance for legacy recipients, not Bot completion.
/// An error may follow a partial publication. Conflicting history, empty queue
/// admissions and rejected recipients must not be acknowledged as success.
/// Stable message identity deduplicates history, but does not promise idempotent
/// Bot routing: callers must not blindly replay an ambiguous invocation.
#[async_trait]
pub trait StateMachineResultPublisherPort: Send + Sync {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()>;

    /// Deliver one bot-only RunResult to the manager of a failed or aborted Run.
    /// `Delivered` acknowledges durable queue admission for a managed target or
    /// synchronous acceptance for a legacy one. `Skipped` and `Retry` guarantee
    /// that nothing was sent; `Uncertain` must never be retried.
    async fn publish_state_machine_run_result(
        &self,
        _payload: crate::StateMachineRunResultPayload,
    ) -> ServiceResult<crate::StateMachineRunResultOutcome> {
        Err(crate::ServiceError::InternalError("state-machine RunResult publication is not configured".into()))
    }
}
