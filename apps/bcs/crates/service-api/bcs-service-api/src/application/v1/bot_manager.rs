//! `BotManagerService` — the Human-only manager list/mutate API (plan
//! Task 13, spec §6).
//!
//! Transport-independent use cases behind
//! `GET/PUT/DELETE /openapi/v1/collaboration/bots/{bot_id}/managers`:
//! - the caller is ALWAYS the Gateway-verified Human inside
//!   [`AuthenticatedCaller`] (never a request-body value);
//! - `list_managers` projects the current owner into the separate
//!   read-only `owner_user_id` field (the owner never mixes into the
//!   manager page) plus one user-deduplicated, `user_id ASC`-sorted page
//!   of explicit managers;
//! - `grant_manager`/`revoke_manager` are idempotent DIRECT-lane
//!   mutations (`AuditActor::Human`): a grant only ever writes the
//!   `direct` source, a revoke only ever revokes `direct` and
//!   `ownership_transfer` sources and reports the team sources it
//!   deliberately left in place — `revoked=true` is NOT total loss of
//!   management rights (spec §6);
//! - the owner is not touchable through this lane: an owner-target
//!   grant/revoke surfaces the fixed 409 `owner_role_requires_transfer`
//!   conflict and must be retried through the ownership-transfer flow.
//!
//! This trait is a NEW manager-lane vocabulary; it deliberately does not
//! collide with the legacy registration/lifecycle
//! `application::BotManagementService` contract, which keeps its own
//! onboarding semantics.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller};

/// The fixed role label of every manager-page item (spec §6: the owner is
/// the separate `owner_user_id` field, so every listed item is a manager).
pub const BOT_MANAGER_ROLE_LABEL: &str = "manager";

/// Read one page of the manager list (spec §6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListBotManagers {
    /// The Gateway-verified caller — the trusted Human reading the list.
    pub caller: AuthenticatedCaller,
    /// The Bot whose manager list is read.
    pub bot_id: String,
    /// Positional page offset (users).
    pub offset: u64,
    /// Page size; the application layer clamps it into 1..=100.
    pub limit: u64,
}

/// Idempotently grant one Human the DIRECT manager source (spec §6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GrantBotManager {
    /// The Gateway-verified Human actor performing the grant (owner or a
    /// current manager per Gate 0; validated inside the store's mutation
    /// transaction, never from a body value).
    pub caller: AuthenticatedCaller,
    /// The Bot receiving the manager edge.
    pub bot_id: String,
    /// The trusted User ID of the grant subject (must be a live Human of
    /// the same env, and never the current owner).
    pub user_id: String,
}

/// Idempotently revoke one Human's non-team manager sources (spec §6):
/// `direct` and `ownership_transfer` sources only; `team/*` sources are
/// the team-sync lane's business and stay untouched.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RevokeBotManager {
    /// The Gateway-verified Human actor performing the revoke.
    pub caller: AuthenticatedCaller,
    /// The Bot losing the non-team manager edges.
    pub bot_id: String,
    /// The trusted User ID of the revoke subject.
    pub user_id: String,
}

/// One deduplicated manager-list item: the subject's bare user id, its
/// Human actor id (`human_<user_id>`), and the fixed manager role label.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerEntry {
    /// The trusted User ID of the manager.
    pub user_id: String,
    /// The Human actor id carrying the user_id (D11 convention).
    pub actor_id: String,
    /// Always [`BOT_MANAGER_ROLE_LABEL`] — an item with another role never
    /// enters this page.
    pub role: String,
}

/// The application-level projection of [`crate::types::BotManagerList`]:
/// the owner in its own read-only field plus the positional manager page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerPage {
    pub bot_id: String,
    /// The single effective owner — read-only, never mixed into `items`.
    pub owner_user_id: String,
    pub items: Vec<BotManagerEntry>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

/// Result of one idempotent direct grant (spec §6): `role` is always
/// `manager`; `changed=false` means the stored edge already matched.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerGrantResult {
    pub bot_id: String,
    pub user_id: String,
    pub role: String,
    pub changed: bool,
}

/// Result of one idempotent non-team revoke (spec §6): `revoked` states
/// whether THIS request changed the direct/ownership-transfer state, and
/// `remaining_team_sources` lists the `team/*` sources this endpoint
/// deliberately does not touch — a non-empty list means the subject is
/// still a manager via the platform's team synchronization.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotManagerRevokeResult {
    pub bot_id: String,
    pub user_id: String,
    pub revoked: bool,
    pub remaining_team_sources: Vec<String>,
}

/// Human-only manager list/mutate application use cases (spec §6).
#[async_trait]
pub trait BotManagerService: Send + Sync {
    /// Read the manager page of one Bot (caller must hold a current
    /// owner-or-manager role).
    async fn list_managers(
        &self,
        command: ListBotManagers,
    ) -> Result<BotManagerPage, ApplicationError>;

    /// Idempotent direct grant of the manager edge.
    async fn grant_manager(
        &self,
        command: GrantBotManager,
    ) -> Result<BotManagerGrantResult, ApplicationError>;

    /// Idempotent non-team revoke of the manager edge. The caller keeps
    /// managerial authority through the whole request: a fully revoked
    /// former manager must not exploit idempotency to change state.
    async fn revoke_manager(
        &self,
        command: RevokeBotManager,
    ) -> Result<BotManagerRevokeResult, ApplicationError>;
}