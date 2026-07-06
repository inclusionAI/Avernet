use async_trait::async_trait;

use super::ServiceResult;

pub use bcs_domain::{
    ContextBotSummary, ContextConflict, ContextConflictPosition, ContextFusionRequest,
    ContextFusionResponse, ContextParticipantPerspective,
};

/// Service for context fusion.
#[async_trait]
pub trait FusionCoreService: Send + Sync {
    /// Fuse contexts from multiple bots.
    async fn fuse(&self, request: &ContextFusionRequest) -> ServiceResult<ContextFusionResponse>;

    /// Load a bot's context.
    fn load_bot_context(&self, bot_id: &str) -> ServiceResult<ContextBotSummary>;

    /// Load multiple bots' contexts.
    fn load_bot_contexts(&self, bot_ids: &[String]) -> Vec<ContextBotSummary>;
}
