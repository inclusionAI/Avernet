//! `SessionAuthorizationCoreServiceImpl` — the narrow
//! authorization snapshot Core (bot-authority plan Task 6.4,
//! spec §8.3 权威资源读取.
//!
//! Sessions have no baseline session-wide Core; this
//! narrow core forwards exactly one strict read to the
//! `SessionRepoPort`. It adds no fallback path: Core
//! behavior is "forward strictly" — no cache consultation,
//! no infallible `get` shadowing.

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::types::authorization_snapshot::SessionAuthorizationSnapshot;
use bcs_service_api::ServiceResult;

/// Forwards the strict Session authorization snapshot port
/// read; the repo decides `Ok(None)` versus `Err`.
pub struct SessionAuthorizationCoreImpl {
    repo: Arc<dyn SessionRepoPort>,
}

impl SessionAuthorizationCoreImpl {
    pub fn new(repo: Arc<dyn SessionRepoPort>) -> Self {
        Self { repo }
    }
}

#[async_trait]
impl SessionAuthorizationCoreService for SessionAuthorizationCoreImpl {
    async fn read_authorization_snapshot(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<SessionAuthorizationSnapshot>> {
        self.repo.read_authorization_snapshot(session_id).await
    }
}
