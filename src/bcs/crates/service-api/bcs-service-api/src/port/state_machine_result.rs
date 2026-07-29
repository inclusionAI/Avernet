use async_trait::async_trait;

use crate::ServiceResult;

#[derive(Debug, Clone)]
pub struct StateMachineResultPublishCommand {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    pub sender_bot_id: String,
    pub content: String,
}

/// Publishes a completed one-shot state-machine result back into its chat
/// session without coupling the collaboration runtime to routing or delivery.
#[async_trait]
pub trait StateMachineResultPublisherPort: Send + Sync {
    async fn publish_state_machine_result(
        &self,
        cmd: StateMachineResultPublishCommand,
    ) -> ServiceResult<()>;
}
