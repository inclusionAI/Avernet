//! One-lock local transaction. All fallible validation and version allocation
//! complete before any map/counter/audit is changed. No await while locked.
use async_trait::async_trait;
use bcs_domain::edge_permission::{EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType};
use bcs_service_api::port::repo::agent_invoke::AgentInvokeCommandRepoPort;

use super::*;

struct Mutation {
    approval: Option<AgentInvokeApproval>,
    request: Option<LocalRequest>,
    next_edge: u64,
    next_request: u64,
}

impl LocalAgentInvokeStore {
    fn build_approval(
        &self,
        state: &State,
        pair: &AgentInvokePair,
        facts: &AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<AgentInvokeApproval> {
        let previous = state.edges.get(pair);
        let was_active = previous.is_some_and(|a| a.edge.status == EdgeStatus::Approved);
        if !was_active {
            let degree = |outbound: bool| {
                state
                    .edges
                    .iter()
                    .filter(|(p, a)| {
                        a.edge.status == EdgeStatus::Approved
                            && (if outbound {
                                p.from_bot_id == pair.from_bot_id
                            } else {
                                p.to_bot_id == pair.to_bot_id
                            })
                    })
                    .count()
            };
            if degree(true) >= self.limits.max_out_degree.get() as usize
                || degree(false) >= self.limits.max_in_degree.get() as usize
            {
                return Err(AgentInvokeError::LimitExceeded);
            }
        }
        let edge_id = match previous {
            Some(a) => a.edge.edge_id,
            None => next(state.next_edge)?.get(),
        };
        let version = next(previous.map_or(0, |a| a.version.get()))?;
        Ok(AgentInvokeApproval {
            edge: EdgeGrant {
                edge_id,
                env: self.scope.env.clone(),
                from_id: pair.from_bot_id.clone(),
                to_id: pair.to_bot_id.clone(),
                grant_kind: GrantKind::AgentInvoke,
                grant_ref_id: 0,
                rules: None,
                status: EdgeStatus::Approved,
                originator_policy_type: OriginatorPolicyType::SameAsFrom,
                originator_policy_data: None,
            },
            version,
            from_binding_generation: facts.source.binding_generation,
            to_binding_generation: facts.target.binding_generation,
            from_eligibility_generation: facts.source.eligibility_generation,
            to_eligibility_generation: facts.target.eligibility_generation,
        })
    }

    fn prepare_mutation(
        &self,
        state: &State,
        command: &AgentInvokeCommand,
        facts: &AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<Mutation> {
        let mut mutation = Mutation {
            approval: None,
            request: None,
            next_edge: state.next_edge,
            next_request: state.next_request,
        };
        let current_version = state
            .edges
            .get(&command.pair)
            .map_or(0, |a| a.version.get());
        if matches!(
            command.change,
            AgentInvokeChange::Grant { .. }
                | AgentInvokeChange::Request
                | AgentInvokeChange::Approve { .. }
        ) && (!eligible(&facts.source) || !eligible(&facts.target))
        {
            return Err(AgentInvokeError::Forbidden);
        }
        match &command.change {
            AgentInvokeChange::Grant { expected_version } => {
                if *expected_version != current_version {
                    return Err(AgentInvokeError::Conflict);
                }
                mutation.approval = Some(self.build_approval(state, &command.pair, facts)?);
            }
            AgentInvokeChange::Request => {
                if state
                    .requests
                    .values()
                    .any(|r| r.pair == command.pair && r.view.status == RequestStatus::Pending)
                {
                    return Err(AgentInvokeError::Conflict);
                }
                if Self::pending_count(state, &command.pair.from_bot_id, true)
                    >= self.limits.max_pending_out.get() as usize
                    || Self::pending_count(state, &command.pair.to_bot_id, false)
                        >= self.limits.max_pending_in.get() as usize
                {
                    return Err(AgentInvokeError::LimitExceeded);
                }
                let sequence = next(state.next_request)?.get();
                if sequence > 0xffffffffffff {
                    return Err(AgentInvokeError::InvalidState);
                }
                // Deterministic synthetic UUID. Never a production identifier allocator.
                let request_id = format!("00000000-0000-4000-8000-{sequence:012x}");
                mutation.next_request = sequence;
                mutation.request = Some(LocalRequest {
                    view: AgentInvokeRequestView {
                        request_id,
                        status: RequestStatus::Pending,
                        version: next(0)?,
                        edge_id: None,
                    },
                    pair: command.pair.clone(),
                    creator: command.principal.clone(),
                    source: facts.source.clone(),
                    target: facts.target.clone(),
                    edge_version: current_version,
                    source_authority_generation: Self::source_authority_generation(
                        state,
                        &command.principal,
                        &command.pair,
                    ),
                });
            }
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
            } => {
                let mut request = state
                    .requests
                    .get(request_id)
                    .cloned()
                    .ok_or(AgentInvokeError::NotFound)?;
                if request.pair != command.pair {
                    return Err(AgentInvokeError::NotFound);
                }
                if request.view.version != *expected_version
                    || request.view.status != RequestStatus::Pending
                {
                    return Err(AgentInvokeError::Conflict);
                }
                request.view.version = next(request.view.version.get())?;
                request.view.status = match &command.change {
                    AgentInvokeChange::Approve { .. } => {
                        if !facts.requester_source_authorized
                            || request.source != facts.source
                            || request.target != facts.target
                            || request.edge_version != current_version
                            || request.source_authority_generation
                                != Self::source_authority_generation(
                                    state,
                                    &request.creator,
                                    &request.pair,
                                )
                        {
                            return Err(AgentInvokeError::Conflict);
                        }
                        let approval = self.build_approval(state, &command.pair, facts)?;
                        request.view.edge_id = Some(
                            NonZeroU64::new(approval.edge.edge_id)
                                .ok_or(AgentInvokeError::InvalidState)?,
                        );
                        mutation.approval = Some(approval);
                        RequestStatus::Approved
                    }
                    AgentInvokeChange::Reject { .. } => RequestStatus::Rejected,
                    AgentInvokeChange::Cancel { .. } => RequestStatus::Cancelled,
                    _ => return Err(AgentInvokeError::InvalidState),
                };
                mutation.request = Some(request);
            }
            AgentInvokeChange::Revoke {
                edge_id,
                expected_version,
            } => {
                let mut approval = state
                    .edges
                    .get(&command.pair)
                    .cloned()
                    .ok_or(AgentInvokeError::NotFound)?;
                if approval.edge.edge_id != edge_id.get() {
                    return Err(AgentInvokeError::NotFound);
                }
                if approval.version != *expected_version {
                    return Err(AgentInvokeError::Conflict);
                }
                if approval.edge.status == EdgeStatus::Approved {
                    approval.edge.status = EdgeStatus::Revoked;
                    approval.version = next(approval.version.get())?;
                }
                mutation.approval = Some(approval);
            }
        }
        if let Some(approval) = &mutation.approval {
            mutation.next_edge = mutation.next_edge.max(approval.edge.edge_id);
        }
        Ok(mutation)
    }

    fn replay(state: &State, stored: &StoredReceipt) -> AgentInvokeResult<AgentInvokeReceipt> {
        let mut result = stored.receipt.clone();
        result.replayed = true;
        result.current_grant = state
            .edges
            .get(&stored.command.pair)
            .map(edge_view)
            .transpose()?;
        result.current_request = stored
            .receipt
            .original_request
            .as_ref()
            .and_then(|r| state.requests.get(&r.request_id))
            .map(|r| r.view.clone());
        Ok(result)
    }
}

#[async_trait]
impl AgentInvokeCommandRepoPort for LocalAgentInvokeStore {
    async fn execute(
        &self,
        scope: &AgentInvokeScope,
        command: AgentInvokeCommand,
        snapshot: AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<AgentInvokeReceipt> {
        self.require_scope(scope)?;
        let mut state = self.state.lock().await;
        let current = self.management_facts(&state, &command)?;
        // Re-evaluate the SAME pure policy against current facts inside the
        // serialization boundary. A raw port call cannot bypass authorization.
        self.policy
            .authorize_change(&self.scope, &command, &current)?;
        let key = (command.principal.clone(), command.idempotency_key.clone());
        if let Some(stored) = state.receipts.get(&key) {
            if stored.command != command {
                return Err(AgentInvokeError::Conflict);
            }
            return Self::replay(&state, stored);
        }
        if snapshot != current {
            return Err(AgentInvokeError::Conflict);
        }
        let revision = next(state.revision)?.get();
        let sequence = next(state.next_action)?.get();
        let action_id = format!("local-action-{sequence}");
        let mutation = self.prepare_mutation(&state, &command, &current)?;
        let original_grant = mutation.approval.as_ref().map(edge_view).transpose()?;
        let original_request = mutation.request.as_ref().map(|r| r.view.clone());
        let receipt = AgentInvokeReceipt {
            action_id: action_id.clone(),
            replayed: false,
            original_grant: original_grant.clone(),
            original_request: original_request.clone(),
            current_grant: original_grant.or(state
                .edges
                .get(&command.pair)
                .map(edge_view)
                .transpose()?),
            current_request: original_request,
        };
        let audit = LocalAgentInvokeAudit {
            action_id,
            principal: command.principal.clone(),
            pair: command.pair.clone(),
            action: LocalAgentInvokeAuditAction::Lifecycle(command.change.action()),
            reason: command.reason.clone(),
            before_grant: state.edges.get(&command.pair).map(edge_view).transpose()?,
            after_grant: receipt.current_grant.clone(),
            before_request: mutation
                .request
                .as_ref()
                .and_then(|r| state.requests.get(&r.view.request_id))
                .map(|r| r.view.clone()),
            after_request: receipt.current_request.clone(),
        };
        // Nothing below can return a business/storage error. A real SQL driver
        // MUST commit these effects durably together, not copy this local lock.
        if let Some(approval) = mutation.approval {
            state.edges.insert(command.pair.clone(), approval);
        }
        if let Some(request) = mutation.request {
            state
                .requests
                .insert(request.view.request_id.clone(), request);
        }
        state.next_edge = mutation.next_edge;
        state.next_request = mutation.next_request;
        state.next_action = sequence;
        state.revision = revision;
        state.audits.push(audit);
        state.receipts.insert(
            key,
            StoredReceipt {
                command,
                receipt: receipt.clone(),
            },
        );
        Ok(receipt)
    }
}
