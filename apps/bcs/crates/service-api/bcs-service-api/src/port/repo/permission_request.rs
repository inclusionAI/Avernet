//! `PermissionRequestRepoPort` — persistence port for `permission_requests`.
use async_trait::async_trait;
use bcs_domain::edge_permission::{PermissionRequest, RequestKind, RequestStatus};

use crate::core::error::ServiceResult;

#[async_trait]
pub trait PermissionRequestRepoPort: Send + Sync {
    async fn insert(&self, request: PermissionRequest) -> ServiceResult<()>;

    async fn get(&self, request_id: &str, env: &str) -> Option<PermissionRequest>;

    /// Bounded existence read: the most recent request `from_id -> to_id`
    /// of `request_kind` and `status` authored by `created_by` in `env`,
    /// if any.
    ///
    /// Serves duplicate-guard checks that must not materialize the actor's
    /// whole request history — implementations MUST bound the read at the
    /// storage layer (SQL `LIMIT 1`), never page an unbounded list and
    /// filter in memory.
    async fn find_request(
        &self,
        from_id: &str,
        to_id: &str,
        env: &str,
        request_kind: RequestKind,
        status: RequestStatus,
        created_by: &str,
    ) -> Option<PermissionRequest>;

    /// Query and row-decoding failures propagate; never return partial/empty success.
    /// Owner inbox: requests whose `to_id == to_id` (optionally filtered by status).
    async fn list_inbox(
        &self,
        to_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>>;

    /// Sent outbox: requests whose `from_id == from_id` (optionally filtered
    /// by status). Mirrors [`Self::list_inbox`] but keyed on the sender. Used
    /// by the `Sent`/`All` directions of `ConnectService::list_requests`.
    async fn list_sent(
        &self,
        from_id: &str,
        env: &str,
        status: Option<RequestStatus>,
    ) -> ServiceResult<Vec<PermissionRequest>>;

    async fn decide(
        &self,
        request_id: &str,
        env: &str,
        status: RequestStatus,
        decided_by: &str,
        decision_reason: Option<&str>,
    ) -> ServiceResult<()>;

    /// Back-fill `edge_id` after approval creates the edge.
    async fn backfill_edge_id(&self, request_id: &str, env: &str, edge_id: u64) -> ServiceResult<()>;
}
