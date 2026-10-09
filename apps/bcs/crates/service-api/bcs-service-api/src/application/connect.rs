//! `ConnectService` — inbound use case for friend connect lifecycle.
//!
//! Route-facing; called by `routes/friends.rs`. Orchestrates `PermissionRequestRepo`,
//! `EdgeGrantRepo`, `PermissionProfileRepo` (wired in a later installment).
//!
//! Plan Task 12 (spec §12.5 + §12.2): every WRITE of the friend lifecycle
//! carries the REQUIRED [`BotOperationContext`] supplied by the delivery
//! adapter after caller authentication — the store writes the business row
//! and its `bcs_bot_action_audits` record in the same transaction, and the
//! lane never invents (or silently downgrades to System) an operator. The
//! connect lane also owns the acting-actor authorization question: a Human
//! may only act as a Bot the CURRENT authority facts (owner/manager) cover,
//! never via the legacy `created_by` fallback or a Bot-ID suffix. Role
//! lifecycle audits (`bot_manager_changes`) are a different table and are
//! never touched here (permission-request decisions and role mutations stay
//! separate lifecycles, spec §12.3).
use async_trait::async_trait;
use bcs_domain::edge_permission::{FriendListEntry, PermissionRequest, RequestStatus};

use crate::core::error::ServiceResult;
use crate::principal::RequestAuthHeaders;
use crate::types::BotOperationContext;
pub use crate::port::repo::edge_grant::FriendListQuery;

/// Enriched page; ordering and filtered total are supplied by the repository.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FriendEntriesPage {
    pub items: Vec<FriendListEntry>,
    pub total: u64,
}

/// Outcome of `create_connect`. Mirrors `POST /friends/request` response.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConnectStatus {
    Pending,
    Approved,
    /// Historical runtime admission result for public discoverability;
    /// explicit friend-add auto-approval should still create durable edges.
    PublicNoEdge,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConnectResult {
    pub request_ids: Vec<String>,
    pub edge_ids: Vec<u64>,
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
    /// Whether the verified Human `staff_no` may act as `requested_actor_id`
    /// (plan Task 12, spec §12.1(5)/§12.4). This is the application-owned
    /// acting-actor question: the adapter resolves IDENTITY ONLY and this
    /// service answers the authorization from the CURRENT authority facts
    /// (owner/manager role on the exact Bot), never from `created_by`, a
    /// Bot-ID suffix or a signed claim. Fail-closed default: an unconfigured
    /// authority answer denies.
    async fn authorize_acting_actor(
        &self,
        _staff_no: &str,
        _requested_actor_id: &str,
    ) -> ServiceResult<bool> {
        Ok(false)
    }

    /// Human→Bot: 1 request (+1 edge on approve). Bot↔Bot: 2 requests (+2 edges).
    ///
    /// `operation` is the REQUIRED audit context of this use case (spec
    /// §12.5); a new command missing its context is rejected, never
    /// recorded as a forged System operator.
    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
        request_auth: Option<RequestAuthHeaders>,
        operation: BotOperationContext,
    ) -> ServiceResult<ConnectResult>;

    /// Owner (or auto) approves; same-tx builds edge(s) + back-fills request.edge_id.
    /// Returns created edge_ids. Idempotent on already-approved.
    ///
    /// `request_auth` carries the inbound HTTP principal headers so the
    /// friend-auth-sync trigger (Task 11b) can forward them to the backend.
    async fn approve(
        &self,
        request_id: &str,
        decider: &str,
        request_auth: Option<RequestAuthHeaders>,
        operation: BotOperationContext,
    ) -> ServiceResult<Vec<u64>>;

    async fn reject(
        &self,
        request_id: &str,
        decider: &str,
        reason: Option<String>,
        operation: BotOperationContext,
    ) -> ServiceResult<()>;

    /// Caller withdraws a pending request. `operator` is the acting actor
    /// id, verified by the service against the request's creator before the
    /// decision persists.
    async fn cancel(
        &self,
        request_id: &str,
        operator: &str,
        operation: BotOperationContext,
    ) -> ServiceResult<()>;

    /// Fetch a request by id for delivery-layer authorization checks.
    async fn get_request(&self, request_id: &str) -> ServiceResult<PermissionRequest>;

    /// Unfriend: revoke friend edge(s) only (human→bot 1 / bot↔bot 2). Other edges untouched.
    /// Returns the revoked edge_ids.
    ///
    /// `request_auth` carries the inbound HTTP principal headers so the
    /// friend-auth-sync revoke trigger (Task 11d) can forward them to the backend.
    async fn revoke_friend(
        &self,
        caller: &str,
        target: &str,
        request_auth: Option<RequestAuthHeaders>,
        operation: BotOperationContext,
    ) -> ServiceResult<Vec<u64>>;

    /// Friend list (any direction, default-profile edge), enriched.
    async fn list_friends(&self, actor: &str) -> ServiceResult<Vec<FriendListEntry>>;

    /// Bounded friend list with the same directional semantics as list_friends.
    /// Filtering and pagination MUST run in the repository, not on a full list.
    async fn list_friends_paginated(
        &self,
        actor: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendEntriesPage>;

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
