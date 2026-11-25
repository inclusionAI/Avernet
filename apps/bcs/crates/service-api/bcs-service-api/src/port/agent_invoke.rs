//! Replaceable, trusted Agent eligibility/identity facts. Implementations may
//! compose inventory and store reads, but MUST return a current fenced snapshot.
//! App/Core never depend on a host SDK or infer qualification from AgentCard.
use async_trait::async_trait;

use crate::types::agent_invoke::{
    AgentInvokeCommand, AgentInvokeManagementSnapshot, AgentInvokePair, AgentInvokeResult,
    AgentInvokeRuntimeSnapshot, AgentInvokeScope,
};

#[async_trait]
pub trait AgentInvokeFactsPort: Send + Sync {
    /// One bounded pair read; errors/unknown/malformed rows fail as a whole.
    /// Approval + durable identity/eligibility fences share one consistent
    /// snapshot. Current facts must not permit an earlier approval to revive
    /// after qualification loss/restoration. No public/default fallback.
    async fn runtime_snapshot(
        &self,
        scope: &AgentInvokeScope,
        pair: &AgentInvokePair,
    ) -> AgentInvokeResult<AgentInvokeRuntimeSnapshot>;

    /// Resolve current exact-pair Human roles or explicitly scoped governance
    /// delegation, plus requester identity for decisions/cancel. Missing or
    /// ambiguous identity is not synthesized. No legacy auto-claim.
    /// External calls occur before the write transaction, never under DB locks.
    async fn management_snapshot(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<AgentInvokeManagementSnapshot>;
}

/// Current credential-to-registration resolution. Exactly scoped physical
/// bot_uuid PLUS verified AgentCode; never a code-only or public auto-claim.
#[async_trait]
pub trait AgentInvokeIdentityResolutionPort: Send + Sync {
    async fn resolve_invocation_source(
        &self,
        scope: &AgentInvokeScope,
        identity: &crate::types::agent_invoke::AgentInvokeVerifiedBot,
    ) -> AgentInvokeResult<crate::types::agent_invoke::AgentInvokeAuthenticatedSource>;
}
