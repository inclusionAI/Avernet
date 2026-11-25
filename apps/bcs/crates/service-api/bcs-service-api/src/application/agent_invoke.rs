//! Independent Agent invocation use cases. Delivery calls these contracts, not
//! legacy friend/admission APIs or raw edge insert/upsert methods.
use async_trait::async_trait;

// Delivery imports the application contract and its credential-free input/
// result vocabulary, never Core, repository ports or concrete implementations.
pub use crate::types::agent_invoke::{
    AgentInvocationAuthentication, AgentInvokeAuthenticatedSource, AgentInvokeRegistrationBinding, AgentInvokeVerifiedBot,
    AgentInvocationInput, AgentInvocationPermit, AgentInvokeOperation, AgentInvokeCommand, AgentInvokeReceipt,
    AgentInvokeResult, AgentInvokeScope, AgentInvokePrincipal, AgentInvokePair,
    AgentInvokeChange, AgentInvokeError, AgentInvokeGrantView, AgentInvokeRequestView,
};
pub use crate::types::agent_invoke_query::*;
pub use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};


#[async_trait]
pub trait AgentInvocationService: Send + Sync {
    /// A decision for ONE direct operation. New operations re-read current
    /// facts; the result is not a reusable positive authorization cache.
    async fn authorize_invocation(
        &self,
        input: AgentInvocationInput,
    ) -> AgentInvokeResult<AgentInvocationPermit>;
}

#[async_trait]
pub trait AgentInvokeGrantManagementService: Send + Sync {
    /// Request/grant/approve/reject/cancel/revoke with one atomic audited,
    /// version-fenced command. A success requires durable audit and receipt.
    /// Unknown commit outcomes are surfaced; callers recover with the SAME key.
    async fn change(&self, command: AgentInvokeCommand) -> AgentInvokeResult<AgentInvokeReceipt>;
}

/// Read-only governance, distinct from invocation permission and write commands.
#[async_trait]
pub trait AgentInvokeGovernanceQueryService: Send + Sync {
    async fn read(&self, query: AgentInvokeQuery)
        -> AgentInvokeResult<AgentInvokeQueryResult>;
}

/// Narrow public direct-call command. Caller/scope are authentication-bound
/// data supplied by delivery; all remaining fields are ordinary request data.
#[derive(Debug, Clone)]
pub struct AgentInvokeDirectCommand {
    pub identity: AgentInvokeVerifiedBot,
    pub authenticated_scope: AgentInvokeScope,
    pub to_bot_id: String,
    pub message: String,
    pub session_id: Option<String>,
    pub timeout_ms: Option<u64>,
}

#[derive(Debug, Clone, Copy)]
pub struct AgentInvokeDirectLimits {
    pub default_timeout_ms: std::num::NonZeroU64,
    pub max_timeout_ms: std::num::NonZeroU64,
}

#[async_trait]
pub trait AgentInvokeDirectService: Send + Sync {
    /// Resolve verified Bot claims against the current scoped physical row,
    /// pin generations, then call ONLY the explicit runtime capability. No
    /// anonymous/auto-claim, implicit owner delegation or automatic retry.
    async fn invoke(&self, command: AgentInvokeDirectCommand)
        -> AgentInvokeResult<super::a2a_chat::AsyncA2aChatAccepted>;
}
