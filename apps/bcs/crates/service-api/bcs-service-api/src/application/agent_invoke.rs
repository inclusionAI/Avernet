//! Independent Agent invocation use cases. Delivery calls these contracts, not
//! legacy friend/admission APIs or raw edge insert/upsert methods.
use async_trait::async_trait;

use crate::types::agent_invoke::{
    AgentInvocationInput, AgentInvocationPermit, AgentInvokeCommand, AgentInvokeReceipt,
    AgentInvokeResult,
};

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
    async fn read(&self, query: crate::types::agent_invoke_query::AgentInvokeQuery)
        -> AgentInvokeResult<crate::types::agent_invoke_query::AgentInvokeQueryResult>;
}
