//! Bot-authority Core service contract.
//!
//! Authoritative owner/manager reads (and, once the Store write paths
//! ship, the manager/transfer commands) over one bound environment.
//! The Core is the only layer the Hook delegates to; delivery adapters
//! never reach the Repo port. See the 2026-10-10 Bot authority design
//! §3/§5/§6 and implementation plan §3.

use async_trait::async_trait;

use crate::ServiceResult;
use crate::types::bot_authority::{
    BotAccessRelation, BotOwnershipView, BotRoleQuery, ManagerEdit, ManagerListQuery,
    ManagerPage, ManagerSetChange, ManagerSync, OwnershipChange, OwnershipSync,
    TransferCommand, TransferKey, TransferListQuery, TransferOutcome, TransferPage,
    TransferView,
};

/// Strict bot-authority reads and permission commands.
///
/// All methods operate on the environment bound into the
/// implementing instance (bootstrap-injected); user parameters cannot
/// widen that scope. Identity comparison is byte-exact
/// (case-sensitive); distinct principals stay distinct.
#[async_trait]
pub trait BotAuthorityCoreService: Send + Sync {
    /// Role of `user_id` (verified User ID) on `bot_id` in the bound
    /// environment.
    ///
    /// Strictness (spec §3.2, plan task 1.5): a missing/deleted bot, a
    /// missing/deleted target Human, no approved edge, or version 0
    /// reads deny with `Ok(None)`; a live bot with
    /// `ownership_version > 0` whose approved-owner state is missing
    /// or duplicated is a completeness error (`Err`) that must not be
    /// swallowed into a deny; storage failures are `Err`, never
    /// `Ok(None)`.
    async fn role(&self, user_id: &str, bot_id: &str) -> ServiceResult<Option<BotAccessRelation>>;

    /// Batch form of [`BotAuthorityCoreService::role`]: result is
    /// same length and order as `queries`. Implementations batch the
    /// owner and target-edge reads into bounded collection queries
    /// (at most [`crate::types::bot_authority::AUTHORITY_QUERY_CHUNK`]
    /// ids per statement) — they never call `role` once per input.
    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>>;

    /// Current owner state of a live, initialized bot. Version 0
    /// (uninitialized) and corrupt owner state are `Err`, not
    /// synthesized views.
    async fn ownership(&self, bot_id: &str) -> ServiceResult<BotOwnershipView>;

    /// Paged manager list with the owner summary (default limit 50,
    /// hard max 100).
    async fn managers(&self, query: ManagerListQuery) -> ServiceResult<ManagerPage>;

    /// One transfer row by key (spec §6.1: located by Bot ID + target
    /// Human actor ID, never a `transfer_id`), or `None` when the
    /// pair has no row.
    async fn transfer(&self, key: &TransferKey) -> ServiceResult<Option<TransferView>>;

    /// Inbox (`Received`) / outbox (`Sent`) transfer list filtered by
    /// the effective status lens (pending past expiry projects to
    /// expired; filter/count/page share one lens).
    async fn transfers(&self, query: TransferListQuery) -> ServiceResult<TransferPage>;

    /// `true` exactly when `role` returns `Some` (owner or manager on
    /// a live bot with complete owner state); errors propagate — this
    /// never degrades to a boolean deny.
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool>;

    /// Deny unless `user_id` is the current owner.
    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()>;

    /// Deny unless `user_id` is the current owner or a manager.
    async fn require_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<()>;

    /// Single-item manager add/restore/remove. Implemented with the
    /// Task-2 manager write path; stores that do not implement it
    /// yet must return an explicit error, never a silent success.
    async fn edit_manager(&self, command: ManagerEdit) -> ServiceResult<ManagerSetChange>;

    /// Trusted full-set sync (spec §5.2). Task-2 write path.
    async fn sync_managers(&self, command: ManagerSync) -> ServiceResult<ManagerSetChange>;

    /// Trusted ownership sync (2026-10-29 design §3): Service-actor
    /// only; the Core maps the target user id to the actor id and
    /// validates the target shape before forwarding.
    async fn sync_ownership(&self, command: OwnershipSync) -> ServiceResult<OwnershipChange>;

    /// Transfer lifecycle command (request/refresh/accept/reject/
    /// cancel, spec §6). Task-3 write path.
    async fn change_transfer(&self, command: TransferCommand) -> ServiceResult<TransferOutcome>;
}
