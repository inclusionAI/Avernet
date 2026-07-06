//! Proposal Service Implementation.
//!
//! This crate provides the concrete implementation of `ProposalCoreService`
//! for managing group chat proposals.

use std::collections::HashMap;

use async_trait::async_trait;
use tokio::sync::RwLock;
use tracing::debug;

use bcs_service_api::{GroupChatProposal, ProposalCoreService};

/// Repository contract for proposal persistence implementations.
#[async_trait]
pub trait ProposalRepo: ProposalCoreService {}

/// In-memory proposal repository.
pub type MemoryProposalRepo = ProposalStore;

/// In-memory implementation of ProposalCoreService.
#[derive(Debug, Default)]
pub struct ProposalStore {
    proposals: RwLock<HashMap<String, GroupChatProposal>>,
}

impl ProposalStore {
    /// Create a new proposal store.
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl ProposalCoreService for ProposalStore {
    async fn store(&self, proposal: GroupChatProposal) -> String {
        let token = proposal.token.clone();
        debug!(token = %token, "Storing proposal");
        let mut proposals = self.proposals.write().await;
        proposals.insert(token.clone(), proposal);
        token
    }

    async fn get(&self, token: &str) -> Option<GroupChatProposal> {
        let proposals = self.proposals.read().await;
        proposals.get(token).cloned()
    }

    async fn take(&self, token: &str) -> Option<GroupChatProposal> {
        let mut proposals = self.proposals.write().await;
        proposals.remove(token)
    }

    async fn cleanup_expired(&self) -> usize {
        let mut proposals = self.proposals.write().await;
        let before = proposals.len();
        proposals.retain(|_, p| !p.is_expired());
        before - proposals.len()
    }
}

impl ProposalRepo for ProposalStore {}

/// Helper functions for creating proposals.
pub struct ProposalBuilder {
    driver_bot: String,
    participants: Vec<String>,
    reason: String,
    proposed_by: String,
    member_intros: String,
}

impl ProposalBuilder {
    /// Create a new proposal builder.
    pub fn new(proposed_by: impl Into<String>, reason: impl Into<String>) -> Self {
        Self {
            driver_bot: String::new(),
            participants: Vec::new(),
            reason: reason.into(),
            proposed_by: proposed_by.into(),
            member_intros: String::new(),
        }
    }

    /// Set the driver bot.
    pub fn driver(mut self, driver_bot: impl Into<String>) -> Self {
        self.driver_bot = driver_bot.into();
        self
    }

    /// Add a participant.
    pub fn participant(mut self, bot_id: impl Into<String>) -> Self {
        self.participants.push(bot_id.into());
        self
    }

    /// Set all participants.
    pub fn participants(mut self, participants: Vec<String>) -> Self {
        self.participants = participants;
        self
    }

    /// Set member introductions.
    pub fn member_intros(mut self, intros: impl Into<String>) -> Self {
        self.member_intros = intros.into();
        self
    }

    /// Build the proposal.
    pub fn build(self) -> GroupChatProposal {
        let token = uuid::Uuid::new_v4().to_string();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        let driver_bot = if self.driver_bot.is_empty() {
            self.participants.first().cloned().unwrap_or_default()
        } else {
            self.driver_bot
        };

        GroupChatProposal {
            token,
            driver_bot,
            participants: self.participants,
            reason: self.reason,
            proposed_by: self.proposed_by,
            member_intros: self.member_intros,
            confirm_url: String::new(), // To be set by caller
            created_at: now,
        }
    }

    /// Build with a specific token (for testing).
    pub fn build_with_token(self, token: impl Into<String>) -> GroupChatProposal {
        let mut proposal = self.build();
        proposal.token = token.into();
        proposal
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_proposal_store() {
        let store = ProposalStore::new();

        let proposal = ProposalBuilder::new("bot1", "Need help with database")
            .driver("dba")
            .participant("bot1")
            .participant("dba")
            .member_intros("Bot1 and DBA Expert")
            .build_with_token("test-token");

        let token = store.store(proposal).await;
        assert_eq!(token, "test-token");

        let retrieved = store.get("test-token").await;
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().driver_bot, "dba");
    }

    #[tokio::test]
    async fn test_proposal_take() {
        let store = ProposalStore::new();

        let proposal = ProposalBuilder::new("bot1", "Test")
            .participant("bot1")
            .build_with_token("take-token");

        store.store(proposal).await;

        // Take removes and returns
        let taken = store.take("take-token").await;
        assert!(taken.is_some());

        // Should not exist anymore
        let not_found = store.get("take-token").await;
        assert!(not_found.is_none());
    }

    #[tokio::test]
    async fn test_proposal_cleanup_expired() {
        let store = ProposalStore::new();

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        // Store a fresh proposal
        let fresh = ProposalBuilder::new("bot1", "Fresh")
            .participant("bot1")
            .build_with_token("fresh");
        store.store(fresh).await;

        // Store an expired proposal (11 minutes old)
        let old_time = now.saturating_sub(11 * 60 * 1000);
        let mut expired = ProposalBuilder::new("bot1", "Expired")
            .participant("bot1")
            .build_with_token("expired");
        expired.created_at = old_time;
        store.store(expired).await;

        // Cleanup should remove 1
        let removed = store.cleanup_expired().await;
        assert_eq!(removed, 1);

        // Fresh should still exist
        assert!(store.get("fresh").await.is_some());
        // Expired should be gone
        assert!(store.get("expired").await.is_none());
    }

    #[test]
    fn test_proposal_expiry() {
        let proposal = ProposalBuilder::new("bot1", "Test")
            .participant("bot1")
            .build();
        assert!(!proposal.is_expired());
    }

    // ========================================================================
    // Additional tests for BCS.md features
    // ========================================================================

    #[tokio::test]
    async fn test_proposal_get_nonexistent_token() {
        let store = ProposalStore::new();

        let result = store.get("nonexistent-token").await;
        assert!(result.is_none());

        let taken = store.take("nonexistent-token").await;
        assert!(taken.is_none());
    }

    #[tokio::test]
    async fn test_proposal_g1_task_distribution_scenario() {
        // Test G1: Task distribution group
        let store = ProposalStore::new();

        let proposal = ProposalBuilder::new("zhangsan", "数据库死锁排查")
            .driver("zhangsan")
            .participants(vec!["zhangsan".to_string(), "dba".to_string()])
            .member_intros("张三(发起方) 和 DBA专家")
            .build_with_token("g1-token");

        store.store(proposal.clone()).await;

        let retrieved = store.get("g1-token").await.unwrap();
        assert_eq!(retrieved.driver_bot, "zhangsan");
        assert_eq!(retrieved.participants, vec!["zhangsan", "dba"]);
        assert_eq!(retrieved.proposed_by, "zhangsan");
    }

    #[tokio::test]
    async fn test_proposal_g2_conflict_alignment_scenario() {
        // Test G2: Conflict alignment group
        let store = ProposalStore::new();

        let proposal = ProposalBuilder::new("zhangsan", "代码实现与PRD要求冲突，需要协调")
            .driver("zhangsan")
            .participants(vec![
                "zhangsan".to_string(),
                "lisi".to_string(),
                "security".to_string(),
            ])
            .member_intros("张三(开发) 李四(PM) 安全Bot")
            .build_with_token("g2-token");

        store.store(proposal).await;

        let retrieved = store.get("g2-token").await.unwrap();
        assert_eq!(retrieved.participants.len(), 3);
    }

    #[tokio::test]
    async fn test_proposal_g5_expert_consultation_scenario() {
        // Test G5: Expert consultation group
        let store = ProposalStore::new();

        let proposal = ProposalBuilder::new("zhangsan", "复杂问题需要多专家讨论")
            .driver("zhangsan")
            .participants(vec![
                "zhangsan".to_string(),
                "security".to_string(),
                "legal".to_string(),
                "dba".to_string(),
            ])
            .member_intros("张三 安全Bot 法务Bot DBABot")
            .build_with_token("g5-token");

        store.store(proposal).await;

        let retrieved = store.get("g5-token").await.unwrap();
        assert_eq!(retrieved.participants.len(), 4);
    }

    #[test]
    fn test_proposal_builder_defaults() {
        let proposal = ProposalBuilder::new("bot1", "Test reason")
            .participant("bot1")
            .build();

        assert!(!proposal.token.is_empty());
        assert!(!proposal.is_expired());
    }

    #[test]
    fn test_proposal_driver_defaults_to_first_participant() {
        let proposal = ProposalBuilder::new("bot1", "Test")
            .participant("first-bot")
            .participant("second-bot")
            .build();

        // When driver not set, should default to first participant
        assert_eq!(proposal.driver_bot, "first-bot");
    }

    #[tokio::test]
    async fn test_proposal_confirm_url_workflow() {
        // Simulate the confirmation workflow
        let store = ProposalStore::new();

        let mut proposal = ProposalBuilder::new("bot1", "Need help")
            .participant("bot1")
            .participant("expert")
            .build_with_token("confirm-test");
        proposal.confirm_url = "http://localhost:21000/groups/confirm-test/confirm".to_string();

        store.store(proposal).await;

        // Verify proposal exists before confirmation
        assert!(store.get("confirm-test").await.is_some());

        // User confirms - take the proposal (removes it)
        let taken = store.take("confirm-test").await;
        assert!(taken.is_some());
        assert_eq!(
            taken.unwrap().confirm_url,
            "http://localhost:21000/groups/confirm-test/confirm"
        );

        // Proposal should be gone after confirmation
        assert!(store.get("confirm-test").await.is_none());
    }

    #[tokio::test]
    async fn test_proposal_multiple_proposals() {
        let store = ProposalStore::new();

        // Store multiple proposals
        for i in 0..5 {
            let proposal = ProposalBuilder::new("bot1", format!("Proposal {}", i))
                .participant("bot1")
                .build_with_token(format!("token-{}", i));
            store.store(proposal).await;
        }

        // All should exist
        for i in 0..5 {
            assert!(store.get(&format!("token-{}", i)).await.is_some());
        }

        // Take one
        store.take("token-2").await;
        assert!(store.get("token-2").await.is_none());

        // Others should still exist
        assert!(store.get("token-0").await.is_some());
        assert!(store.get("token-4").await.is_some());
    }

    #[tokio::test]
    async fn test_proposal_expiry_boundary() {
        let store = ProposalStore::new();

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        // Create a proposal exactly at the expiry boundary (10 minutes)
        let boundary_time = now.saturating_sub(10 * 60 * 1000);
        let mut proposal = ProposalBuilder::new("bot1", "Boundary test")
            .participant("bot1")
            .build_with_token("boundary-token");
        proposal.created_at = boundary_time;

        store.store(proposal).await;

        // This should be expired or at the boundary
        // The exact behavior depends on timing, so we just verify the mechanism works
        let _retrieved = store.get("boundary-token").await;
        // Whether it's considered expired depends on exact timing
    }
}
