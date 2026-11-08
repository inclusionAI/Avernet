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

#[derive(Debug, Clone)]
pub struct AgentSelfView {
    pub agent_code: String,
    pub bots: Vec<crate::types::agent_registration::AgentBotRecord>,
    pub next_cursor: Option<String>,
}

#[async_trait]
pub trait BotSelfService: Send + Sync {
    /// Authenticate the raw Agent identity credential before looking up persisted
    /// registration in the current BCN environment. This never registers or
    /// modifies a Bot and accepts no request-supplied identity selector.
    async fn get_me(&self, token: &str) -> Result<BotSelfView, ApplicationError>;

    async fn get_me_with_actor(&self, token: &str, actor: Option<&str>) -> Result<BotSelfView, ApplicationError> {
        if actor.is_some() { return Err(ApplicationError::Unauthenticated); }
        self.get_me(token).await
    }

    async fn get_agent_me(&self, _token: &str, _provider: Option<&str>, _cursor: Option<&str>, _limit: usize) -> Result<AgentSelfView, ApplicationError> {
        Err(ApplicationError::internal("Agent self service is not configured"))
    }
}
