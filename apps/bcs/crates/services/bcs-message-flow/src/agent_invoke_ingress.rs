//! Transport-independent trusted ingress -> explicit Direct A2A use case.
use async_trait::async_trait;
use bcs_service_api::application::agent_invoke::*;
use bcs_service_api::port::agent_invoke::AgentInvokeIdentityResolutionPort;
use bcs_service_api::{
    A2aChatRunService, AsyncA2aChatAccepted, AsyncA2aChatCommand, BotActor, CallerContext,
    ChatResponseMode, ServiceError,
};
use std::sync::Arc;

pub struct AgentInvokeDirectApplicationService {
    scope: AgentInvokeScope,
    identities: Arc<dyn AgentInvokeIdentityResolutionPort>,
    runs: Arc<dyn A2aChatRunService>,
    limits: AgentInvokeDirectLimits,
}
impl AgentInvokeDirectApplicationService {
    pub fn new(
        scope: AgentInvokeScope,
        identities: Arc<dyn AgentInvokeIdentityResolutionPort>,
        runs: Arc<dyn A2aChatRunService>,
        limits: AgentInvokeDirectLimits,
    ) -> AgentInvokeResult<Self> {
        if scope.namespace.trim().is_empty()
            || scope.env.trim().is_empty()
            || limits.default_timeout_ms > limits.max_timeout_ms
            || limits.max_timeout_ms.get() > 24 * 60 * 60 * 1_000
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        Ok(Self {
            scope,
            identities,
            runs,
            limits,
        })
    }
}
#[async_trait]
impl AgentInvokeDirectService for AgentInvokeDirectApplicationService {
    async fn invoke(
        &self,
        command: AgentInvokeDirectCommand,
    ) -> AgentInvokeResult<AsyncA2aChatAccepted> {
        self.invoke_direct(command).await
    }
}

impl AgentInvokeDirectApplicationService {
    // Preserve original source spans for deterministic executable coverage.
    async fn invoke_direct(
        &self,
        command: AgentInvokeDirectCommand,
    ) -> AgentInvokeResult<AsyncA2aChatAccepted> {
        if command.authenticated_scope != self.scope {
            return Err(AgentInvokeError::Forbidden);
        }
        let source = &command.identity.bot_id;
        if source.trim().is_empty()
            || source.starts_with("human_")
            || command.identity.agent_code.trim().is_empty()
        {
            return Err(AgentInvokeError::Unauthenticated);
        }
        if command.to_bot_id.trim().is_empty()
            || command.to_bot_id.starts_with("human_")
            || command.to_bot_id == *source
            || command.message.is_empty()
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        let timeout_ms = command
            .timeout_ms
            .unwrap_or(self.limits.default_timeout_ms.get());
        if timeout_ms == 0 || timeout_ms > self.limits.max_timeout_ms.get() {
            return Err(AgentInvokeError::InvalidInput);
        }
        let run_id = uuid::Uuid::new_v4().to_string();
        let session_key = command
            .session_id
            .unwrap_or_else(|| format!("agent-invoke:{run_id}"));
        if session_key.starts_with("a2a-test:") {
            return Err(AgentInvokeError::InvalidInput);
        }
        bcs_service_api::port::repo::session_registry::validate_direct_session_id(&session_key)
            .map_err(|_| AgentInvokeError::InvalidInput)?;
        // Current exact physical binding and epochs are pinned BEFORE any Run,
        // session, response-channel or network effect; runtime rechecks them.
        let binding = self
            .identities
            .resolve_invocation_source(&self.scope, &command.identity)
            .await?;
        let input = AgentInvocationInput {
            authentication: AgentInvocationAuthentication::VerifiedSource(binding),
            principal: AgentInvokePrincipal::Agent(source.clone()),
            authenticated_scope: self.scope.clone(),
            pair: AgentInvokePair {
                from_bot_id: source.clone(),
                to_bot_id: command.to_bot_id.clone(),
            },
            originator: source.clone(),
            operation: AgentInvokeOperation::DirectInvocation,
        };
        self.runs
            .start_async_agent_invocation(
                AsyncA2aChatCommand {
                    caller: CallerContext::Bot(BotActor {
                        bot_uuid: source.clone(),
                    }),
                    target_bot_id: command.to_bot_id,
                    message: command.message,
                    from_actor_id: Some(source.clone()),
                    run_channel_from: Some(source.clone()),
                    authenticated_staff_id: None,
                    run_id,
                    session_key,
                    timeout_ms,
                    client: Some("agent-invoke".into()),
                    tags: Vec::new(),
                    response_mode: ChatResponseMode::Full,
                    caller_wait_mode: None,
                    organization_code: None,
                    provider_bypass_headers: Vec::new(),
                    a2a_test_user: None,
                },
                input,
            )
            .await
            .map_err(|error| match error {
                ServiceError::Forbidden(_) | ServiceError::Unauthorized(_) => {
                    AgentInvokeError::Forbidden
                }
                ServiceError::BotNotFound(_) => AgentInvokeError::NotFound,
                ServiceError::Conflict(_) => AgentInvokeError::Conflict,
                ServiceError::InvalidOperation { .. } => AgentInvokeError::InvalidInput,
                _ => AgentInvokeError::Unavailable,
            })
    }
}

