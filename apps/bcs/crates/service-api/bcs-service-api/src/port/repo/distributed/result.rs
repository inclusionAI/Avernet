use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SavedResult {
    Saved(BlobRef),
    AlreadySaved(BlobRef),
    ContentConflict,
}

#[async_trait]
pub trait ResultStorePort: Send + Sync {
    /// Save complete authorized final record before terminal publication.
    /// Same identity with different content is NOT successful save-if-absent.
    async fn save_final(
        &self,
        source: EffectSource,
        record: FinalTaskRecord,
    ) -> Result<SavedResult, StoreError>;
    async fn get_final(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<FinalTaskRecord>, StoreError>;
}

#[async_trait]
pub trait BlobStorePort: Send + Sync {
    /// Must reject changed bytes/digest under an existing immutable identity.
    async fn put_immutable(
        &self,
        identity: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError>;
    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError>;
}
