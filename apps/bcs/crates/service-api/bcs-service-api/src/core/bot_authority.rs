//! `BotAuthorityCoreService` — core contract for strict authority
//! resolution (plan Task 3, spec §5/§12.4).
//!
//! Mirrors the persistence port's three reads with one addition: the
//! `role`/`roles_for` questions are authorization questions, so the Core
//! MUST validate the involved Bot first — existence (`BotNotFound`),
//! initialization (`OwnershipNotInitialized`), and the unique owner
//! (`CorruptAuthority`) — before answering. Corruption never degrades into
//! an ordinary deny, and a whole batch fails closed when any involved Bot
//! is invalid (spec §12.4/§13.5).
//!
//! Application-layer authorization hooks compose on top of this contract
//! and must never reach the repo port directly.

use async_trait::async_trait;
use bcs_domain::{BotAccessRelation, OwnershipState};

use crate::types::error::ServiceResult;

/// Core contract for strict bot authority resolution.
#[async_trait]
pub trait BotAuthorityCoreService: Send + Sync {
    /// Strictly validated ownership snapshot of one Bot (same semantics as
    /// `BotAuthorityRepoPort::ownership`).
    async fn ownership(&self, bot_id: &str) -> ServiceResult<OwnershipState>;

    /// One User's active role on one Bot, after validating the Bot's
    /// authority invariant first. `Ok(None)` is an ordinary deny.
    async fn role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> ServiceResult<Option<BotAccessRelation>>;

    /// Positional batch of [`Self::role`] over full (user_id, bot_id)
    /// pairs; each distinct Bot is validated once, and any invalid Bot
    /// fails the whole batch.
    async fn roles_for(
        &self,
        pairs: &[(String, String)],
    ) -> ServiceResult<Vec<Option<BotAccessRelation>>>;
}