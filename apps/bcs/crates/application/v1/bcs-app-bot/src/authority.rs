//! `BotAuthorityServiceImpl` — the application facade for the
//! unified `/api/v1/collaboration/` authority use cases
//! (bot-authority plan Task 5, spec §5/§6).
//!
//! Per-call identity selection (spec §6.1):
//! - Human admission keeps the existing invite-code gate: a
//!   caller with a Human identity runs it (when enabled) and
//!   is never escalated to the trusted-App scope after a
//!   Human rejection (dual rejection is final).
//! - `sync_managers` is trusted-App only (`managers:sync`).
//! - Single transfer GET, accept and reject are dual
//!   admission; every other route keeps its Human rule.
//! - Provider/Bot/AccessKey-only credentials (a caller with
//!   neither a Human nor a trusted App identity) never gain
//!   authority operations: those stay retirement-only.
//!
//! The facade builds [`AuthorityMutation`] values from the
//! verified identity (a service actor keeps its real service
//! id), selects the Human/App subject per the spec matrix,
//! and delegates to the `BotAuthorityCoreService`. It never
//! serializes mutation ids or SQL internals: core errors map
//! to application errors carrying the pinned public codes per
//! the spec §7 status mapping (403 identity, 404 missing
//! target, 409 state/version/uninitialized, 400 input, 500
//! database/integrity).

use std::sync::Arc;

use async_trait::async_trait;
use ulid::Ulid;

use bcs_service_api::application::v1::bot_authority::{
    ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN, ERROR_BOT_AUTHORITY_NOT_INITIALIZED,
    ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN, ERROR_BOT_MANAGER_INPUT_LIMIT,
    ERROR_BOT_MANAGER_STOCK_LIMIT, ERROR_BOT_TRANSFER_NOT_FOUND,
    ERROR_OWNER_NOT_MANAGER,
};
use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::application::v1::identity::AuthenticatedCaller;
use bcs_service_api::application::v1::invite_code::InviteCodeService;
use bcs_service_api::application::v1::bot_authority::BotAuthorityService;
use bcs_service_api::core::bot_authority::BotAuthorityCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::types::bot_authority::{
    check_page_bounds, human_actor_id, AuthorityActor, AuthorityMutation,
    BotAccessRelation, BotOwnershipView, BotRoleQuery, ManagerEdit,
    ManagerListQuery, ManagerPage, ManagerSetChange, ManagerSync,
    OwnershipChange, OwnershipSync, TransferAction, TransferCommand,
    TransferKey, TransferListQuery, TransferOutcome, TransferPage, TransferView,
};

use crate::authority_policy::{AuthorityAction, AuthorityServicePolicy};

/// Application facade over one bound authority Core.
pub struct BotAuthorityServiceImpl {
    hook: Arc<dyn BotAuthorityPort>,
    core: Arc<dyn BotAuthorityCoreService>,
    invite_code: Arc<dyn InviteCodeService>,
    /// Whether the Human invite-code gate is enabled (the
    /// same flag the shared invite gate middleware uses).
    invite_gate_enabled: bool,
    policy: AuthorityServicePolicy,
    /// Env this instance serves (diagnostics only).
    env: String,
}

impl BotAuthorityServiceImpl {
    pub fn new(
        hook: Arc<dyn BotAuthorityPort>,
        core: Arc<dyn BotAuthorityCoreService>,
        invite_code: Arc<dyn InviteCodeService>,
        invite_gate_enabled: bool,
        policy: AuthorityServicePolicy,
        env: impl Into<String>,
    ) -> Self {
        Self {
            hook,
            core,
            invite_code,
            invite_gate_enabled,
            policy,
            env: env.into(),
        }
    }

    /// The bound env (diagnostics only).
    pub fn env(&self) -> &str {
        &self.env
    }

    // -- identity selection ------------------------------------------------

    fn user_id_of(caller: &AuthenticatedCaller) -> Result<String, ApplicationError> {
        caller
            .user
            .as_ref()
            .map(|user| user.id.clone())
            .ok_or_else(|| operator_forbidden(
                "this command requires a verified Human caller",
            ))
    }

    /// The Human identity with the existing invite-code gate
    /// applied (spec §6.1). A trusted service is never treated
    /// as an un-invite-coded Human: the gate runs only for a
    /// Human identity, and the decision is final.
    async fn authorized_human(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<String, ApplicationError> {
        let user_id = Self::user_id_of(caller)?;
        if self.invite_gate_enabled {
            self.invite_code
                .ensure_invite_code_access(caller)
                .await?;
        }
        Ok(user_id)
    }

    fn mutation_for_human(user_id: &str) -> AuthorityMutation {
        AuthorityMutation {
            mutation_id: Ulid::new().to_string(),
            actor: AuthorityActor::Human {
                user_id: user_id.to_string(),
            },
        }
    }

    fn mutation_of(actor: &AuthorityActor) -> AuthorityMutation {
        AuthorityMutation {
            mutation_id: Ulid::new().to_string(),
            actor: actor.clone(),
        }
    }

    async fn require_can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<(), ApplicationError> {
        match self.hook.can_manage(user_id, bot_id).await {
            Ok(true) => Ok(()),
            Ok(false) => Err(operator_forbidden(&format!(
                "the caller is not the owner or a manager of bot '{bot_id}'"
            ))),
            Err(error) => Err(map_core_error(error)),
        }
    }

    /// The single transfer GET handler: dual admission. A Human
    /// must be one of the row's two current parties; a trusted
    /// App needs `ownership:read` scoped to the target.
    async fn transfer_as_dual_identity(
        &self,
        caller: &AuthenticatedCaller,
        key: &TransferKey,
    ) -> Result<Option<TransferView>, ApplicationError> {
        if caller.user.is_some() {
            self.authorized_human(caller).await?;
        } else {
            self.policy
                .authorize(caller, AuthorityAction::OwnershipRead, &key.bot_id)
                .map(|_| ())?;
        }
        match self.core.transfer(key).await.map_err(map_core_error)? {
            None => Err(ApplicationError::not_found(
                ERROR_BOT_TRANSFER_NOT_FOUND,
                format!(
                    "no ownership transfer for bot '{}' target '{}'",
                    key.bot_id, key.target_owner_actor_id
                ),
            )),
            Some(view) => {
                if let Some(user) = caller.user.as_ref() {
                    // Human read: only the row's two current
                    // parties (spec §6.1).
                    let actor = human_actor_id(&user.id);
                    if actor != view.from_owner_actor_id
                        && actor != view.target_owner_actor_id
                    {
                        return Err(operator_forbidden(&format!(
                            "transfer reads are limited to the row's two \
                             parties; '{actor}' is neither"
                        )));
                    }
                }
                Ok(Some(view))
            }
        }
    }

    /// One transfer decision (`accept` / `reject`): dual
    /// admission — Human first (final, no App fallback),
    /// trusted App second (scoped to the target). The
    /// Human branch keeps the invite-code gate exactly
    /// like every other Human path (spec §6.1).
    async fn decide_transfer_as_dual_identity(
        &self,
        caller: &AuthenticatedCaller,
        key: &TransferKey,
        action: TransferAction,
    ) -> Result<TransferCommand, ApplicationError> {
        let mutation = match caller.user.as_ref() {
            // Human identity keeps the Human rule (incl. the
            // invite gate) even when the same request
            // carries an App identity; the Store
            // re-verifies the target match inside the command.
            Some(user) => {
                self.authorized_human(caller).await?;
                Self::mutation_for_human(&user.id)
            }
            None => {
                let app_action = match action {
                    TransferAction::Accept => AuthorityAction::OwnershipAccept,
                    TransferAction::Reject => AuthorityAction::OwnershipReject,
                    _ => {
                        return Err(operator_forbidden(
                            "transfer initiation is a Human (current-owner) \
                             command",
                        ));
                    }
                };
                let actor = self.policy.authorize(caller, app_action, &key.bot_id)?;
                Self::mutation_of(&actor)
            }
        };
        Ok(TransferCommand {
            key: key.clone(),
            action,
            mutation,
        })
    }

    fn paged_query_bounds(offset: u64, limit: u64) -> Result<(), ApplicationError> {
        check_page_bounds(offset, limit).map_err(|error| {
            ApplicationError::invalid("invalid_request", error.to_string())
        })
    }
}

#[async_trait]
impl BotAuthorityService for BotAuthorityServiceImpl {
    async fn role(
        &self,
        caller: &AuthenticatedCaller,
        user_id: &str,
        bot_id: &str,
    ) -> Result<Option<BotAccessRelation>, ApplicationError> {
        Self::user_id_of(caller)?;
        self.core.role(user_id, bot_id).await.map_err(map_core_error)
    }

    async fn roles_for(
        &self,
        caller: &AuthenticatedCaller,
        queries: &[BotRoleQuery],
    ) -> Result<Vec<Option<BotAccessRelation>>, ApplicationError> {
        Self::user_id_of(caller)?;
        self.core
            .roles_for(queries)
            .await
            .map_err(map_core_error)
    }

    async fn ownership(
        &self,
        caller: &AuthenticatedCaller,
        bot_id: &str,
    ) -> Result<BotOwnershipView, ApplicationError> {
        // owner/manager read (spec §6.1); the App scope has no
        // owner-summary read action.
        let user_id = self.authorized_human(caller).await?;
        self.require_can_manage(&user_id, bot_id).await?;
        self.core.ownership(bot_id).await.map_err(map_core_error)
    }

    async fn managers(
        &self,
        caller: &AuthenticatedCaller,
        query: ManagerListQuery,
    ) -> Result<ManagerPage, ApplicationError> {
        Self::paged_query_bounds(query.offset, query.limit)?;
        let user_id = self.authorized_human(caller).await?;
        self.require_can_manage(&user_id, &query.bot_id).await?;
        self.core.managers(query).await.map_err(map_core_error)
    }

    async fn transfer(
        &self,
        caller: &AuthenticatedCaller,
        key: &TransferKey,
    ) -> Result<Option<TransferView>, ApplicationError> {
        self.transfer_as_dual_identity(caller, key).await
    }

    async fn transfers(
        &self,
        caller: &AuthenticatedCaller,
        query: TransferListQuery,
    ) -> Result<TransferPage, ApplicationError> {
        Self::paged_query_bounds(query.offset, query.limit)?;
        // The inbox/outbox Human rule: the list owner is the
        // authenticated Human, never a supplied body field.
        let user_id = self.authorized_human(caller).await?;
        let query = TransferListQuery {
            user_id: human_actor_id(&user_id),
            ..query
        };
        self.core.transfers(query).await.map_err(map_core_error)
    }

    async fn edit_manager(
        &self,
        caller: &AuthenticatedCaller,
        command: ManagerEdit,
    ) -> Result<ManagerSetChange, ApplicationError> {
        // Single-item manager PUT/DELETE: Human-rule (spec §5.1).
        // The Store re-validates the operator's current edge
        // inside the write transaction.
        let user_id = self.authorized_human(caller).await?;
        self.core
            .edit_manager(ManagerEdit {
                mutation: Self::mutation_for_human(&user_id),
                ..command
            })
            .await
            .map_err(map_core_error)
    }

    async fn sync_managers(
        &self,
        caller: &AuthenticatedCaller,
        command: ManagerSync,
    ) -> Result<ManagerSetChange, ApplicationError> {
        // sync is trusted-App only (spec §5.2/§6.1): a Human-only
        // credential is rejected, and a dual Human+App credential is
        // processed as the App identity — never a Human fallback or
        // bypass.
        let actor =
            self.policy
                .authorize(caller, AuthorityAction::ManagersSync, &command.bot_id)?;
        self.core
            .sync_managers(ManagerSync {
                mutation: Self::mutation_of(&actor),
                ..command
            })
            .await
            .map_err(map_core_error)
    }

    async fn sync_ownership(
        &self,
        caller: &AuthenticatedCaller,
        command: OwnershipSync,
    ) -> Result<OwnershipChange, ApplicationError> {
        // Ownership sync is trusted-App only (design §2): a
        // Human-only credential is rejected, and a dual Human+App
        // credential is processed as the App identity — never a
        // Human fallback or bypass.
        let actor = self.policy.authorize(
            caller,
            AuthorityAction::OwnershipSync,
            &command.bot_id,
        )?;
        self.core
            .sync_ownership(OwnershipSync {
                mutation: Self::mutation_of(&actor),
                ..command
            })
            .await
            .map_err(map_core_error)
    }

    async fn change_transfer(
        &self,
        caller: &AuthenticatedCaller,
        command: TransferCommand,
    ) -> Result<TransferOutcome, ApplicationError> {
        let command = match command.action {
            TransferAction::RequestOrRefresh | TransferAction::Cancel => {
                // Initiation (current owner) and cancellation
                // (initiator) are Human commands (spec §6.1/§6.2).
                let user_id = self.authorized_human(caller).await?;
                TransferCommand {
                    mutation: Self::mutation_for_human(&user_id),
                    ..command
                }
            }
            TransferAction::Accept | TransferAction::Reject => {
                self.decide_transfer_as_dual_identity(
                    caller,
                    &command.key,
                    command.action,
                )
                .await?
            }
        };
        // Committed business conflicts broadcast as 409 with the
        // pinned code (spec §6.4); applied outcomes pass through.
        let outcome = self
            .core
            .change_transfer(command)
            .await
            .map_err(map_core_error)?;
        match outcome {
            TransferOutcome::Conflict { transfer, code } => {
                Err(ApplicationError::conflict(
                    code.as_str(),
                    format!(
                        "ownership transfer for bot '{}' target '{}' \
                         conflicts: {}",
                        transfer.bot_id,
                        transfer.target_owner_actor_id,
                        code.as_str()
                    ),
                ))
            }
            applied => Ok(applied),
        }
    }
}

/// `bot_authority_operator_forbidden` Forbidden (403).
fn operator_forbidden(detail: &str) -> ApplicationError {
    ApplicationError::forbidden_code(
        ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN,
        detail.to_string(),
    )
}

/// Core error → application error with pinned public codes
/// (spec §7). Internal details never become public codes; the
/// adapter redacts `Internal` bodies to `Internal server error`
/// and logs the detail.
fn map_core_error(error: bcs_service_api::ServiceError) -> ApplicationError {
    use bcs_service_api::ServiceError;
    match error {
        // 409: pinned authority conflict codes ride the
        // Store message prefix.
        ServiceError::Conflict(message) => {
            if let Some(rest) = strip_pinned(&message, ERROR_OWNER_NOT_MANAGER) {
                return ApplicationError::conflict(ERROR_OWNER_NOT_MANAGER, rest);
            }
            if let Some(rest) =
                strip_pinned(&message, ERROR_BOT_AUTHORITY_NOT_INITIALIZED)
            {
                return ApplicationError::conflict(
                    ERROR_BOT_AUTHORITY_NOT_INITIALIZED,
                    rest,
                );
            }
            if let Some(rest) =
                strip_pinned(&message, ERROR_BOT_MANAGER_STOCK_LIMIT)
            {
                return ApplicationError::conflict(
                    ERROR_BOT_MANAGER_STOCK_LIMIT,
                    rest,
                );
            }
            ApplicationError::conflict("conflict", message)
        }
        ServiceError::Forbidden(message) => {
            if let Some(rest) = strip_pinned(
                &message,
                ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN,
            ) {
                return ApplicationError::forbidden_code(
                    ERROR_BOT_AUTHORITY_OPERATOR_FORBIDDEN,
                    rest,
                );
            }
            ApplicationError::forbidden(message)
        }
        // 400: input-level authority codes.
        ServiceError::InvalidOperation { message, .. } => {
            if let Some(rest) =
                strip_pinned(&message, ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN)
            {
                return ApplicationError::invalid(
                    ERROR_BOT_AUTHORITY_HUMAN_UNKNOWN,
                    rest,
                );
            }
            if let Some(rest) =
                strip_pinned(&message, ERROR_BOT_MANAGER_INPUT_LIMIT)
            {
                return ApplicationError::invalid(ERROR_BOT_MANAGER_INPUT_LIMIT, rest);
            }
            ApplicationError::invalid("invalid_request", message)
        }
        ServiceError::Unauthorized(message) => ApplicationError::forbidden(message),
        ServiceError::BotNotFound(bot_id)
        | ServiceError::BotNotRegistered(bot_id) => ApplicationError::not_found(
            "bot_not_found",
            format!("Bot '{bot_id}' not found"),
        ),
        // 500 (database/integrity/uncertain-outcome): details are
        // logged and redacted by the adapter.
        error => ApplicationError::internal(error.to_string()),
    }
}

/// Split a `"<code>: detail"` message into the pinned code's
/// detail (the Store carries public codes this way).
fn strip_pinned(message: &str, code: &str) -> Option<String> {
    message
        .strip_prefix(code)
        .and_then(|rest| rest.strip_prefix(':'))
        .map(str::trim_start)
        .map(str::to_string)
}
