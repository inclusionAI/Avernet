//! `OwnershipTransferService` production implementation (plan Task 14,
//! spec §9-§11): the Human-only ownership query and transfer use cases.
//!
//! Decision boundaries kept here:
//! - the caller is ALWAYS the Gateway-verified Human
//!   ([`require_authenticated_user`]); Bot/App/AccessKey-only principals
//!   are rejected and a body value never names the actor or viewer;
//! - `get_ownership` asks the centralized [`BotAuthorityHook`] first
//!   (the same current-owner/manager read family as the manager list,
//!   spec §11.1), then reads state strictly through
//!   [`BotAuthorityCoreService`];
//! - `create_transfer` validates the request shapes (400), then
//!   delegates the whole ONE-Bot-transaction contract — idempotency
//!   replay FIRST (a same-key replay of an historical receipt does NOT
//!   re-require current ownership, spec §10.1), then the in-transaction
//!   owner/recipient/version validation — to the Core, which enforces
//!   every business predicate inside its own transaction (the manager
//!   that initiates gets the store's 403 `forbidden` from the stronger
//!   in-lock check, never a weaker pre-read). A recipient that is not
//!   a resolvable current-scope Human surfaces 400
//!   `invalid_transfer_recipient` — only the already-authorized
//!   initiator gets that result (spec §11.2). A stale
//!   `expected_owner_version` is the Core's TYPED
//!   [`TransferConflict::VersionSnapshotStale`] branch: 409
//!   `ownership_changed` with NOTHING persisted (the branch is matched
//!   on the TYPE, never on a message string);
//! - `accept`/`reject`/`cancel` map the STORE's committed domain
//!   results per the Task-14 Step-3 table: `Receipt` → the historical
//!   receipt (200); `OwnerChanged` → 409 `ownership_changed` — the
//!   invalidation was already committed inside the store's transaction
//!   and this facade NEVER starts a rollback or a compensating write
//!   (spec §10.2), and same-receiver retries answer with this exact
//!   code (OT12); `Expired` → 409 `ownership_transfer_expired`;
//!   `Invalidated` re-reads the PERSISTED row's `terminal_reason`
//!   through the Core before choosing the code (the committed-outcome
//!   enum alone is not sufficient): `owner_changed` → 409
//!   `ownership_changed`, every other invalidation reason → 409
//!   `ownership_transfer_not_pending`;
//! - storage/decode/commit failures stay 500 `internal_error` with no
//!   SQL leakage; concealment (a non-party 404 indistinguishable from a
//!   missing id) lives in the store's reads and surfaces unchanged.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, BotAuthorityHook, BotOwnership, BotOwnershipTransferCreation,
    BotOwnershipTransferPage, BotOwnershipTransferReceipt, CreateBotOwnershipTransfer,
    DecideBotOwnershipTransfer, ERROR_INVALID_TRANSFER_RECIPIENT, GetBotOwnership,
    GetBotOwnershipTransfer, ListBotOwnershipTransfers, OwnershipTransferService,
    require_authenticated_user,
};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::types::ownership_transfer::{
    CommittedTransferOutcome, CreateOwnershipTransfer, ListOwnershipTransfers,
};
use bcs_service_api::types::{OwnershipState, ServiceError, TerminalReason, TransferAction};

/// Inbox/outbox pagination contract (spec §11.1): positional with
/// limit 1..=100 (default 20 at the transport); out-of-range windows
/// never query the store.
const TRANSFER_PAGE_LIMIT_MAX: u64 = 100;

/// Centralized ownership-transfer application facade.
pub struct OwnershipTransferServiceImpl {
    core: Arc<dyn BotAuthorityCoreService>,
    authority: Arc<dyn BotAuthorityHook>,
}

impl OwnershipTransferServiceImpl {
    pub fn new(
        core: Arc<dyn BotAuthorityCoreService>,
        authority: Arc<dyn BotAuthorityHook>,
    ) -> Self {
        Self { core, authority }
    }

    /// The hook-answered question the ownership read asks first: only a
    /// current owner/manager may read the live owner slot (the same
    /// read family as the manager list, spec §11.1).
    async fn require_current_manager_role(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<(), ApplicationError> {
        let allowed = self
            .authority
            .can_manage(user_id, bot_id)
            .await
            .map_err(map_transfer_error)?;
        if allowed {
            Ok(())
        } else {
            Err(ApplicationError::forbidden(format!(
                "user '{user_id}' holds no current owner/manager role on bot '{bot_id}'"
            )))
        }
    }

    /// Decide one transfer (or observe its committed outcome) and map
    /// the committed domain result per the Step-3 table.
    async fn decide(
        &self,
        command: DecideBotOwnershipTransfer,
        action: TransferAction,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        let user = require_authenticated_user(&command.caller)?;
        Self::validate_identity("transfer_id", &command.transfer_id)?;
        let actor_user_id = user.id.clone();
        let outcome = self
            .core
            .decide_transfer(&actor_user_id, &command.transfer_id, action)
            .await
            .map_err(map_transfer_error)?;
        self.map_committed_outcome(outcome, &actor_user_id, &command.transfer_id)
            .await
    }

    /// The Task-14 Step-3 committed-outcome mapping.
    async fn map_committed_outcome(
        &self,
        outcome: CommittedTransferOutcome,
        actor_user_id: &str,
        transfer_id: &str,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        match outcome {
            CommittedTransferOutcome::Receipt(receipt) => Ok(receipt.into()),
            CommittedTransferOutcome::OwnerChanged => {
                // Already committed as invalidated(owner_changed) inside
                // the store's transaction: NO rollback is ever started
                // here, and same-receiver retries answer with this
                // exact code (spec §10.2/OT12).
                Err(ApplicationError::ownership_changed(format!(
                    "ownership changed before transfer '{transfer_id}' could be confirmed; \
                     the invalidated request stays readable to its parties"
                )))
            }
            CommittedTransferOutcome::Expired => {
                Err(ApplicationError::ownership_transfer_expired(format!(
                    "transfer '{transfer_id}' expired before it could be confirmed"
                )))
            }
            CommittedTransferOutcome::Invalidated => {
                // The outcome enum alone cannot distinguish WHY the row
                // was invalidated: re-read the PERSISTED row's
                // `terminal_reason` through the Core (the caller was
                // just proven a recorded party by the decide lane, so
                // the read stays in-scope for the same Human).
                let receipt = self
                    .core
                    .get_transfer(actor_user_id, transfer_id)
                    .await
                    .map_err(map_transfer_error)?;
                match receipt.terminal_reason {
                    Some(TerminalReason::OwnerChanged) => {
                        Err(ApplicationError::ownership_changed(format!(
                            "ownership changed before transfer '{transfer_id}' could be \
                             confirmed; the invalidated request stays readable to its parties"
                        )))
                    }
                    _ => Err(ApplicationError::ownership_transfer_not_pending(format!(
                        "transfer '{transfer_id}' is no longer pending"
                    ))),
                }
            }
        }
    }

    fn validate_identity(field: &'static str, value: &str) -> Result<(), ApplicationError> {
        if value.trim().is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                format!("ownership transfer field '{field}' must be a non-blank identity"),
            ));
        }
        Ok(())
    }
}

/// Fixed application-error mapping of the ownership lane:
/// - the TYPED [`AuthorityError::TransferConflict`] branches keep their
///   per-branch fixed §11.2 codes through `ApplicationError::authority`
///   (matched on the TYPE, never on a message string);
/// - every other authority branch keeps its shared fixed spec code
///   (403 `forbidden`, 404 `ownership_transfer_not_found`, 409
///   `ownership_not_initialized`, 500 `corrupt_authority` …);
/// - non-authority storage/internal failures stay 500 `internal_error`,
///   never a mapped business code and never SQL leakage.
fn map_transfer_error(error: ServiceError) -> ApplicationError {
    match &error {
        ServiceError::Authority(authority) => ApplicationError::authority(authority.clone()),
        ServiceError::BotNotFound(bot_id) => ApplicationError::not_found(
            "bot_not_found",
            format!("Bot '{bot_id}' was not found"),
        ),
        other => ApplicationError::internal(other.to_string()),
    }
}

/// The create lane's own additions: the recipient branches
/// ([`AuthorityError::InvalidSubject`] from the store's live-same-env
/// Human validation) surface 400 `invalid_transfer_recipient` — only
/// after the caller passed the owner gate (spec §11.2); every other
/// error keeps the shared mapping.
fn map_create_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::Authority(AuthorityError::InvalidSubject(message)) => {
            ApplicationError::invalid(ERROR_INVALID_TRANSFER_RECIPIENT, message)
        }
        other => map_transfer_error(other),
    }
}

#[async_trait]
impl OwnershipTransferService for OwnershipTransferServiceImpl {
    async fn get_ownership(
        &self,
        query: GetBotOwnership,
    ) -> Result<BotOwnership, ApplicationError> {
        let user = require_authenticated_user(&query.caller)?;
        Self::validate_identity("bot_id", &query.bot_id)?;
        self.require_current_manager_role(&user.id, &query.bot_id)
            .await?;
        let state: OwnershipState = self
            .core
            .ownership(&query.bot_id)
            .await
            .map_err(map_transfer_error)?;
        Ok(BotOwnership {
            bot_id: query.bot_id,
            owner_user_id: state.owner_user_id,
            ownership_version: state.ownership_version,
        })
    }

    async fn create_transfer(
        &self,
        command: CreateBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferCreation, ApplicationError> {
        let user = require_authenticated_user(&command.caller)?;
        Self::validate_identity("bot_id", &command.bot_id)?;
        Self::validate_identity("to_user_id", &command.to_user_id)?;
        Self::validate_identity("client_request_id", &command.client_request_id)?;
        if command.expected_owner_version == 0 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "expected_owner_version must be a positive integer",
            ));
        }
        if command.to_user_id == user.id {
            // 转交给自己 is a plain parameter error (spec §11.2), caught
            // here before any store call.
            return Err(ApplicationError::invalid(
                "invalid_request",
                "the owner cannot transfer a Bot to themselves",
            ));
        }
        // NO owner pre-gate exists here on purpose (spec §10.1): the
        // store's ONE transaction answers the idempotency branch FIRST
        // (a same-key replay of a committed receipt never re-requires
        // current ownership), and re-proves actor-is-current-owner,
        // recipient liveness and the exact version INSIDE the
        // transaction — the manager that initiates a FRESH transfer gets
        // the store's typed `Forbidden` (403) from the stronger
        // in-lock check.
        let result = self
            .core
            .create_transfer(CreateOwnershipTransfer {
                actor_user_id: user.id.clone(),
                bot_id: command.bot_id,
                to_user_id: command.to_user_id,
                expected_owner_version: command.expected_owner_version,
                client_request_id: command.client_request_id,
            })
            .await
            .map_err(map_create_error)?;
        Ok(BotOwnershipTransferCreation {
            receipt: result.receipt.into(),
            created: result.created,
        })
    }

    async fn list_transfers(
        &self,
        query: ListBotOwnershipTransfers,
    ) -> Result<BotOwnershipTransferPage, ApplicationError> {
        let user = require_authenticated_user(&query.caller)?;
        if query.limit == 0 || query.limit > TRANSFER_PAGE_LIMIT_MAX {
            // API contract (spec §11.1): limit 1..=100, default 20 at the
            // transport; an out-of-range window never queries the store.
            return Err(ApplicationError::invalid(
                "invalid_request",
                "transfer list limit must be within 1..=100",
            ));
        }
        let page = self
            .core
            .list_transfers(ListOwnershipTransfers {
                viewer_user_id: user.id.clone(),
                direction: query.direction,
                status: query.status,
                offset: query.offset,
                limit: query.limit,
            })
            .await
            .map_err(map_transfer_error)?;
        Ok(BotOwnershipTransferPage {
            items: page.items.into_iter().map(Into::into).collect(),
            total: page.total,
            offset: query.offset,
            limit: query.limit,
        })
    }

    async fn get_transfer(
        &self,
        query: GetBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        let user = require_authenticated_user(&query.caller)?;
        Self::validate_identity("transfer_id", &query.transfer_id)?;
        let receipt = self
            .core
            .get_transfer(&user.id, &query.transfer_id)
            .await
            .map_err(map_transfer_error)?;
        Ok(receipt.into())
    }

    async fn accept_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        self.decide(command, TransferAction::Accept).await
    }

    async fn reject_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        self.decide(command, TransferAction::Reject).await
    }

    async fn cancel_transfer(
        &self,
        command: DecideBotOwnershipTransfer,
    ) -> Result<BotOwnershipTransferReceipt, ApplicationError> {
        self.decide(command, TransferAction::Cancel).await
    }
}
