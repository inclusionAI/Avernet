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
    async fn get_agent_me(&self, token: &str, provider: Option<&str>, cursor: Option<&str>, limit: usize) -> Result<bcs_service_api::application::v1::AgentSelfView, ApplicationError> {
        if token.trim().is_empty() { return Err(ApplicationError::Unauthenticated); }
        if !(1..=100).contains(&limit) || cursor.is_some_and(|v| v.is_empty() || v.len() > 256) {
            return Err(ApplicationError::invalid("invalid_request", "invalid Agent list pagination"));
        }
        let identity = self.identity.verify(token).await.map_err(|error| match error {
            AgentIdentityError::InvalidToken => ApplicationError::Unauthenticated,
            AgentIdentityError::NotAgent => ApplicationError::forbidden("Agent identity required"),
            AgentIdentityError::Unavailable => identity_unavailable(),
        })?;
        if identity.agent_code.trim().is_empty() { return Err(identity_unavailable()); }
        let query = bcs_service_api::types::agent_registration::AgentBotQuery {
            agent_code: identity.agent_code.clone(), bot_uuid: None,
            provider_id: provider.map(str::to_string), cursor: cursor.map(str::to_string),
            limit: limit + 1, implicit_only: false,
        };
        let mut bots = self.registry.query_agent_bots(&query).await
            .map_err(|_| ApplicationError::internal("Agent Bot list lookup failed"))?;
        let has_more = bots.len() > limit;
        bots.truncate(limit);
        let next_cursor = has_more.then(|| bots.last().map(|bot| bot.bot.bot_id.clone())).flatten();
        Ok(bcs_service_api::application::v1::AgentSelfView { agent_code: identity.agent_code, bots, next_cursor })
    }

    async fn get_me(&self, token: &str) -> Result<BotSelfView, ApplicationError> {
        self.get_me_with_actor(token, None).await
    }

    async fn get_me_with_actor(&self, token: &str, actor: Option<&str>) -> Result<BotSelfView, ApplicationError> {
        if token.trim().is_empty() {
            return Err(ApplicationError::Unauthenticated);
        }
        if token.split('.').count() != 3 {
                let record = self.registry.resolve_bot_token(token, actor).await
                    .map_err(|error| match error {
                        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
                        _ => ApplicationError::internal("Bot identity store unavailable"),
                    })?
                    .ok_or(ApplicationError::Unauthenticated)?;
                return Ok(BotSelfView { registration_status: BotRegistrationStatus::Registered,
                    agent_code: record.agent_code, bot: Some(record.bot) });
        }
        let identity = match self.identity.verify(token).await {
            Ok(identity) => identity,
            Err(AgentIdentityError::InvalidToken) => return Err(ApplicationError::Unauthenticated),
            Err(AgentIdentityError::NotAgent) => return Err(ApplicationError::forbidden("Agent identity required")),
            Err(AgentIdentityError::Unavailable) => return Err(identity_unavailable()),
        };
        if identity.agent_code.trim().is_empty() {
            return Err(identity_unavailable());
        }
        let bot = self.registry.resolve_agent_bot(&identity.agent_code, actor).await
            .map_err(|error| match error {
                ServiceError::Conflict(_) => ApplicationError::conflict(
                    "agent_registration_conflict", "Agent registration is ambiguous",
                ),
                ServiceError::Unauthorized(message) => ApplicationError::forbidden(message),
                // Driver errors may contain row/credential data. Only a fixed
                // description crosses the application error/logging boundary.
                _ => ApplicationError::internal("Agent registration lookup failed"),
            })?.map(|record| record.bot);
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
