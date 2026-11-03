pub use crate::types::collaboration_draft::*;
use async_trait::async_trait;

#[derive(Debug, thiserror::Error)]
pub enum DraftCoreError {
    #[error("invalid draft input")]
    Invalid,
    #[error("draft reference unavailable")]
    Unavailable,
    #[error("draft management forbidden")]
    Forbidden,
    #[error("draft revision conflict")]
    Conflict,
    #[error("draft storage unavailable")]
    Storage,
}

#[async_trait]
pub trait CollaborationDraftCoreService: Send + Sync {
    async fn create(
        &self,
        owner: &str,
        id: &str,
        content: DraftContent,
    ) -> Result<SavedDraftRevision, DraftCoreError>;
    async fn update(
        &self,
        owner: &str,
        id: &str,
        expected: u64,
        content: DraftContent,
    ) -> Result<DraftRevision, DraftCoreError>;
    async fn metadata(&self, owner: &str, id: &str) -> Result<DraftMetadata, DraftCoreError>;
    async fn read(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<DraftRevision, DraftCoreError>;
    async fn reference(
        &self,
        owner: &str,
        id: &str,
        revision: u64,
    ) -> Result<DraftReference, DraftCoreError>;
    async fn revoke(&self, owner: &str, id: &str, revision: u64) -> Result<(), DraftCoreError>;
    async fn delete(&self, owner: &str, id: &str) -> Result<(), DraftCoreError>;
}
