use async_trait::async_trait;
use bcs_dist_model::BlobRef;
use bcs_dist_store::archive::DbArchiveStore;
use bcs_service_api::port::repo::distributed::{common::*, result::*};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
pub struct FaultBlob {
    pub inner: Arc<DbArchiveStore>,
    pub fail_read: AtomicBool,
}
#[async_trait]
impl BlobStorePort for FaultBlob {
    async fn put_immutable(
        &self,
        id: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError> {
        self.inner.put_immutable(id, bytes).await
    }
    async fn get(&self, r: &BlobRef) -> Result<Option<Vec<u8>>, StoreError> {
        if self.fail_read.swap(false, Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "injected_blob_read".into(),
            });
        }
        self.inner.get(r).await
    }
}
