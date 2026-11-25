use async_trait::async_trait;
use bcs_service_api::port::agent_invoke::AgentInvokeFactsPort;

use super::*;

impl LocalAgentInvokeStore {
    pub(super) fn management_facts(
        &self,
        state: &State,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<AgentInvokeManagementSnapshot> {
        let source = state
            .bots
            .get(&command.pair.from_bot_id)
            .cloned()
            .ok_or(AgentInvokeError::NotFound)?;
        let target = state
            .bots
            .get(&command.pair.to_bot_id)
            .cloned()
            .ok_or(AgentInvokeError::NotFound)?;
        let role = |bot: &str| match &command.principal {
            AgentInvokePrincipal::Human(id) => state.roles.get(&(id.clone(), bot.into())).copied(),
            _ => None,
        };
        let request = match &command.change {
            AgentInvokeChange::Approve { request_id, .. }
            | AgentInvokeChange::Reject { request_id, .. }
            | AgentInvokeChange::Cancel { request_id, .. } => {
                let request = state
                    .requests
                    .get(request_id)
                    .ok_or(AgentInvokeError::NotFound)?;
                if request.pair != command.pair {
                    return Err(AgentInvokeError::NotFound);
                }
                Some(request)
            }
            _ => None,
        };
        let delegated_actions = match &command.principal {
            AgentInvokePrincipal::Governance(id) => state
                .delegations
                .get(&(id.clone(), command.pair.clone()))
                .cloned()
                .unwrap_or_default(),
            _ => Vec::new(),
        };
        Ok(AgentInvokeManagementSnapshot {
            scope: self.scope.clone(),
            pair: command.pair.clone(),
            principal: command.principal.clone(),
            source,
            target,
            source_role: role(&command.pair.from_bot_id),
            target_role: role(&command.pair.to_bot_id),
            delegated_actions,
            request_creator: request.map(|r| r.creator.clone()),
            requester_source_authorized: request
                .is_some_and(|r| Self::has_source_authority(state, &r.creator, &r.pair)),
            fence: state.revision.to_string(),
        })
    }
}

#[async_trait]
impl AgentInvokeFactsPort for LocalAgentInvokeStore {
    async fn runtime_snapshot(
        &self,
        scope: &AgentInvokeScope,
        pair: &AgentInvokePair,
    ) -> AgentInvokeResult<AgentInvokeRuntimeSnapshot> {
        self.require_scope(scope)?;
        let state = self.state.lock().await;
        Ok(AgentInvokeRuntimeSnapshot {
            scope: self.scope.clone(),
            pair: pair.clone(),
            source: state
                .bots
                .get(&pair.from_bot_id)
                .cloned()
                .ok_or(AgentInvokeError::NotFound)?,
            target: state
                .bots
                .get(&pair.to_bot_id)
                .cloned()
                .ok_or(AgentInvokeError::NotFound)?,
            approval: state.edges.get(pair).cloned(),
        })
    }

    async fn management_snapshot(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<AgentInvokeManagementSnapshot> {
        self.require_scope(scope)?;
        let state = self.state.lock().await;
        self.management_facts(&state, command)
    }
}
