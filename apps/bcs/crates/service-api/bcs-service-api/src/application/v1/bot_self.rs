use async_trait::async_trait;

use super::ApplicationError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotRegistrationStatus {
    Registered,
    Unregistered,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotSelfView {
    pub registration_status: BotRegistrationStatus,
    pub agent_code: String,
    pub bot: Option<crate::types::AgentBotRegistration>,
}

#[async_trait]
pub trait BotSelfService: Send + Sync {
    /// Authenticate the raw Agent identity credential before looking up persisted
    /// registration in the current BCN environment. This never registers or
    /// modifies a Bot and accepts no request-supplied identity selector.
    async fn get_me(&self, token: &str) -> Result<BotSelfView, ApplicationError>;
}
