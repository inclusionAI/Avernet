//! Pure Agent invocation policy; implementations do not call external services.
use crate::types::agent_invoke::{
    AgentInvocationInput, AgentInvocationPermit, AgentInvokeCommand, AgentInvokeManagementSnapshot,
    AgentInvokeResult, AgentInvokeRuntimeSnapshot, AgentInvokeScope,
};

pub trait AgentInvokePolicyCoreService: Send + Sync {
    /// Validate ingress identity/scope/operation before any dependency read.
    fn validate_invocation(
        &self,
        scope: &AgentInvokeScope,
        input: &AgentInvocationInput,
    ) -> AgentInvokeResult<()>;

    fn authorize_invocation(
        &self,
        scope: &AgentInvokeScope,
        input: &AgentInvocationInput,
        snapshot: &AgentInvokeRuntimeSnapshot,
    ) -> AgentInvokeResult<AgentInvocationPermit>;

    fn validate_command(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
    ) -> AgentInvokeResult<()>;

    /// Authorization only. State transitions, optimistic versions, quota,
    /// eligibility/role fencing, audit and idempotency commit in the store.
    fn authorize_change(
        &self,
        scope: &AgentInvokeScope,
        command: &AgentInvokeCommand,
        snapshot: &AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<()>;
}
