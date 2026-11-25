use super::collaboration_runtime::CollaborationDefinitionValidationOutcome;
pub use crate::types::collaboration_draft::{
    DraftContent, DraftMetadata, DraftReference, DraftRevision, SavedDraftRevision,
};
use async_trait::async_trait;
use serde::Serialize;

#[derive(Serialize)]
pub struct DraftValidation {
    pub draft_id: String,
    pub revision: u64,
    pub content_hash: String,
    pub validated_at: String,
    pub validator: String,
    pub validation: CollaborationDefinitionValidationOutcome,
}

#[derive(Debug, thiserror::Error)]
pub enum DraftError {
    #[error("invalid draft input")]
    Invalid,
    #[error("draft reference unavailable")]
    Unavailable,
    #[error("draft management forbidden")]
    Forbidden,
    #[error("draft revision conflict")]
    Conflict,
    #[error("draft service unavailable")]
    ServiceUnavailable,
}

#[async_trait]
pub trait CollaborationDraftService: Send + Sync {
    async fn create(
        &self,
        owner: &str,
        id: &str,
        content: DraftContent,
    ) -> Result<SavedDraftRevision, DraftError>;
    async fn update(
        &self,
        owner: &str,
        id: &str,
        expected: u64,
        content: DraftContent,
    ) -> Result<DraftRevision, DraftError>;
    async fn metadata(&self, owner: &str, id: &str) -> Result<DraftMetadata, DraftError>;
    async fn read(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<DraftRevision, DraftError>;
    async fn validate(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<DraftValidation, DraftError>;
    async fn reference(
        &self,
        owner: &str,
        id: &str,
        revision: u64,
    ) -> Result<DraftReference, DraftError>;
    async fn revoke(&self, owner: &str, id: &str, revision: u64) -> Result<(), DraftError>;
    async fn delete(&self, owner: &str, id: &str) -> Result<(), DraftError>;
}
