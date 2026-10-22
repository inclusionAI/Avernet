use crate::types::collaboration_draft::{DraftMetadata, StoredDraftRevision};
use async_trait::async_trait;

#[derive(Debug, thiserror::Error)]
pub enum DraftRepoError {
    #[error("draft write conflict")]
    Conflict,
    #[error("draft persistence failed")]
    Failed,
}

/// Draft IDs are unique within the configured database. Revision read grants are durable.
#[async_trait]
pub trait CollaborationDraftRepoPort: Send + Sync {
    async fn metadata(&self, id: &str) -> Result<Option<DraftMetadata>, DraftRepoError>;
    async fn revision(
        &self,
        id: &str,
        revision: u64,
    ) -> Result<Option<StoredDraftRevision>, DraftRepoError>;
    async fn create(
        &self,
        metadata: DraftMetadata,
        revision: StoredDraftRevision,
    ) -> Result<(), DraftRepoError>;
    /// Atomically advance a live owner's head and insert exactly expected + 1.
    async fn append(
        &self,
        owner: &str,
        expected: u64,
        revision: StoredDraftRevision,
    ) -> Result<(), DraftRepoError>;
    async fn revoke(&self, owner: &str, id: &str, revision: u64) -> Result<(), DraftRepoError>;
    /// Atomically tombstone the owner row and remove all source revisions.
    async fn delete(&self, owner: &str, id: &str) -> Result<(), DraftRepoError>;
}
