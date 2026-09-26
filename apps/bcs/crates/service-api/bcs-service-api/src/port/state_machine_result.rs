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
/// Success acknowledges the existing synchronous message-flow acceptance, not
/// every recipient's completion. An error may follow a partial publication.
/// Stable message identity deduplicates history, but does not promise idempotent
/// Bot routing: callers must not blindly replay an ambiguous invocation.
#[async_trait]
pub trait StateMachineResultPublisherPort: Send + Sync {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()>;
}
