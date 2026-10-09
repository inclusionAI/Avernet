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
use bcs_domain::{
    BotAccessRelation, ManagerMutation, ManagerMutationResult, OwnershipState, TransferAction,
};

use crate::types::error::ServiceResult;
use crate::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult, ListOwnershipTransfers,
    OwnershipTransfer, OwnershipTransferPage,
};
use crate::types::team_manager_sync::{TeamManagerSync, TeamSyncReceipt};
use crate::types::{AuditActor, BotManagerList};

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
    /// one Bot, with the authoritative validation enforced INSIDE the
    /// store's single mutation transaction as conditions of the changing
    /// statements themselves (spec §5.4/§6, plan Task 4): the actor must
    /// still hold a current owner-or-manager role (Gate 0: current
    /// managers may re-grant/revoke other managers), the subject must be
    /// a live human of the same env and must not be the owner, the Bot
    /// must be live/initialized with its unique owner slot, and the
    /// mutation must never touch `team/*` sources (team sources are
    /// written only by the team-sync lane, never through this port).
    /// Only `Human` actors may mutate managers — `Service`/`System` are
    /// rejected fail-closed before any from_id matching.
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

    /// Atomically synchronize one URL team's manager source (spec §5.4/§6,
    /// plan Task 7) with durable idempotency. The REPO owns the whole
    /// one-transaction contract (credential scope re-validation,
    /// normalization, Human validation, chunked reconcile, service-actor
    /// audit, `bot_team_manager_sources` binding and the persisted
    /// receipt — see `BotAuthorityRepoPort::sync_team`), so the Core
    /// forwards the command verbatim and composes nothing on top, exactly
    /// like [`Self::mutate_manager`]: a validation outside the mutation
    /// transaction adds no authority, and the application layer reaches
    /// this lane through the Core, never the repo port directly.
    ///
    /// Same-key replays return the original committed receipt without
    /// re-execution; payloads that differ for the same key are a
    /// `Conflict`. The verified-service credential is the only accepted
    /// actor shape — the Core never substitutes a raw client actor.
    async fn sync_team(&self, command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt>;

    /// Initiate one ownership transfer (plan Task 8, spec §10.1). The REPO
    /// owns the whole one-Bot-transaction contract — idempotency replay,
    /// owner/recipient/version validation inside the transaction, slot
    /// hygiene and the pending insert — so the Core forwards the command
    /// VERBATIM and composes nothing on top, exactly like
    /// [`Self::mutate_manager`]/[`Self::sync_team`]: a validation outside
    /// the mutation transaction adds no authority, and the application
    /// layer reaches this lane through the Core, never the repo port
    /// directly. Same-key same-payload replays return the original receipt
    /// with `created = false`; different payloads are a `Conflict`.
    async fn create_transfer(
        &self,
        command: CreateOwnershipTransfer,
    ) -> ServiceResult<CreateTransferResult>;

    /// Decide (accept / reject / cancel) one transfer, or observe its
    /// already-committed outcome (plan Task 8, spec §10.2/§10.3). The REPO
    /// owns the whole one-Bot-transaction contract — party visibility,
    /// terminal-row retry re-derivation from `terminal_reason`, the
    /// database-clock expiry materialization, the committed
    /// `invalidated(owner_changed)` domain result, and the all-or-nothing
    /// accept (owner edge swap + `ownership_transfer/<id>` manager source
    /// for the previous owner + recipient non-team source revoke + version
    /// CAS + the accepted receipt). The Core forwards verbatim and composes
    /// nothing: [`CommittedTransferOutcome::OwnerChanged`] /
    /// [`CommittedTransferOutcome::Expired`] /
    /// [`CommittedTransferOutcome::Invalidated`] are COMMITTED domain
    /// results the application layer maps (409 semantics) — the Core must
    /// never wrap them into errors or roll the invalidations back.
    async fn decide_transfer(
        &self,
        actor_user_id: &str,
        transfer_id: &str,
        action: TransferAction,
    ) -> ServiceResult<CommittedTransferOutcome>;

    /// Read one transfer receipt for a recorded party (plan Task 8). The
    /// Core forwards verbatim: the effective-expiry projection and the
    /// party-visibility concealment contract ([§11.1/OT18]) live in the
    /// repo reads — there is no authorization composition to add, and a
    /// pre-read would only duplicate read-side truth.
    async fn get_transfer(
        &self,
        viewer_user_id: &str,
        transfer_id: &str,
    ) -> ServiceResult<OwnershipTransfer>;

    /// List the viewer's transfer inbox/outbox page (plan Task 8). The
    /// Core forwards verbatim (same read-only contract as
    /// [`Self::get_transfer`]; see the port docs for the snapshot/
    /// effective-status/anti-enumeration rules).
    async fn list_transfers(
        &self,
        query: ListOwnershipTransfers,
    ) -> ServiceResult<OwnershipTransferPage>;
}