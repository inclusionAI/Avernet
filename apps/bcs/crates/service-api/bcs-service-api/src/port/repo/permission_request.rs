//! `PermissionRequestRepoPort` — persistence port for `permission_requests`.
//!
//! Plan Task 12 (spec §12.5): the INSERT/DECIDE write lanes carry the
//! REQUIRED [`BotOperationContext`] so the store commits the business row
//! and its `bcs_bot_action_audits` record in ONE transaction — a missing
//! context on a new command is rejected, never downgraded to System. These
//! records belong to the friend/invitation lifecycle only; the manager
//! role lifecycle lives in `bot_manager_changes` behind the authority
//! store and is never written from this port.
use async_trait::async_trait;
use bcs_domain::edge_permission::{PermissionRequest, RequestStatus};

use crate::core::error::ServiceResult;
use crate::types::BotOperationContext;

#[async_trait]
pub trait PermissionRequestRepoPort: Send + Sync {
    /// Insert one request row + its ordinary-business audit record in the
    /// same transaction. The audit step key is derived per request record
    /// from the caller-supplied operation (sub-operations for Bot↔Bot pairs
    /// are the caller's derivation, see `ConnectService`).
    async fn insert(&self, request: PermissionRequest, operation: &BotOperationContext) -> ServiceResult<()>;

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

    /// Decide one request row + its ordinary-business audit record in the
    /// same transaction (REQUIRED operation context, spec §12.5).
    async fn decide(
        &self,
        request_id: &str,
        env: &str,
        status: RequestStatus,
        decided_by: &str,
        decision_reason: Option<&str>,
        operation: &BotOperationContext,
    ) -> ServiceResult<()>;

    /// Back-fill `edge_id` after approval creates the edge, committing the
    /// row update and its ordinary-business audit record in the same
    /// transaction (REQUIRED operation context, plan Task 12).
    async fn backfill_edge_id(
        &self,
        request_id: &str,
        env: &str,
        edge_id: u64,
        operation: &BotOperationContext,
    ) -> ServiceResult<()>;
}
