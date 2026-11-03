//! Bot-authority Repo port (persistence contract).
//!
//! Persistence-level contract for the strict owner/manager reads and
//! the manager/transfer permission commands. Implemented by
//! `bcs-edge-permission-store` (SQL authority Store over `edge_grants`
//! + `bcs_bots` + the two authority tables) and by `bcs-bot-store`'s
//! memory projection (same `MemoryBotRepo` state). `storage` is the
//! only layer that sees SQL.
//!
//! ## Identity convention
//!
//! **`user_id` parameters in this port are the persisted Human actor
//! ids** (`human_<user_id>`, the bytes stored in
//! `edge_grants.from_id` / transfer actor columns). The
//! [`crate::core::bot_authority::BotAuthorityCoreService`] maps
//! User IDs → actor ids before calling this port, so implementations
//! must compare these values byte-exactly (case-sensitive) and never
//! case-fold. Store/Core instances bind the environment at
//! construction; user parameters cannot override it.
//!
//! ## Strictness contract
//!
//! Every read treats approved owner/manager edges
//! (`grant_kind = 'owner' | 'manager'`, `status = 'approved'`,
//! unified `grant_ref_id = 0`, `rules NULL`) as the only authority
//! facts, scoped to the bound env. A live bot with
//! `ownership_version > 0` whose approved-owner set is empty or has
//! more than one row is a completeness error. Storage failures return
//! `Err` — never a `None`/empty success. Write methods are explicit
//! until their task ships: unimplemented writes must return errors,
//! never default success.

use async_trait::async_trait;

use crate::ServiceResult;
use crate::types::bot_authority::{
    BotAccessRelation, BotOwnershipView, BotRoleQuery, ManagerEdit, ManagerListQuery, ManagerPage,
    ManagerSetChange, ManagerSync, OwnershipChange, OwnershipSync, TransferCommand, TransferKey,
    TransferListQuery, TransferOutcome, TransferPage, TransferView,
};

#[async_trait]
pub trait BotAuthorityRepoPort: Send + Sync {
    /// Role of the Human actor `user_id` on `bot_id` in the bound env.
    async fn role(&self, user_id: &str, bot_id: &str) -> ServiceResult<Option<BotAccessRelation>>;

    /// Batch role read; same length/order as the input, bounded
    /// collection queries (≤
    /// [`crate::types::bot_authority::AUTHORITY_QUERY_CHUNK`] ids per
    /// statement).
    async fn roles_for(
        &self,
        queries: &[BotRoleQuery],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>>;

    /// Owner state of a live, initialized bot; uninitialized or
    /// corrupt state is an error.
    async fn ownership(&self, bot_id: &str) -> ServiceResult<BotOwnershipView>;

    /// Paged manager list with owner summary.
    async fn managers(&self, query: ManagerListQuery) -> ServiceResult<ManagerPage>;

    /// Transfer row for the key, or `None` when absent.
    async fn transfer(&self, key: &TransferKey) -> ServiceResult<Option<TransferView>>;

    /// Inbox/outbox transfer list with the shared effective-status
    /// lens for filter/count/page.
    async fn transfers(&self, query: TransferListQuery) -> ServiceResult<TransferPage>;

    /// Single-item manager add/restore/remove (Task 2).
    async fn edit_manager(&self, command: ManagerEdit) -> ServiceResult<ManagerSetChange>;

    /// Trusted full-set manager sync (Task 2).
    async fn sync_managers(&self, command: ManagerSync) -> ServiceResult<ManagerSetChange>;

    /// Trusted owner replace (ownership sync; Task 2026-10-29).
    async fn sync_ownership(&self, command: OwnershipSync) -> ServiceResult<OwnershipChange>;

    /// Transfer lifecycle command (Task 3).
    async fn change_transfer(&self, command: TransferCommand) -> ServiceResult<TransferOutcome>;
}
