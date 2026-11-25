//! Credential-free governance reads. Authentication/scope are trusted inputs,
//! not deserializable request fields. A stored approval is not an invoke permit.
use super::agent_invoke::*;
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
use std::num::{NonZeroU16, NonZeroU64};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentInvokeDirection {
    Outbound,
    Inbound,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryTarget {
    pub bot_id: String,
    /// None means the controlled Bot's full incident range. Governance only
    /// has exact pair delegations, so it must provide a peer. Audit is pair-only.
    pub peer_bot_id: Option<String>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentInvokeAuditAction {
    Request,
    Grant,
    Approve,
    Reject,
    Cancel,
    Revoke,
    QualificationPublish,
    QualificationInvalidate,
    DelegationPublish,
}
impl AgentInvokeAuditAction {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Request => "request",
            Self::Grant => "grant",
            Self::Approve => "approve",
            Self::Reject => "reject",
            Self::Cancel => "cancel",
            Self::Revoke => "revoke",
            Self::QualificationPublish => "qualification_publish",
            Self::QualificationInvalidate => "qualification_invalidate",
            Self::DelegationPublish => "delegation_publish",
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentInvokeQueryFilter {
    Grant {
        direction: AgentInvokeDirection,
        edge_id: NonZeroU64,
    },
    Request {
        direction: AgentInvokeDirection,
        request_id: String,
    },
    Grants {
        direction: AgentInvokeDirection,
        status: EdgeStatus,
    },
    Requests {
        direction: AgentInvokeDirection,
        status: RequestStatus,
    },
    Degrees,
    Audit {
        direction: AgentInvokeDirection,
        action: Option<AgentInvokeAuditAction>,
    },
}
impl AgentInvokeQueryFilter {
    pub fn direction(&self) -> Option<AgentInvokeDirection> {
        match self {
            Self::Grant { direction, .. }
            | Self::Request { direction, .. }
            | Self::Grants { direction, .. }
            | Self::Requests { direction, .. }
            | Self::Audit { direction, .. } => Some(*direction),
            Self::Degrees => None,
        }
    }
    pub fn is_page(&self) -> bool {
        matches!(
            self,
            Self::Grants { .. } | Self::Requests { .. } | Self::Audit { .. }
        )
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryContext {
    pub scope: AgentInvokeScope,
    pub principal: AgentInvokePrincipal,
    pub target: AgentInvokeQueryTarget,
    /// The resource fixes kind=agent_invoke; callers cannot select other grants.
    pub filter: AgentInvokeQueryFilter,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentInvokeCursorPosition {
    RowId(NonZeroU64),
    AuditTime { created_at_ms: u64, id: NonZeroU64 },
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryCursor {
    pub context: AgentInvokeQueryContext,
    pub position: AgentInvokeCursorPosition,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryPage {
    pub limit: NonZeroU16,
    pub cursor: Option<AgentInvokeQueryCursor>,
}
impl Default for AgentInvokeQueryPage {
    fn default() -> Self {
        Self {
            limit: match NonZeroU16::new(50) {
                Some(limit) => limit,
                None => unreachable!("positive constant"),
            },
            cursor: None,
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQuery {
    pub context: AgentInvokeQueryContext,
    /// Some is REQUIRED for paged resources and forbidden for exact/degree reads.
    pub page: Option<AgentInvokeQueryPage>,
}
/// Trusted current authority projection, independent of mutable Agent eligibility.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryFacts {
    pub context: AgentInvokeQueryContext,
    pub operator_live: bool,
    pub anchor_live: bool,
    pub ownership_version: u64,
    pub owner_count: u64,
    pub canonical_owner_count: u64,
    pub anchor_role: Option<AgentInvokeRole>,
    pub delegated_actions: Vec<AgentInvokeAction>,
    /// Degrees of an exact pair reveal both directions. Governance must hold
    /// Query delegation for BOTH directions; it never gets a whole-Bot degree.
    pub reverse_delegated_actions: Vec<AgentInvokeAction>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokePeerSummary {
    pub bot_id: String,
    /// Actual registration may be absent for retained history. Display fields
    /// are not identity, qualification or authorization proof.
    pub name: Option<String>,
    pub agent_code: Option<String>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeGrantRecord {
    pub grant: AgentInvokeGrantView,
    pub from: AgentInvokePeerSummary,
    pub to: AgentInvokePeerSummary,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeRequestRecord {
    pub request: AgentInvokeRequestView,
    pub from: AgentInvokePeerSummary,
    pub to: AgentInvokePeerSummary,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeAuditRecord {
    pub id: NonZeroU64,
    pub action_id: String,
    pub action: AgentInvokeAuditAction,
    pub pair: AgentInvokePair,
    pub principal_kind: String,
    pub principal_id: String,
    pub reason: String,
    pub created_at_ms: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentInvokeCountBasis {
    StoredApproved,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeDegrees {
    pub out_degree: u64,
    pub in_degree: u64,
    pub pending_out: u64,
    pub pending_in: u64,
    pub count_basis: AgentInvokeCountBasis,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryRows<T> {
    pub items: Vec<T>,
    pub next_cursor: Option<AgentInvokeQueryCursor>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentInvokeQueryData {
    Grant(AgentInvokeGrantRecord),
    Request(AgentInvokeRequestRecord),
    Grants(AgentInvokeQueryRows<AgentInvokeGrantRecord>),
    Requests(AgentInvokeQueryRows<AgentInvokeRequestRecord>),
    Degrees(AgentInvokeDegrees),
    Audit(AgentInvokeQueryRows<AgentInvokeAuditRecord>),
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentInvokeQueryResult {
    pub context: AgentInvokeQueryContext,
    pub snapshot_at_ms: u64,
    pub data: AgentInvokeQueryData,
}
