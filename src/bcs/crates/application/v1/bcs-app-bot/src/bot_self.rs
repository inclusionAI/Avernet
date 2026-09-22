use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::application::v1::{ApplicationError, BotRegistrationStatus, BotSelfService, BotSelfView};
use bcs_service_api::core::BotRegistryCoreService;
use bcs_service_api::port::agent_identity::{AgentIdentityError, AgentIdentityPort};
use bcs_service_api::ServiceError;

pub struct BotSelfServiceImpl {
    identity: Arc<dyn AgentIdentityPort>,
    registry: Arc<dyn BotRegistryCoreService>,
}

impl BotSelfServiceImpl {
    pub fn new(identity: Arc<dyn AgentIdentityPort>, registry: Arc<dyn BotRegistryCoreService>) -> Self {
        Self { identity, registry }
    }
}

fn identity_unavailable() -> ApplicationError {
    ApplicationError::bad_gateway("agent_identity_unavailable", "Agent identity service unavailable")
}

#[async_trait]
impl BotSelfService for BotSelfServiceImpl {
    async fn get_me(&self, token: &str) -> Result<BotSelfView, ApplicationError> {
        if token.trim().is_empty() {
            return Err(ApplicationError::Unauthenticated);
        }
        let identity = self.identity.verify(token).await.map_err(|error| match error {
            AgentIdentityError::InvalidToken => ApplicationError::Unauthenticated,
            AgentIdentityError::NotAgent => ApplicationError::forbidden("Agent identity required"),
            AgentIdentityError::Unavailable => identity_unavailable(),
        })?;
        if identity.agent_code.trim().is_empty() {
            return Err(identity_unavailable());
        }
        let bot = self.registry.find_agent_registration(&identity.agent_code).await
            .map_err(|error| match error {
                ServiceError::Conflict(_) => ApplicationError::conflict(
                    "agent_registration_conflict", "Agent registration is ambiguous",
                ),
                // Driver errors may contain row/credential data. Only a fixed
                // description crosses the application error/logging boundary.
                _ => ApplicationError::internal("Agent registration lookup failed"),
            })?;
        Ok(BotSelfView {
            registration_status: if bot.is_some() {
                BotRegistrationStatus::Registered
            } else {
                BotRegistrationStatus::Unregistered
            },
            agent_code: identity.agent_code,
            bot,
        })
    }
}
