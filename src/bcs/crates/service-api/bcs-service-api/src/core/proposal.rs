use async_trait::async_trait;

pub use bcs_domain::GroupChatProposal;

/// Service for proposal management.
#[async_trait]
pub trait ProposalCoreService: Send + Sync {
    /// Store a proposal.
    async fn store(&self, proposal: GroupChatProposal) -> String;

    /// Get a proposal by token.
    async fn get(&self, token: &str) -> Option<GroupChatProposal>;

    /// Take (remove and return) a proposal.
    async fn take(&self, token: &str) -> Option<GroupChatProposal>;

    /// Clean up expired proposals.
    async fn cleanup_expired(&self) -> usize;
}
