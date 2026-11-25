//! Atomic Agent invocation lifecycle persistence contract (outbound business).
use async_trait::async_trait;

use crate::types::agent_invoke::{
    AgentInvokeCommand, AgentInvokeManagementSnapshot, AgentInvokeReceipt, AgentInvokeResult,
    AgentInvokeScope,
};

#[async_trait]
pub trait AgentInvokeCommandRepoPort: Send + Sync {
    // Explicitly declared synthetic in-process drivers may exercise the atomic
    // semantics without restart durability. They are not production stores;
    // production implementations must durably commit all effects below.
    /// Atomically revalidate the exact scope/principal/pair and the complete
    /// management fence, then apply expected_version, pending terminal-state
    /// checks and degree limits. New request/grant/approval requires both Bots
    /// active, eligible and non-hidden; a new approval also reauthorizes the
    /// ORIGINAL requester on the source. Replay recovery precedes mutable
    /// qualification/state checks but still requires current operator authority.
    /// Persist the edge/request, advanced approval
    /// generation, audit and idempotency receipt in ONE transaction.
    ///
    /// Same key + same canonical command returns the ORIGINAL receipt plus
    /// current state; a different command conflicts. Replays never mutate a
    /// revoked edge. Key scope includes namespace, env and principal. Grant
    /// restores the same unique (from,to,env,kind,ref=0) row with a NEW version.
    /// No reverse/mirror edges, friend writes, profile seed or authority edits.
    /// Revoke is exact edge_id+kind+scope and remains possible at quota limits
    /// or after Agent qualification loss. Request/edge IDs must match the pair.
    ///
    /// Fences and quota checks share the same serialization boundary as all
    /// competing grant/revoke/identity/role writers. Stores MUST NOT implement
    /// this by chaining existing insert/decide/revoke methods. Definite failed
    /// writes roll back all effects; timeout/unknown commit is CommitUnknown
    /// and never blindly retried. Raw DB/provider details stay in the store.
    async fn execute(
        &self,
        scope: &AgentInvokeScope,
        command: AgentInvokeCommand,
        snapshot: AgentInvokeManagementSnapshot,
    ) -> AgentInvokeResult<AgentInvokeReceipt>;
}
