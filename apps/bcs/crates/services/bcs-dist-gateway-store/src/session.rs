//! The `SessionStorePort` of a Gateway process: only the frozen spec read.

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::capability;
use bcs_dist_store::codec::decode_json;
use bcs_dist_store::error::from_read;
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::{SessionOwnership, StoreError};
use bcs_service_api::port::repo::distributed::ownership::ShardClaim;
use bcs_service_api::port::repo::distributed::session::*;
use bcs_store_api::StoreCapability;
use bcs_store_api::json::JsonStorePlugin;
use std::sync::Arc;

/// Code of a Worker operation called on the Gateway's session store.
pub const WORKER_ONLY: &str = "worker_only_operation";

/// Read-only session store of a Gateway. `get_spec` reads the frozen spec that
/// admission created. Acquiring, loading and committing a session belong to the
/// Worker; a Gateway never calls them, and this provider refuses them with
/// `NotApplied { code: "worker_only_operation" }` instead of touching `ckpt`.
pub struct GatewaySessionStore {
    json: Arc<dyn JsonStorePlugin>,
    layout: KeyLayout,
}

impl GatewaySessionStore {
    /// Fails when the store lacks `AtomicJson`.
    pub fn new(json: Arc<dyn JsonStorePlugin>, layout: KeyLayout) -> Result<Self, StoreError> {
        capability::require(&[(json.as_ref(), StoreCapability::AtomicJson)])?;
        Ok(Self { json, layout })
    }
}

fn worker_only<T>() -> Result<T, StoreError> {
    Err(StoreError::NotApplied {
        code: WORKER_ONLY.to_owned(),
    })
}

#[async_trait]
impl SessionStorePort for GatewaySessionStore {
    async fn get_spec(&self, sid: &Sid) -> Result<Option<SessionSpec>, StoreError> {
        let key = self.layout.spec(sid)?;
        match self.json.get_json(&key).await.map_err(|e| from_read(&e))? {
            Some(doc) => decode_json(doc.value).map(Some),
            None => Ok(None),
        }
    }

    async fn acquire(
        &self,
        _sid: &Sid,
        _claim: &ShardClaim,
        _initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError> {
        worker_only()
    }

    async fn load(&self, _sid: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError> {
        worker_only()
    }

    async fn commit(&self, _request: CommitRequest) -> Result<CommitOutcome, StoreError> {
        worker_only()
    }

    async fn resolve_commit(
        &self,
        _owner: &SessionOwnership,
        _commit_id: &CommitId,
        _digest: &Digest,
    ) -> Result<CommitResolution, StoreError> {
        worker_only()
    }

    async fn record_recovery_attempt(
        &self,
        _owner: &SessionOwnership,
        _expected_commit_seq: CommitSeq,
        _attempt: RequestId,
    ) -> Result<RecoveryUpdate, StoreError> {
        worker_only()
    }
}
