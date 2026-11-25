//! Agent invocation contracts. Trusted facts are deliberately NOT deserializable.
//! Namespaces, principal identity, roles and generations come from configured
//! authentication/fact providers, never a management or invocation request body.

use std::num::NonZeroU64;

use bcs_domain::edge_permission::{AuthzGrantRef, EdgeGrant, EdgeStatus, RequestStatus};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct AgentInvokeScope {
    /// A physically isolated store namespace, or a persisted tenant/domain key.
    /// `env` alone is not a multi-tenant isolation boundary.
    pub namespace: String,
    pub env: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct AgentInvokePair {
    pub from_bot_id: String,
    pub to_bot_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum AgentInvokePrincipal {
    Human(String),
    Agent(String),
    Governance(String),
}

impl AgentInvokePrincipal {
    pub fn id(&self) -> &str {
        match self {
            Self::Human(id) | Self::Agent(id) | Self::Governance(id) => id,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentInvokeOperation {
    DirectInvocation,
    DelegatedInvocation,
    ToolInvocation,
    GroupOperation,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvocationInput {
    pub principal: AgentInvokePrincipal,
    /// Scope of the verified ingress principal, not client-supplied metadata.
    pub authenticated_scope: AgentInvokeScope,
    pub pair: AgentInvokePair,
    /// Trusted lineage; MUST NOT be replaced by an asserted body originator.
    pub originator: String,
    pub operation: AgentInvokeOperation,
}

/// Current, credential-free facts for one exact Bot. Both generations must
/// durably advance on changes, including loss and restoration of eligibility.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeBotState {
    pub bot_id: String,
    pub binding_generation: NonZeroU64,
    pub eligibility_generation: NonZeroU64,
    pub eligible: bool,
    pub active: bool,
    pub hidden: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeApproval {
    pub edge: EdgeGrant,
    /// Approval/revocation generation, NOT the stored grant_ref_id sentinel.
    pub version: NonZeroU64,
    pub from_binding_generation: NonZeroU64,
    pub to_binding_generation: NonZeroU64,
    pub from_eligibility_generation: NonZeroU64,
    pub to_eligibility_generation: NonZeroU64,
}

/// A consistent, current snapshot. Providers must fence external eligibility
/// changes against the persisted approval read; sequential, unfenced reads or
/// cached `eligible=true` facts do not implement this contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeRuntimeSnapshot {
    pub scope: AgentInvokeScope,
    pub pair: AgentInvokePair,
    pub source: AgentInvokeBotState,
    pub target: AgentInvokeBotState,
    /// None is actual absence, never a dependency/decoding failure.
    pub approval: Option<AgentInvokeApproval>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentInvokeAction {
    Request,
    Grant,
    Approve,
    Reject,
    Cancel,
    Revoke,
    Query,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentInvokeRole {
    Owner,
    Manager,
}

/// Only the new kind is addressable. No caller-controlled kind, rules, ref,
/// status, actor stamp or originator policy can cross this API.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentInvokeChange {
    Grant {
        expected_version: u64,
    },
    Request,
    Approve {
        request_id: String,
        expected_version: NonZeroU64,
    },
    Reject {
        request_id: String,
        expected_version: NonZeroU64,
    },
    Cancel {
        request_id: String,
        expected_version: NonZeroU64,
    },
    Revoke {
        edge_id: NonZeroU64,
        expected_version: NonZeroU64,
    },
}

impl AgentInvokeChange {
    pub fn action(&self) -> AgentInvokeAction {
        match self {
            Self::Grant { .. } => AgentInvokeAction::Grant,
            Self::Request => AgentInvokeAction::Request,
            Self::Approve { .. } => AgentInvokeAction::Approve,
            Self::Reject { .. } => AgentInvokeAction::Reject,
            Self::Cancel { .. } => AgentInvokeAction::Cancel,
            Self::Revoke { .. } => AgentInvokeAction::Revoke,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeCommand {
    pub principal: AgentInvokePrincipal,
    pub authenticated_scope: AgentInvokeScope,
    pub pair: AgentInvokePair,
    pub change: AgentInvokeChange,
    pub idempotency_key: String,
    pub reason: String,
}

/// Trusted roles/delegations are scoped to exactly this principal and pair.
/// The opaque fence must cover Bot binding/eligibility, role/delegation and
/// requester ownership versions. The store revalidates it atomically at commit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeManagementSnapshot {
    pub scope: AgentInvokeScope,
    pub pair: AgentInvokePair,
    pub principal: AgentInvokePrincipal,
    pub source: AgentInvokeBotState,
    pub target: AgentInvokeBotState,
    pub source_role: Option<AgentInvokeRole>,
    pub target_role: Option<AgentInvokeRole>,
    pub delegated_actions: Vec<AgentInvokeAction>,
    /// A pending request's original authenticated creator, absent for a grant.
    pub request_creator: Option<AgentInvokePrincipal>,
    /// Current source authority of the ORIGINAL requester. Checked on a new
    /// approval in the atomic store, not inferred from the deciding actor.
    pub requester_source_authorized: bool,
    pub fence: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeGrantView {
    pub edge_id: NonZeroU64,
    pub status: EdgeStatus,
    pub version: NonZeroU64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeRequestView {
    pub request_id: String,
    pub status: RequestStatus,
    pub version: NonZeroU64,
    pub edge_id: Option<NonZeroU64>,
}

/// Current state is separate from the immutable original operation receipt.
/// Replaying an old approval after revoke MUST NOT approve the edge again.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeReceipt {
    pub action_id: String,
    pub replayed: bool,
    pub original_grant: Option<AgentInvokeGrantView>,
    pub original_request: Option<AgentInvokeRequestView>,
    pub current_grant: Option<AgentInvokeGrantView>,
    pub current_request: Option<AgentInvokeRequestView>,
}

/// Closed, credential-free failures. Adapters map these to transport errors;
/// raw DB/provider messages cannot become a successful empty result or leak.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum AgentInvokeError {
    #[error("invalid Agent invocation input")]
    InvalidInput,
    #[error("Agent invocation authentication required")]
    Unauthenticated,
    #[error("Agent invocation forbidden")]
    Forbidden,
    #[error("Agent invocation resource not found")]
    NotFound,
    #[error("Agent invocation state or version conflict")]
    Conflict,
    #[error("Agent invocation limit reached")]
    LimitExceeded,
    #[error("Agent invocation dependency unavailable")]
    Unavailable,
    #[error("Agent invocation commit outcome unknown")]
    CommitUnknown,
    #[error("Agent invocation state invalid")]
    InvalidState,
}

pub type AgentInvokeResult<T> = Result<T, AgentInvokeError>;
pub type AgentInvocationPermit = AuthzGrantRef;

/// Explicit, finite positive limits. No missing-value/unlimited convention.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AgentInvokeLimits {
    pub max_out_degree: std::num::NonZeroU32,
    pub max_in_degree: std::num::NonZeroU32,
    pub max_pending_out: std::num::NonZeroU32,
    pub max_pending_in: std::num::NonZeroU32,
}


/// Trusted host publication, not an ingress DTO. The host must durably publish
/// invalidation before acknowledging a binding/qualification change. Generations
/// cannot decrease or be reused after loss/restoration, including an expired
/// freshness lease. No human/API request may supply these assertions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeFactPublication {
    pub state: AgentInvokeBotState,
    /// Zero creates an absent fact, otherwise CAS the exact stored revision.
    pub expected_revision: u64,
    /// Server-verified freshness bound, checked against DB time and the
    /// configured finite maximum lease. TTL is NOT the sole revocation fence.
    pub valid_until_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokePublishedFact {
    pub state: AgentInvokeBotState,
    pub revision: NonZeroU64,
    pub valid_until_ms: u64,
}

/// Trusted delegation feed publication, never an ingress DTO. The authenticated
/// host publisher owns this input; `principal` is the delegate, not the issuer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeDelegationPublication {
    pub principal: AgentInvokePrincipal,
    pub pair: AgentInvokePair,
    pub expected_revision: u64,
    pub actions: Vec<AgentInvokeAction>,
    pub reason: String,
}
