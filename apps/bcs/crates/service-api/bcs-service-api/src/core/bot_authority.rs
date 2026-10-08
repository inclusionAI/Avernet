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
use bcs_domain::{BotAccessRelation, ManagerMutation, ManagerMutationResult, OwnershipState};

use crate::types::{AuditActor, BotManagerList};
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

    /// Idempotently grant/revoke one Human's non-team manager sources on
    /// one Bot, validating the actor, the subject and the Bot INSIDE the
    /// store's single mutation transaction (spec §5.4/§6, plan Task 4):
    /// the actor must still hold a current owner-or-manager role (Gate 0:
    /// current managers may re-grant/revoke other managers), the subject
    /// must be a live human of the same env and must not be the owner,
    /// and the mutation must never touch `team/*` sources (team sources
    /// are written only by the team-sync lane, never through this port).
    ///
    /// The repo owns the atomicity contract: validation, edge writes and
    /// the `bot_manager_changes` audit append commit together or not at
    /// all; only actual state changes are audited. The Core does NOT
    /// pre-read the Bot here — a validation outside the mutation
    /// transaction adds no authority, so composing one in would be racy
    /// duplication; the strict read composition stays on `role`/
    /// `roles_for`/`ownership`.
    async fn mutate_manager(
        &self,
        actor: AuditActor,
        bot_id: &str,
        mutation: ManagerMutation,
    ) -> ServiceResult<ManagerMutationResult>;

    /// The manager list of one initialized Bot (spec §6): the owner in the
    /// separate `owner_user_id` field plus one user-deduplicated page of
    /// explicit managers sorted user_id ASC. Same Bot validation branches
    /// as [`Self::mutate_manager`] apply (missing → `BotNotFound`,
    /// version 0 → `OwnershipNotInitialized`, damaged owner slot →
    /// `CorruptAuthority`).
    async fn list_managers(
        &self,
        bot_id: &str,
        offset: u64,
        limit: u64,
    ) -> ServiceResult<BotManagerList>;
}