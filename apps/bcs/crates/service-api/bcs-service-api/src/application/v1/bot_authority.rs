//! `BotAuthorityService` — application facade for the unified
//! `/api/v1/collaboration/` authority use cases (spec §5/§6).
//!
//! Transport-independent: delivery adapters translate requests into
//! these commands and never construct actors or touch the authority
//! Core/Repo directly. The implementation (composition root selected;
//! Task 5 of the implementation plan) verifies the Human/App identity
//! for every call, builds [`crate::types::bot_authority::
//! AuthorityMutation`] values for write commands from the *verified*
//! identity, and delegates to the `BotAuthorityCoreService`.
//!
//! Public error codes (stable spelling) for this surface. Adapter
//! mapping table (spec §7):
//!
//! | code | Application variant | HTTP |
//! | --- | --- | --- |
//! | `bot_ownership_not_initialized` | `Conflict` | 409 |
//! | `bot_owner_integrity` | `Internal` | 500 |
//! | `owner_not_manager` | `Conflict` | 409 |
//! | `transfer_*` ( [`AuthorityConflictCode`] spelling) | `Conflict` | 409 |
//! | auth/scope denials | `Forbidden`/`ForbiddenCode` | 403 |
//!
//! Internal mutation ids and SQL/DB errors never appear as codes or
//! messages here.

use async_trait::async_trait;

use crate::application::v1::error::ApplicationError;

// The application facade carries its value vocabulary so
// delivery adapters import ONLY the application boundary
// (`bcs_service_api::application::...`).
pub use crate::types::bot_authority::{
    AuthorityConflictCode, AuthorityMutation, AuthorityActor,
    AuthorityActorKind, BotAccessRelation, BotOwnershipView, BotRoleQuery,
    ManagerEdit, ManagerEditAction, ManagerListQuery, ManagerPage,
    ManagerSetChange, ManagerSync, TransferAction, TransferChange,
    TransferCommand, TransferDirection, TransferKey, TransferListQuery,
    TransferOutcome, TransferPage, TransferStatus, TransferView,
    DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, human_user_id_of_actor,
};
use crate::application::v1::identity::AuthenticatedCaller;

/// `bot_ownership_not_initialized` — version 0 bot touched by a
/// Human/authority operation (spec §3.2.2; HTTP 409).
pub const ERROR_BOT_AUTHORITY_NOT_INITIALIZED: &str = "bot_ownership_not_initialized";

/// `bot_owner_integrity` — live bot with missing or duplicated
/// approved owner (spec §3.2.2; HTTP 500).
pub const ERROR_BOT_OWNER_INTEGRITY: &str = "bot_owner_integrity";

/// `bot_transfer_not_found` — a transfer GET addressed a
/// `(bot_id, target_owner_actor_id)` pair that has no
/// transfer row (spec §6.1: located by Bot ID + target,
/// never a `transfer_id`; HTTP 404).
pub const ERROR_BOT_TRANSFER_NOT_FOUND: &str = "bot_transfer_not_found";

/// `owner_not_manager` — owner addressed through the manager
/// item endpoints (spec §5.1; HTTP 409).
pub const ERROR_OWNER_NOT_MANAGER: &str = "owner_not_manager";

/// `bot_authority_human_unknown` — a manager edit/sync names a
/// user id that is not a valid, existing Human actor id (spec
/// §5.1 "新增目标必须是有效 Human"; spec §5.2: an unknown Human
/// fails the whole request; HTTP 400 until the adapter maps it).
pub const ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN: &str = "bot_authority_human_unknown";

/// `bot_manager_input_limit` — a synced set exceeds the
/// 1,000-deduped-user input cap (spec §5.2; HTTP 400).
pub const ERROR_BOT_MANAGER_INPUT_LIMIT: &str =
    "bot_manager_input_limit";

/// `bot_manager_stock_limit` — the current manager set exceeds the
/// frozen 10,000-stock bound of the capacity record
/// (feasibility doc §5.2); the whole request fails and is
/// never truncated (HTTP 409).
pub const ERROR_BOT_MANAGER_STOCK_LIMIT: &str =
    "bot_manager_stock_limit";

/// `bot_authority_operator_forbidden` — the verified mutation
/// actor may not run this manager command: a Human without a
/// current owner/manager edge on edit, or an actor kind barred
/// by the Human/App-only split (edit is Human-only, sync is
/// trusted-App only; spec §5.1/§5.2; HTTP 403).
pub const ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN: &str =
    "bot_authority_operator_forbidden";

/// `bot_authority_tx_outcome_uncertain` — the DB transaction hit
/// `DbError::Timeout` (Task-0 方案 A). The outcome is rolled back
/// by the plugin contract, but callers MUST treat the command as
/// terminal and never auto-retry (spec §5.2 consumer contract;
/// HTTP 500).
pub const ERROR_BOT_TX_OUTCOME_UNCERTAIN: &str =
    "bot_authority_tx_outcome_uncertain";

/// `retirement_cleanup_pending` — the retirement transaction
/// committed (the tombstone and permission cleanup are
/// DURABLE and are never rolled back), but the post-commit
/// external cleanup failed. The call reports neither full
/// success nor a rollback; the caller retries the cleanup —
/// it re-derives the identity from the retained tombstone/
/// metadata/binding and is idempotent, never resurrecting
/// the Bot nor writing a second retire audit (spec §7.1).
pub const ERROR_RETIREMENT_CLEANUP_PENDING: &str =
    "retirement_cleanup_pending";

/// `bot_list_changed` — the mine page backfill discovered a
/// page target deleted (or its role drifted) and ONE whole
/// fresh retry could not reproduce the same page composition.
/// The application NEVER fills a shifted page as an exact
/// result: the caller re-pages from a fresh request (spec
/// §8.0 point 4; HTTP 409).
pub const ERROR_BOT_LIST_CHANGED: &str = "bot_list_changed";

/// Authority application facade. Method names mirror the Core; every
/// method additionally receives the authenticated caller so the best
/// identity (Human vs trusted App) is selected per action.
#[async_trait]
pub trait BotAuthorityService: Send + Sync {
    async fn role(
        &self,
        caller: &AuthenticatedCaller,
        user_id: &str,
        bot_id: &str,
    ) -> Result<Option<BotAccessRelation>, ApplicationError>;

    async fn roles_for(
        &self,
        caller: &AuthenticatedCaller,
        queries: &[BotRoleQuery],
    ) -> Result<Vec<Option<BotAccessRelation>>, ApplicationError>;

    async fn ownership(
        &self,
        caller: &AuthenticatedCaller,
        bot_id: &str,
    ) -> Result<BotOwnershipView, ApplicationError>;

    async fn managers(
        &self,
        caller: &AuthenticatedCaller,
        query: ManagerListQuery,
    ) -> Result<ManagerPage, ApplicationError>;

    async fn transfer(
        &self,
        caller: &AuthenticatedCaller,
        key: &TransferKey,
    ) -> Result<Option<TransferView>, ApplicationError>;

    async fn transfers(
        &self,
        caller: &AuthenticatedCaller,
        query: TransferListQuery,
    ) -> Result<TransferPage, ApplicationError>;

    async fn edit_manager(
        &self,
        caller: &AuthenticatedCaller,
        command: ManagerEdit,
    ) -> Result<ManagerSetChange, ApplicationError>;

    async fn sync_managers(
        &self,
        caller: &AuthenticatedCaller,
        command: ManagerSync,
    ) -> Result<ManagerSetChange, ApplicationError>;

    async fn change_transfer(
        &self,
        caller: &AuthenticatedCaller,
        command: TransferCommand,
    ) -> Result<TransferOutcome, ApplicationError>;
}

#[cfg(test)]
mod tests {
    //! Pin tests for the manager-command error codes
    //! (T1-c1): Task 5 maps these spellings to HTTP; they
    //! must not drift.

    use super::*;

    #[test]
    fn manager_command_error_codes_are_pinned() {
        assert_eq!(ERROR_OWNER_NOT_MANAGER, "owner_not_manager");
        assert_eq!(ERROR_BOT_AUTHORITY_NOT_INITIALIZED, "bot_ownership_not_initialized");
        assert_eq!(ERROR_BOT_OWNER_INTEGRITY, "bot_owner_integrity");
        assert_eq!(ERROR_BOT_TRANSFER_NOT_FOUND, "bot_transfer_not_found");
        assert_eq!(
            ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN,
            "bot_authority_human_unknown"
        );
        assert_eq!(ERROR_BOT_MANAGER_INPUT_LIMIT, "bot_manager_input_limit");
        assert_eq!(ERROR_BOT_MANAGER_STOCK_LIMIT, "bot_manager_stock_limit");
        assert_eq!(
            ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN,
            "bot_authority_operator_forbidden"
        );
        assert_eq!(
            ERROR_BOT_TX_OUTCOME_UNCERTAIN,
            "bot_authority_tx_outcome_uncertain"
        );
        assert_eq!(
            ERROR_RETIREMENT_CLEANUP_PENDING,
            "retirement_cleanup_pending"
        );
        assert_eq!(ERROR_BOT_LIST_CHANGED, "bot_list_changed");
    }
}
