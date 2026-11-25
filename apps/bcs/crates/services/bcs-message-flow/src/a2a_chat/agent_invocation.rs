//! Explicit direct invocation only; ordinary friend/public/organization paths
//! never fall back to this authority. No body credentials or reusable permit.
use super::*;
use bcs_service_api::application::agent_invoke::{
    AgentInvocationInput, AgentInvokeError, AgentInvokeOperation, AgentInvokePair,
    AgentInvokePrincipal, AgentInvokeScope,
};
use serde::{Deserialize, Serialize};

/// Private store-owned V2 projection. V1 readers reject its version; the
/// public trusted input remains non-serde. No positive decision is persisted.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DirectInvocationProjection {
    namespace: String,
    env: String,
    originator: String,
    operation: String,
}

impl DirectInvocationProjection {
    pub(super) fn from_input(input: &AgentInvocationInput) -> Self {
        Self {
            namespace: input.authenticated_scope.namespace.clone(),
            env: input.authenticated_scope.env.clone(),
            originator: input.originator.clone(),
            operation: "direct_invocation".into(),
        }
    }

    pub(super) fn input(&self, source: &str, target: &str) -> AgentInvocationInput {
        AgentInvocationInput {
            principal: AgentInvokePrincipal::Agent(source.into()),
            authenticated_scope: AgentInvokeScope {
                namespace: self.namespace.clone(),
                env: self.env.clone(),
            },
            pair: AgentInvokePair {
                from_bot_id: source.into(),
                to_bot_id: target.into(),
            },
            originator: self.originator.clone(),
            // Unknown/corrupt operations can never upgrade to a direct call.
            operation: if self.operation == "direct_invocation" {
                AgentInvokeOperation::DirectInvocation
            } else {
                AgentInvokeOperation::DelegatedInvocation
            },
        }
    }
}

impl A2aChat {
    /// Last mandatory check after StartSend CAS/correlation awaits, immediately
    /// before the runtime may call transport. Recovery never redeems an old permit.
    pub(crate) async fn authorize_direct_send(
        &self,
        row: &bcs_domain::message_delivery::PersistedMessageDelivery,
        command: &BotDeliveryCommand,
    ) -> ServiceResult<()> {
        let p: queued::DirectProjection =
            serde_json::from_value(row.semantic_projection_json.clone())
                .map_err(|_| queued::invalid("invalid direct projection"))?;
        match (p.version, p.agent_invocation.as_ref()) {
            (1, None) => Ok(()),
            (2, Some(stored))
                if row.group_id.is_empty()
                    && command.target_bot_id() == row.target_bot_id
                    && row.run_id.as_deref() == Some(command.run_id.as_str()) =>
            {
                self.authorize_direct_invocation(
                    &stored.input(&p.from_bot_id, &row.target_bot_id),
                    &CallerContext::Bot(bcs_service_api::BotActor {
                        bot_uuid: p.from_bot_id.clone(),
                    }),
                    &row.target_bot_id,
                    Some(&p.from_actor_id),
                    None,
                    p.organization_code.as_deref(),
                    false,
                )
                .await
            }
            _ => Err(queued::invalid(
                "invalid direct invocation projection scope",
            )),
        }
    }

    pub(super) async fn authorize_direct_invocation(
        &self,
        input: &AgentInvocationInput,
        caller: &CallerContext,
        target: &str,
        from_actor: Option<&str>,
        staff: Option<&str>,
        organization: Option<&str>,
        test_user: bool,
    ) -> ServiceResult<()> {
        if !matches!(caller, CallerContext::Bot(bot) if bot.bot_uuid == input.pair.from_bot_id)
            || input.principal != AgentInvokePrincipal::Agent(input.pair.from_bot_id.clone())
            || target != input.pair.to_bot_id
            || input.pair.from_bot_id == target
            || input.pair.from_bot_id.trim().is_empty()
            || target.trim().is_empty()
            || input.pair.from_bot_id.starts_with("human_")
            || target.starts_with("human_")
            || input.originator != input.pair.from_bot_id
            || from_actor.is_some_and(|actor| actor != input.originator)
            || staff.is_some()
            || organization.is_some()
            || test_user
            || input.operation != AgentInvokeOperation::DirectInvocation
        {
            return Err(ServiceError::Forbidden(
                "AgentInvoke direct identity or lineage mismatch".into(),
            ));
        }
        self.check_invocation(input).await
    }

    pub(super) async fn check_invocation(&self, input: &AgentInvocationInput) -> ServiceResult<()> {
        self.interceptors
            .authorize_agent_invocation(input)
            .await
            .map_err(|error| match error {
                AgentInvokeError::Forbidden
                | AgentInvokeError::Unauthenticated
                | AgentInvokeError::NotFound => {
                    ServiceError::Forbidden("AgentInvoke not authorized".into())
                }
                AgentInvokeError::Conflict => {
                    ServiceError::Conflict("AgentInvoke authorization changed".into())
                }
                _ => ServiceError::InternalError("AgentInvoke authorization unavailable".into()),
            })?;
        // Provider collaboration is an upper boundary, not an alternate grant.
        // A valid invocation edge must not widen a same-owner restriction.
        self.registry
            .collaboration_owner(&[input.pair.from_bot_id.clone(), input.pair.to_bot_id.clone()])
            .await?;
        Ok(())
    }
}
