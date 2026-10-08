//! `BotAuthorityHook` — centralized application authorization hook for bot
//! manage permissions (plan Task 3, spec §6/§13.5).
//!
//! One hook answers "may this Human manage this Bot" and "is this Human
//! the Bot's owner" for every application use case (ship-status changes,
//! manager mutators, delivery batches, ownership transfer …). Delivery
//! adapters and use-case services call the hook; the hook resolves through
//! [`crate::core::BotAuthorityCoreService`] and NEVER the repo port — the
//! application layer depends on Core only. `user_id` is always the
//! authenticated caller's trusted User ID, never a request-body value.
//!
//! Business branches surfaced through `ServiceResult`:
//! - `Authority(OwnershipNotInitialized)` / `Authority(CorruptAuthority)` /
//!   `BotNotFound` propagate from the Core's validation;
//! - `Authority(Forbidden)` when `require_owner` meets a non-owner.

use async_trait::async_trait;

use crate::types::error::ServiceResult;

/// Centralized authority hook for bot manage-permission questions.
#[async_trait]
pub trait BotAuthorityHook: Send + Sync {
    /// May `user_id` manage `bot_id` (owner or any manager)?
    ///
    /// True iff the Core resolves an active role (`Owner` first, any
    /// approved `Manager` source otherwise). Initialized-but-corrupt or
    /// uninitialized authority surfaces as `Err`, never as a plain deny.
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool>;

    /// Require that `user_id` is the current owner of `bot_id`.
    ///
    /// `Ok(())` for the owner; `Err(Forbidden)` for any non-owner;
    /// validation errors (missing bot, uninitialized, corrupt) propagate.
    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()>;
}