//! `PermissionRequestRepoPort` — persistence port for `permission_requests`.
use async_trait::async_trait;
use bcs_domain::edge_permission::{PermissionRequest, RequestStatus};

use crate::core::error::ServiceResult;

#[async_trait]
pub trait PermissionRequestRepoPort: Send + Sync {
    async fn insert(&self, request: PermissionRequest) -> ServiceResult<()>;

    async fn get(&self, request_id: &str, env: &str) -> Option<PermissionRequest>;

    /// Owner inbox: requests whose `to_id == to_id` (optionally filtered by status).
    async fn list_inbox(
        &self,
        to_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest>;

    /// Sent outbox: requests whose `from_id == from_id` (optionally filtered
    /// by status). Mirrors [`Self::list_inbox`] but keyed on the sender. Used
    /// by the `Sent`/`All` directions of `ConnectService::list_requests`.
    async fn list_sent(
        &self,
        from_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> Vec<PermissionRequest>;

    async fn decide(
        &self,
        request_id: &str,
        env: &str,
        status: RequestStatus,
        decided_by: &str,
        decision_reason: Option<&str>,
    ) -> ServiceResult<()>;

    /// Back-fill `edge_id` after approval creates the edge.
    async fn backfill_edge_id(&self, request_id: &str, env: &str, edge_id: &str) -> ServiceResult<()>;
}
