//! Pure, framework-neutral Agent invocation authorization policy.
use bcs_domain::agent_invoke::validate_agent_invoke_shape;
use bcs_domain::edge_permission::{AuthzGrantRef, EdgeStatus, GrantKind, GrantSource};
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::types::agent_invoke::*;

pub struct AgentInvokePolicy;

pub(super) fn validate_scope(scope: &AgentInvokeScope) -> AgentInvokeResult<()> {
    if scope.namespace.trim().is_empty() || scope.env.trim().is_empty() {
        return Err(AgentInvokeError::InvalidInput);
    }
    Ok(())
}

fn validate_pair(pair: &AgentInvokePair) -> AgentInvokeResult<()> {
    if pair.from_bot_id.trim().is_empty()
        || pair.to_bot_id.trim().is_empty()
        || pair.from_bot_id == pair.to_bot_id
        || pair.from_bot_id.starts_with("human_")
        || pair.to_bot_id.starts_with("human_")
    {
        return Err(AgentInvokeError::InvalidInput);
    }
    Ok(())
}

pub(super) fn validate_principal(principal: &AgentInvokePrincipal) -> AgentInvokeResult<()> {
    if principal.id().trim().is_empty() {
        return Err(AgentInvokeError::Unauthenticated);
    }
    let valid = match principal {
        AgentInvokePrincipal::Human(id) => id.starts_with("human_") && id.len() > 6,
        AgentInvokePrincipal::Agent(id) => !id.starts_with("human_"),
        AgentInvokePrincipal::Governance(_) => true,
    };
    if !valid {
        return Err(AgentInvokeError::Unauthenticated);
    }
    Ok(())
}

fn validate_states(
    pair: &AgentInvokePair,
    source: &AgentInvokeBotState,
    target: &AgentInvokeBotState,
) -> AgentInvokeResult<()> {
    if source.bot_id != pair.from_bot_id || target.bot_id != pair.to_bot_id {
        return Err(AgentInvokeError::InvalidState);
    }
    Ok(())
}

fn require_eligible(
    source: &AgentInvokeBotState,
    target: &AgentInvokeBotState,
) -> AgentInvokeResult<()> {
    if [source, target]
        .iter()
        .any(|bot| !bot.eligible || !bot.active || bot.hidden)
    {
        return Err(AgentInvokeError::Forbidden);
    }
    Ok(())
}

impl AgentInvokePolicyCoreService for AgentInvokePolicy {
    fn validate_invocation(
        &self,
        scope: &AgentInvokeScope,
        input: &AgentInvocationInput,
    ) -> AgentInvokeResult<()> {
        validate_scope(scope)?;
        validate_pair(&input.pair)?;
        validate_principal(&input.principal)?;
        if input.authenticated_scope != *scope
            || input.principal != AgentInvokePrincipal::Agent(input.pair.from_bot_id.clone())
            || input.originator != input.pair.from_bot_id
            || input.operation != AgentInvokeOperation::DirectInvocation
        {
            return Err(AgentInvokeError::Forbidden);
        }
        Ok(())
    }

    fn authorize_invocation(
        &self,
        scope: &AgentInvokeScope,
        input: &AgentInvocationInput,
        snapshot: &AgentInvokeRuntimeSnapshot,
    ) -> AgentInvokeResult<AgentInvocationPermit> {
        self.validate_invocation(scope, input)?;
        if snapshot.scope != *scope || snapshot.pair != input.pair {
            return Err(AgentInvokeError::InvalidState);
        }
        validate_states(&input.pair, &snapshot.source, &snapshot.target)?;
        require_eligible(&snapshot.source, &snapshot.target)?;
        let approval = snapshot
            .approval
            .as_ref()
            .ok_or(AgentInvokeError::Forbidden)?;
        let edge = &approval.edge;
        if edge.grant_kind != GrantKind::AgentInvoke
            || edge.edge_id == 0
            || edge.env != scope.env
            || edge.from_id != input.pair.from_bot_id
            || edge.to_id != input.pair.to_bot_id
            || validate_agent_invoke_shape(edge).is_err()
        {
            return Err(AgentInvokeError::InvalidState);
        }
        if edge.status != EdgeStatus::Approved
            || approval.from_binding_generation != snapshot.source.binding_generation
            || approval.to_binding_generation != snapshot.target.binding_generation
            || approval.from_eligibility_generation != snapshot.source.eligibility_generation
            || approval.to_eligibility_generation != snapshot.target.eligibility_generation
        {
            return Err(AgentInvokeError::Forbidden);
        }
        Ok(AuthzGrantRef {
            kind: GrantKind::AgentInvoke,
            ref_id: edge.edge_id,
            revision: Some(approval.version.get()),
            digest: None,
            source: GrantSource::EdgeGrant,
        })
    }

    fn validate_command(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<()> {
        validate_scope(scope)?;
        validate_pair(&command.pair)?;
        validate_principal(&command.principal)?;
        if command.authenticated_scope != *scope {
            return Err(AgentInvokeError::Forbidden);
        }
        if command.idempotency_key.is_empty()
            || command.idempotency_key.len() > 128
            || !command
                .idempotency_key
                .bytes()
                .all(|c| c.is_ascii_graphic())
            || command.reason.trim().is_empty()
            || command.reason.len() > 2048
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        let (request_id, version) = match &command.change {
            AgentInvokeChange::Grant { expected_version } => (None, *expected_version),
            AgentInvokeChange::Request => (None, 0),
            AgentInvokeChange::Approve {
                request_id,
                expected_version,
            }
            | AgentInvokeChange::Reject {
                request_id,
                expected_version,
            }
            | AgentInvokeChange::Cancel {
                request_id,
                expected_version,
            } => (Some(request_id), expected_version.get()),
            AgentInvokeChange::Revoke {
                expected_version, ..
            } => (None, expected_version.get()),
        };
        if version > i64::MAX as u64
            || request_id.is_some_and(|id| uuid::Uuid::parse_str(id).is_err())
        {
            return Err(AgentInvokeError::InvalidInput);
        }
        Ok(())
    }

    fn authorize_change(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
        snapshot: &AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<()> {
        self.validate_command(scope, command)?;
        if snapshot.scope != *scope
            || snapshot.pair != command.pair
            || snapshot.principal != command.principal
            || snapshot.fence.trim().is_empty()
        {
            return Err(AgentInvokeError::InvalidState);
        }
        validate_states(&command.pair, &snapshot.source, &snapshot.target)?;
        let action = command.change.action();
        // Eligibility/state/version checks belong to the atomic store, AFTER
        // idempotency receipt recovery. An ineligible/revoked Bot must not
        // prevent authorized revoke or turn an old successful key into a write.
        let allowed = match &command.principal {
            AgentInvokePrincipal::Agent(_) => false,
            AgentInvokePrincipal::Governance(_) => snapshot.delegated_actions.contains(&action),
            AgentInvokePrincipal::Human(_) => match action {
                AgentInvokeAction::Request => snapshot.source_role.is_some(),
                AgentInvokeAction::Grant
                | AgentInvokeAction::Approve
                | AgentInvokeAction::Reject => snapshot.target_role == Some(AgentInvokeRole::Owner),
                AgentInvokeAction::Cancel => {
                    snapshot.source_role == Some(AgentInvokeRole::Owner)
                        || (snapshot.source_role.is_some()
                            && snapshot.request_creator.as_ref() == Some(&command.principal))
                }
                AgentInvokeAction::Revoke => {
                    snapshot.source_role.is_some()
                        || snapshot.target_role == Some(AgentInvokeRole::Owner)
                }
                AgentInvokeAction::Query => {
                    snapshot.source_role.is_some() || snapshot.target_role.is_some()
                }
            },
        };
        if !allowed {
            return Err(AgentInvokeError::Forbidden);
        }
        Ok(())
    }
}
