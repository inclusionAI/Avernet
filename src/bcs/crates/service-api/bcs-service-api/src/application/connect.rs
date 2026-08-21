//! `ConnectService` — inbound use case for friend connect lifecycle.
//!
//! Route-facing; called by `routes/friends.rs`. Orchestrates `PermissionRequestRepo`,
//! `EdgeGrantRepo`, `PermissionProfileRepo` (wired in a later installment).
use async_trait::async_trait;
use bcs_domain::edge_permission::{FriendListEntry, PermissionRequest, RequestStatus};

use crate::core::error::ServiceResult;

/// Outcome of `create_connect`. Mirrors `POST /friends/request` response.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConnectStatus {
    Pending,
    Approved,
    /// Target is fully public — no edge needed (§6.2 runtime public_default).
    PublicNoEdge,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConnectResult {
    pub request_ids: Vec<String>,
    pub edge_ids: Vec<String>,
    pub status: ConnectStatus,
    pub auto_accepted: bool,
}

/// Direction filter for `list_requests` (mirrors `GET /friends/requests?direction=`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequestDirection {
    /// Requests received by the actor (default).
    Received,
    /// Requests sent by the actor.
    Sent,
    /// Both sent and received.
    All,
}

/// Paginated result of `ConnectService::list_requests`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestsPage {
    pub items: Vec<PermissionRequest>,
    pub total: u32,
    pub page: u32,
    pub page_size: u32,
}

#[async_trait]
pub trait ConnectService: Send + Sync {
    /// Human→Bot: 1 request (+1 edge on approve). Bot↔Bot: 2 requests (+2 edges).
    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
    ) -> ServiceResult<ConnectResult>;

    /// Owner (or auto) approves; same-tx builds edge(s) + back-fills request.edge_id.
    /// Returns created edge_ids. Idempotent on already-approved.
    async fn approve(&self, request_id: &str, decider: &str) -> ServiceResult<Vec<String>>;

    async fn reject(
        &self,
        request_id: &str,
        decider: &str,
        reason: Option<String>,
    ) -> ServiceResult<()>;

    /// Caller withdraws a pending request.
    async fn cancel(&self, request_id: &str) -> ServiceResult<()>;

    /// Fetch a request by id for delivery-layer authorization checks.
    async fn get_request(&self, request_id: &str) -> ServiceResult<PermissionRequest>;

    /// Unfriend: revoke friend edge(s) only (human→bot 1 / bot↔bot 2). Other edges untouched.
    /// Returns the revoked edge_ids.
    async fn revoke_friend(&self, caller: &str, target: &str) -> ServiceResult<Vec<String>>;

    /// Friend list (any direction, default-profile edge), enriched.
    async fn list_friends(&self, actor: &str) -> ServiceResult<Vec<FriendListEntry>>;

    /// Owner inbox / sent list (`GET /friends/requests`). Paginated.
    async fn list_requests(
        &self,
        actor: &str,
        direction: RequestDirection,
        status: Option<RequestStatus>,
        page: u32,
        page_size: u32,
    ) -> ServiceResult<RequestsPage>;

}
