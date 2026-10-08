//! Group proposal use-case contracts.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{GroupChatProposal, ServiceError};

use super::group_management::GroupUseCaseError;

/// Application-owned context for a proposal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalContext {
    pub user_query: Option<String>,
    pub detected_gap: Option<String>,
    pub relevant_history: Vec<String>,
}

/// Request for creating a pending group-chat proposal.
#[derive(Debug, Clone)]
pub struct GroupProposalCreateCommand {
    pub caller_actor_id: Option<String>,
    pub driver_bot_id: String,
    pub suggested_driver_bot_id: Option<String>,
    pub suggested_participants: Vec<String>,
    pub topic: String,
    pub context: Option<ProposalContext>,
}

/// Request for confirming a pending group-chat proposal.
#[derive(Debug, Clone)]
pub struct GroupProposalConfirmCommand {
    pub caller_actor_id: Option<String>,
    pub token: String,
}

/// Response payload for a successful group proposal creation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupProposalCreateResult {
    pub proposal_created: bool,
    pub driver_bot_id: String,
    pub participant_bot_ids: Vec<String>,
    pub member_intros: String,
    pub confirm_url: String,
    pub expires_in_seconds: u64,
    pub message: String,
}

/// Response payload for a successful proposal confirmation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupProposalConfirmResult {
    pub created: bool,
    pub group_id: String,
    pub driver_bot_id: String,
    pub participant_bot_ids: Vec<String>,
    pub chat_url: Option<String>,
    pub session_id: String,
    pub context_injected: u64,
}

#[derive(Debug, Clone)]
pub struct GroupProposalPreviewCommand {
    pub token: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupProposalPreviewResult {
    pub token: String,
    pub proposal: GroupChatProposal,
}

/// Group proposal application service.
#[async_trait]
pub trait GroupProposalService: Send + Sync {
    async fn create_proposal(
        &self,
        cmd: GroupProposalCreateCommand,
    ) -> Result<GroupProposalCreateResult, GroupUseCaseError>;

    async fn confirm_proposal(
        &self,
        cmd: GroupProposalConfirmCommand,
    ) -> Result<GroupProposalConfirmResult, GroupUseCaseError>;

    async fn preview_proposal(
        &self,
        _cmd: GroupProposalPreviewCommand,
    ) -> Result<GroupProposalPreviewResult, GroupUseCaseError> {
        Err(group_proposal_not_configured().into())
    }
}

fn group_proposal_not_configured() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "group proposal service is not configured".to_string(),
        request_id: None,
    }
}
