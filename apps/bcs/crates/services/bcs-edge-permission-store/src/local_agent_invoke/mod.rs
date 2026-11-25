//! Public-local, synthetic Agent invocation driver. All facts, generations,
//! grants, requests, receipts and audit entries share ONE process-local lock.
//! It is deliberately NOT mounted by bootstrap and is NOT a durable SQL store
//! or a production identity/eligibility source. Production requires a provider
//! with durable invalidation fencing and the same atomic command contract.
//!
//! Only this kind is represented here; no legacy friend/authority store is
//! mutated. The fixture/host composition owns fact publication, never HTTP.
mod commands;
mod facts;

use std::collections::HashMap;
use std::num::NonZeroU64;
use std::sync::Arc;

use bcs_domain::edge_permission::RequestStatus;
use bcs_service_api::core::agent_invoke::AgentInvokePolicyCoreService;
use bcs_service_api::types::agent_invoke::*;
use tokio::sync::Mutex;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LocalAgentInvokeAuditAction {
    Lifecycle(AgentInvokeAction),
    QualificationInvalidated,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalAgentInvokeAudit {
    pub action_id: String,
    pub principal: AgentInvokePrincipal,
    pub pair: AgentInvokePair,
    pub action: LocalAgentInvokeAuditAction,
    pub reason: String,
    pub before_grant: Option<AgentInvokeGrantView>,
    pub after_grant: Option<AgentInvokeGrantView>,
    pub before_request: Option<AgentInvokeRequestView>,
    pub after_request: Option<AgentInvokeRequestView>,
}

#[derive(Clone)]
struct LocalRequest {
    view: AgentInvokeRequestView,
    pair: AgentInvokePair,
    creator: AgentInvokePrincipal,
    source: AgentInvokeBotState,
    target: AgentInvokeBotState,
    edge_version: u64,
    source_authority_generation: u64,
}

struct StoredReceipt {
    command: AgentInvokeCommand,
    receipt: AgentInvokeReceipt,
}

#[derive(Default)]
struct State {
    revision: u64,
    next_edge: u64,
    next_request: u64,
    next_action: u64,
    bots: HashMap<String, AgentInvokeBotState>,
    roles: HashMap<(String, String), AgentInvokeRole>,
    role_generations: HashMap<(String, String), u64>,
    delegations: HashMap<(String, AgentInvokePair), Vec<AgentInvokeAction>>,
    delegation_generations: HashMap<(String, AgentInvokePair), u64>,
    edges: HashMap<AgentInvokePair, AgentInvokeApproval>,
    requests: HashMap<String, LocalRequest>,
    receipts: HashMap<(AgentInvokePrincipal, String), StoredReceipt>,
    audits: Vec<LocalAgentInvokeAudit>,
}

/// Explicitly injected synthetic data; identities do not come from Card labels.
pub struct LocalAgentInvokeStore {
    scope: AgentInvokeScope,
    limits: AgentInvokeLimits,
    policy: Arc<dyn AgentInvokePolicyCoreService>,
    state: Mutex<State>,
}

fn next(value: u64) -> AgentInvokeResult<NonZeroU64> {
    value
        .checked_add(1)
        .filter(|n| *n <= i64::MAX as u64)
        .and_then(NonZeroU64::new)
        .ok_or(AgentInvokeError::InvalidState)
}

fn eligible(bot: &AgentInvokeBotState) -> bool {
    bot.eligible && bot.active && !bot.hidden
}

fn edge_view(approval: &AgentInvokeApproval) -> AgentInvokeResult<AgentInvokeGrantView> {
    Ok(AgentInvokeGrantView {
        edge_id: NonZeroU64::new(approval.edge.edge_id).ok_or(AgentInvokeError::InvalidState)?,
        status: approval.edge.status,
        version: approval.version,
    })
}

impl LocalAgentInvokeStore {
    pub fn new(
        scope: AgentInvokeScope,
        limits: AgentInvokeLimits,
        policy: Arc<dyn AgentInvokePolicyCoreService>,
    ) -> AgentInvokeResult<Self> {
        if scope.namespace.trim().is_empty() || scope.env.trim().is_empty() {
            return Err(AgentInvokeError::InvalidInput);
        }
        Ok(Self {
            scope,
            limits,
            policy,
            state: Mutex::new(State::default()),
        })
    }

    /// Local composition ONLY: add a new synthetic Agent at generation one.
    /// Existing IDs conflict; they cannot reset a binding/eligibility epoch.
    pub async fn register_agent(&self, bot_id: &str) -> AgentInvokeResult<()> {
        if bot_id.trim().is_empty() || bot_id.starts_with("human_") {
            return Err(AgentInvokeError::InvalidInput);
        }
        let mut state = self.state.lock().await;
        if state.bots.contains_key(bot_id) {
            return Err(AgentInvokeError::Conflict);
        }
        let revision = next(state.revision)?.get();
        let generation = next(0)?;
        state.bots.insert(
            bot_id.into(),
            AgentInvokeBotState {
                bot_id: bot_id.into(),
                binding_generation: generation,
                eligibility_generation: generation,
                eligible: true,
                active: true,
                hidden: false,
            },
        );
        state.revision = revision;
        Ok(())
    }

    /// Local authority provider input; not exposed as an Agent mutation API.
    pub async fn set_role(
        &self,
        human_actor_id: &str,
        bot_id: &str,
        role: Option<AgentInvokeRole>,
    ) -> AgentInvokeResult<()> {
        if !human_actor_id.starts_with("human_") || human_actor_id.len() <= 6 {
            return Err(AgentInvokeError::InvalidInput);
        }
        let mut state = self.state.lock().await;
        if !state.bots.contains_key(bot_id) {
            return Err(AgentInvokeError::NotFound);
        }
        let key = (human_actor_id.into(), bot_id.into());
        if state.roles.get(&key).copied() == role {
            return Ok(());
        }
        if role == Some(AgentInvokeRole::Owner)
            && state.roles.iter().any(|((human, bot), role)| {
                bot == bot_id && human != human_actor_id && *role == AgentInvokeRole::Owner
            })
        {
            return Err(AgentInvokeError::Conflict);
        }
        let revision = next(state.revision)?.get();
        let generation = next(state.role_generations.get(&key).copied().unwrap_or(0))?.get();
        state.role_generations.insert(key.clone(), generation);
        match role {
            Some(role) => {
                state.roles.insert(key, role);
            }
            None => {
                state.roles.remove(&key);
            }
        }
        state.revision = revision;
        Ok(())
    }

    /// Governance delegation is exact principal + pair, never an implicit role.
    pub async fn set_delegation(
        &self,
        service_id: &str,
        pair: AgentInvokePair,
        actions: Vec<AgentInvokeAction>,
    ) -> AgentInvokeResult<()> {
        if service_id.trim().is_empty() {
            return Err(AgentInvokeError::InvalidInput);
        }
        let mut state = self.state.lock().await;
        if !state.bots.contains_key(&pair.from_bot_id) || !state.bots.contains_key(&pair.to_bot_id)
        {
            return Err(AgentInvokeError::NotFound);
        }
        let revision = next(state.revision)?.get();
        let key = (service_id.into(), pair);
        let generation = next(state.delegation_generations.get(&key).copied().unwrap_or(0))?.get();
        state.delegation_generations.insert(key.clone(), generation);
        state.delegations.insert(key, actions);
        state.revision = revision;
        Ok(())
    }

    /// Simulate a trusted qualification/liveness/visibility or identity change.
    /// Any real change advances the corresponding epoch and revokes every
    /// affected approved edge BEFORE the new facts become visible. Restoration
    /// cannot revive an old edge; a new approval/key/version is required.
    pub async fn publish_agent_state(
        &self,
        bot_id: &str,
        is_eligible: bool,
        active: bool,
        hidden: bool,
        rebind: bool,
    ) -> AgentInvokeResult<()> {
        let mut state = self.state.lock().await;
        let mut bot = state
            .bots
            .get(bot_id)
            .cloned()
            .ok_or(AgentInvokeError::NotFound)?;
        let qualification_changed =
            (bot.eligible, bot.active, bot.hidden) != (is_eligible, active, hidden);
        if !qualification_changed && !rebind {
            return Ok(());
        }
        let revision = next(state.revision)?.get();
        if qualification_changed {
            bot.eligibility_generation = next(bot.eligibility_generation.get())?;
        }
        if rebind {
            bot.binding_generation = next(bot.binding_generation.get())?;
        }
        bot.eligible = is_eligible;
        bot.active = active;
        bot.hidden = hidden;
        // Precompute every fallible increment before changing any state.
        let mut sequence = state.next_action;
        let mut changes = Vec::new();
        for (pair, approval) in &state.edges {
            if (pair.from_bot_id == bot_id || pair.to_bot_id == bot_id)
                && approval.edge.status == bcs_domain::edge_permission::EdgeStatus::Approved
            {
                let before = edge_view(approval)?;
                let mut revoked = approval.clone();
                revoked.edge.status = bcs_domain::edge_permission::EdgeStatus::Revoked;
                revoked.version = next(approval.version.get())?;
                sequence = next(sequence)?.get();
                let audit = LocalAgentInvokeAudit {
                    action_id: format!("local-action-{sequence}"),
                    principal: AgentInvokePrincipal::Governance("synthetic-inventory".into()),
                    pair: pair.clone(),
                    action: LocalAgentInvokeAuditAction::QualificationInvalidated,
                    reason: "trusted qualification/binding change".into(),
                    before_grant: Some(before),
                    after_grant: Some(edge_view(&revoked)?),
                    before_request: None,
                    after_request: None,
                };
                changes.push((pair.clone(), revoked, audit));
            }
        }
        for (pair, approval, audit) in changes {
            state.edges.insert(pair, approval);
            state.audits.push(audit);
        }
        state.next_action = sequence;
        state.bots.insert(bot_id.into(), bot);
        state.revision = revision;
        Ok(())
    }

    pub async fn audit(&self) -> Vec<LocalAgentInvokeAudit> {
        self.state.lock().await.audits.clone()
    }

    fn require_scope(&self, scope: &AgentInvokeScope) -> AgentInvokeResult<()> {
        if scope != &self.scope {
            return Err(AgentInvokeError::NotFound);
        }
        Ok(())
    }

    fn has_source_authority(
        state: &State,
        principal: &AgentInvokePrincipal,
        pair: &AgentInvokePair,
    ) -> bool {
        match principal {
            AgentInvokePrincipal::Human(id) => state
                .roles
                .contains_key(&(id.clone(), pair.from_bot_id.clone())),
            AgentInvokePrincipal::Governance(id) => state
                .delegations
                .get(&(id.clone(), pair.clone()))
                .is_some_and(|actions| actions.contains(&AgentInvokeAction::Request)),
            AgentInvokePrincipal::Agent(_) => false,
        }
    }

    fn source_authority_generation(
        state: &State,
        principal: &AgentInvokePrincipal,
        pair: &AgentInvokePair,
    ) -> u64 {
        match principal {
            AgentInvokePrincipal::Human(id) => state
                .role_generations
                .get(&(id.clone(), pair.from_bot_id.clone()))
                .copied()
                .unwrap_or(0),
            AgentInvokePrincipal::Governance(id) => state
                .delegation_generations
                .get(&(id.clone(), pair.clone()))
                .copied()
                .unwrap_or(0),
            AgentInvokePrincipal::Agent(_) => 0,
        }
    }

    fn pending_count(state: &State, bot_id: &str, outbound: bool) -> usize {
        state
            .requests
            .values()
            .filter(|r| {
                r.view.status == RequestStatus::Pending
                    && (if outbound {
                        &r.pair.from_bot_id
                    } else {
                        &r.pair.to_bot_id
                    }) == bot_id
            })
            .count()
    }
}
