//! `ConnectService` real implementation for the edge-permission model.
//!
//! T13 of the friend→edge-permission reform (installment 3). Implements
//! [`ConnectService`] over the Installment 2 repo ports
//! (`EdgeGrantRepoPort` + `PermissionProfileRepoPort` +
//! `PermissionRequestRepoPort`) plus the T12 [`BotActorConfigRepoPort`] narrow
//! read of `bcs_bots`.
//!
//! `authority` (plan Task 3) holds the strict authority resolution core
//! ([`BotAuthorityCoreServiceImpl`]) — kept separate from Connect; the
//! application-layer `BotAuthorityHook` composes on it and never touches
//! the repo port directly.
//!
//! The service holds an injected `env` (env-isolation: every repo call is
//! scoped to it) and builds friend edges per D3:
//!
//! - Human→Bot: 1 edge (caller→to_bot, ref=to_bot.default) + 1 request.
//! - Bot↔Bot: 2 edges (caller→to_bot ref=to_bot.default AND to_bot→caller
//!   ref=caller.default) + 2 requests (single approve approves both, §4.1).
//! - Human↔Human / Bot→Human: rejected (`InvalidOperation`).
//!
//! See `docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md`
//! §4.1 (connect lifecycle), §4.2 (decision tree), D3 (4-case), D11
//! (id-by-prefix), D12 (friend edge = grant_ref_id == target.default).
//!
//! `AdmissionService` (T14) is a separate task and lives in this same crate
//! later; this file deliberately contains only the crate skeleton +
//! `ConnectService`.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use tracing::{info, warn};
use uuid::Uuid;
use bcs_domain::actor::ActorKind;
use bcs_domain::edge_permission::{
    AdmissionReason, AdmissionResult, AuthzContext, AuthzGrantRef, EdgeGrant,
    FriendListEntry, GrantKind, GrantSource, PermissionRequest, RequestKind,
    RequestStatus,
};
use bcs_service_api::application::admission::AdmissionService;
use bcs_service_api::application::connect::{
    ConnectResult, ConnectService, ConnectStatus, FriendEntriesPage, FriendListQuery, RequestDirection, RequestsPage,
};
use bcs_service_api::port::{
    FriendAuthSyncAction, FriendAuthSyncCommand, FriendAuthSyncPort,
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::types::BotOperationContext;
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::{EdgePermissionFriendSyncService, ServiceError, ServiceResult};
use bcs_user_directory_api::UserDirectoryPlugin;

use connect_support::{
    actor_kind_of, bot_friend_ext_no_check_scope_friend_deps,
    bot_friend_ext_view_scope_agent_friend_deps, bot_friend_ext_view_scope_user_friend_deps,
    is_lock_wait_timeout, is_private_visibility, normalize_policy_value,
};

pub mod authority;
pub mod admission;
mod friend_sync;
mod connect_helpers;
mod connect_support;
pub(crate) use connect_support::*;

pub use admission::DbAdmissionService;
pub use authority::BotAuthorityCoreServiceImpl;

/// Generate a fresh external request id (a bare UUID v4, simple form — no
/// prefix). The internal bigint PK (`permission_requests.id`) is assigned by
/// the DB; this string is the client-facing stable id stored in the
/// `request_id` column.
pub(crate) fn new_request_id() -> String {
    Uuid::new_v4().simple().to_string()
}

/// DB-backed `ConnectService` implementation.
///
/// Holds the four repo ports (injected as `Arc<dyn ...>`) plus the
/// env-isolation string. The env is a service-level concern (the composition
/// root knows which environment this process serves); the `ConnectService`
/// trait methods carry no env parameter, so the service owns it.
pub struct DbConnectService {
    pub(crate) edge_grants: Arc<dyn EdgeGrantRepoPort>,
    pub(crate) profiles: Arc<dyn PermissionProfileRepoPort>,
    pub(crate) requests: Arc<dyn PermissionRequestRepoPort>,
    pub(crate) bot_config: Arc<dyn BotActorConfigRepoPort>,
    pub(crate) user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    pub(crate) friend_connect_notification: Arc<dyn FriendConnectNotificationPort>,
    /// BCS→backend friend-auth-sync; used by Task 11 triggers (grant/revoke).
    /// This is a FRIEND-relation sync only (spec §12.3): manager grants,
    /// ownership transfers or role lifecycles NEVER trigger it, and its
    /// external addressing keeps the historical backend conventions
    /// (creator work no / external Bot-ID suffix) — the current BCS owner is
    /// never projected into it.
    pub(crate) friend_auth_sync: Arc<dyn FriendAuthSyncPort>,
    /// Current-owner/role facts for the friend lane (plan Task 12): approval
    /// notifications address the CURRENT owner through the strict authority
    /// core (spec §12.3/§12.4), and the acting-actor authorization for Humans
    /// resolving through a Bot relies on live role facts — never `created_by`.
    /// `None` keeps the legacy creator-recipients for fixture-only assemblies
    /// that run without the authority schema.
    pub(crate) authority: Option<Arc<dyn BotAuthorityCoreService>>,
    pub(crate) env: String,
}

impl DbConnectService {
    pub fn new(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        profiles: Arc<dyn PermissionProfileRepoPort>,
        requests: Arc<dyn PermissionRequestRepoPort>,
        bot_config: Arc<dyn BotActorConfigRepoPort>,
        user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
        friend_connect_notification: Arc<dyn FriendConnectNotificationPort>,
        friend_auth_sync: Arc<dyn FriendAuthSyncPort>,
        env: String,
    ) -> Self {
        Self {
            edge_grants,
            profiles,
            requests,
            bot_config,
            user_directory,
            friend_connect_notification,
            friend_auth_sync,
            authority: None,
            env,
        }
    }

    /// Wire the strict authority core for current-owner recipients and
    /// acting-actor authorization (production/bootstrap always does).
    pub fn with_authority(
        mut self,
        authority: Arc<dyn BotAuthorityCoreService>,
    ) -> Self {
        self.authority = Some(authority);
        self
    }

    /// Reject a new command that arrived without its REQUIRED operation
    /// context (spec §12.5): the whole use case stops fail-closed instead of
    /// recording a forged System operator. History rows predating the cutover
    /// read back fine; this gates NEW writes only.
    fn require_operation(operation: &BotOperationContext) -> ServiceResult<()> {
        if operation.operation_id.trim().is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "friend-connect write is missing its required                          BotOperationContext (empty operation id)"
                    .into(),
                request_id: None,
            });
        }
        Ok(())
    }

    /// Resolve the CURRENT owner user id of `bot` through the strict
    /// authority core (spec §12.4). Fall back to the historical creator
    /// (`created_by`) only when the authority is not wired (fixture-only
    /// assemblies) or the bot is not ownership-initialized (version 0 keeps
    /// its legacy creator as the only notification addressee). A corrupt
    /// authority state NEVER fabricates an owner: the fallback stays the
    /// historical creator and the inconsistency is logged for governance.
    async fn current_bot_owner_work_no(
        &self,
        bot: &str,
        cfg_owner: Option<&str>,
    ) -> String {
        if let Some(authority) = self.authority.as_ref() {
            match authority.ownership(bot).await {
                Ok(state) => return state.owner_user_id,
                Err(ServiceError::Authority(
                    bcs_service_api::types::error::AuthorityError::OwnershipNotInitialized { .. },
                )) => {}
                Err(err) => {
                    warn!(
                        bot_id = %bot,
                        env = %self.env,
                        error = %err,
                        "friend lane: current-owner resolution failed; keeping the historical creator recipient"
                    );
                }
            }
        }
        cfg_owner
            .map(|owner| owner.strip_prefix("human_").unwrap_or(owner))
            .unwrap_or_default()
            .to_string()
    }

    /// The env this service is scoped to.
    pub fn env(&self) -> &str {
        &self.env
    }

    fn log_request_lookup_miss(&self, operation: &str, request_id: &str, extra: Option<&str>) {
        match extra {
            Some(extra) => warn!(
                env = %self.env,
                operation = %operation,
                request_id = %request_id,
                extra = %extra,
                "permission request lookup missed"
            ),
            None => warn!(
                env = %self.env,
                operation = %operation,
                request_id = %request_id,
                "permission request lookup missed"
            ),
        }
    }
}

/// Id-by-prefix actor-kind discriminator (D11).
///
/// `human_` prefix → [`ActorKind::Human`]; anything else (composite ids with
/// `:` AND bare BCS-native bot uuids without `:`) → [`ActorKind::Bot`]. The
/// direction-validity gate in `create_connect` is what rejects invalid
/// directions (Human↔Human / Bot→Human) — not this helper.
#[async_trait]
impl ConnectService for DbConnectService {
    async fn authorize_acting_actor(
        &self,
        staff_no: &str,
        requested_actor_id: &str,
    ) -> ServiceResult<bool> {
        // Plan Task 12 (spec §12.1(5)/§12.4): the application owns the
        // acting-actor question and answers it from the LIVE role facts —
        // owner or manager on this exact Bot. `created_by`, the Bot-ID
        // suffix and signed claims are no longer authority. Humans may only
        // act as themselves (the id already matched at the caller), so this
        // answers Bot targets only.
        if requested_actor_id.starts_with("human_") {
            return Ok(false);
        }
        let Some(authority) = self.authority.as_ref() else {
            // Fail-closed: an assembly without the authority schema may not
            // upgrade identity into authority.
            return Err(ServiceError::Forbidden(
                "acting-actor authorization requires the authority service; \
                 refusing to fall back to the legacy creator check"
                    .to_string(),
            ));
        };
        let role = authority
            .role(staff_no, requested_actor_id)
            .await
            .map_err(|err| {
                warn!(
                    staff_no = %staff_no,
                    bot_id = %requested_actor_id,
                    env = %self.env,
                    error = %err,
                    "acting-actor role resolution failed"
                );
                err
            })?;
        Ok(role.is_some())
    }

    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
        request_auth: Option<RequestAuthHeaders>,
        operation: BotOperationContext,
    ) -> ServiceResult<ConnectResult> {
        Self::require_operation(&operation)?;
        // 1. Self-add guard.
        if caller == to_bot {
            return Err(ServiceError::CannotAddSelf);
        }

        // 2. Direction validity (D3): only Human→Bot and Bot↔Bot are valid.
        let caller_kind = actor_kind_of(caller);
        let target_kind = actor_kind_of(to_bot);
        let valid_direction = match (caller_kind, target_kind) {
            (ActorKind::Human, ActorKind::Bot) => true,
            (ActorKind::Bot, ActorKind::Bot) => true,
            _ => false,
        };
        if !valid_direction {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "unsupported connect direction: caller={:?} kind={:?}, to_bot={:?} kind={:?} \
                     (valid: Human→Bot, Bot↔Bot)",
                    caller, caller_kind, to_bot, target_kind
                ),
                request_id: None,
            });
        }

        // 3. Load target bot config.
        let cfg = self
            .bot_config
            .get(to_bot, &self.env)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(to_bot.to_string()))?;

        // 4. Idempotency: already friends → Approved (no new ids).
        if self.edge_grants.has_friend_edge(caller, to_bot, &self.env).await {
            return Ok(ConnectResult {
                request_ids: vec![],
                edge_ids: vec![],
                status: ConnectStatus::Approved,
                auto_accepted: false,
            });
        }
        // Idempotency: pending connect already exists in this direction →
        // Pending (don't re-insert, don't 409 — return the existing state).
        let pending = self.find_pending_connect(caller, to_bot).await;
        if !pending.is_empty() {
            let request_ids: Vec<String> = pending.into_iter().map(|r| r.request_id).collect();
            return Ok(ConnectResult {
                request_ids,
                edge_ids: vec![],
                status: ConnectStatus::Pending,
                auto_accepted: false,
            });
        }

        // 5. Existing visibility/status gates.
        // Bots collaborate under `visibility`; humans add under `user_visibility`
        // (mirrors /bots/search viewer-kind selection). `visibility=private`
        // blocks bot→bot collaboration; `user_visibility=private` blocks human→bot
        // add. The bot-facing `visibility` no longer gates human callers, so a
        // `visibility=private` + `user_visibility=public` bot stays human-addable.
        if cfg.status == "hidden" {
            return Err(ServiceError::BotHidden(to_bot.to_string()));
        }
        if caller_kind == ActorKind::Bot && is_private_visibility(&cfg.visibility) {
            return Err(ServiceError::PrivateBotCannotCollaborate);
        }
        if caller_kind == ActorKind::Human && is_private_visibility(&cfg.user_visibility) {
            return Err(ServiceError::Forbidden(format!(
                "bot '{to_bot}' is not human-addable"
            )));
        }

        if caller_kind == ActorKind::Human && normalize_policy_value(&cfg.user_visibility) == "protected" {
            let user_friend_scope = bot_friend_ext_view_scope_user_friend_deps(&cfg.friend_ext);
            if !user_friend_scope.is_empty()
                && !self
                    .actor_department_matches_friend_scope(caller, &user_friend_scope, request_auth.as_ref())
                    .await
            {
                return Err(ServiceError::Forbidden(format!(
                    "caller '{caller}' is not within target bot '{to_bot}' friend scope"
                )));
            }
        }
        if caller_kind == ActorKind::Bot && normalize_policy_value(&cfg.visibility) == "protected" {
            let agent_friend_scope = bot_friend_ext_view_scope_agent_friend_deps(&cfg.friend_ext);
            if !agent_friend_scope.is_empty()
                && !self
                    .actor_department_matches_friend_scope(caller, &agent_friend_scope, request_auth.as_ref())
                    .await
            {
                return Err(ServiceError::Forbidden(format!(
                    "bot owner '{caller}' is not within target bot '{to_bot}' friend scope"
                )));
            }
        }

        let friend_strategy = normalize_policy_value(&cfg.friend_check_in_strategy);
        let dept_free_auto_approved = friend_strategy == "dept_free"
            && self
                .caller_department_matches_friend_allowlist(caller, &cfg, request_auth.as_ref())
                .await;
        let needs_approval = !(friend_strategy == "open" || dept_free_auto_approved);
        // Dispatch on the caller-appropriate visibility: bots on `visibility`,
        // humans on `user_visibility`. The matching `private` case is rejected
        // above for that kind, so only public/protected reach here in practice.
        let collab_visibility = if caller_kind == ActorKind::Human {
            normalize_policy_value(&cfg.user_visibility)
        } else {
            normalize_policy_value(&cfg.visibility)
        };
        match collab_visibility.as_str() {
            "public" | "protected" => {
                if needs_approval {
                    let request_ids = self
                        .insert_pending_connect(caller, to_bot, caller_kind, target_kind, message.clone(), &operation)
                        .await?;
                    self.emit_friend_connect_notification(
                        FriendConnectNotificationKind::ApprovalRequested,
                        request_ids.clone(),
                        caller,
                        to_bot,
                        self.target_notification_recipients(&cfg).await,
                        message.as_deref(),
                        request_auth.clone(),
                    )
                    .await?;
                    Ok(ConnectResult {
                        request_ids,
                        edge_ids: vec![],
                        status: ConnectStatus::Pending,
                        auto_accepted: false,
                    })
                } else {
                    let (edge_ids, default_refs) = self
                        .build_connect_edges(caller, to_bot, caller_kind, target_kind, &operation)
                        .await?;
                    let request_ids = self
                        .insert_approved_connect_requests(
                            caller,
                            to_bot,
                            caller_kind,
                            target_kind,
                            "auto",
                            &edge_ids,
                            &default_refs,
                            message.as_deref(),
                            &operation,
                        )
                        .await?;

                    // 11c: best-effort friend-auth-sync grant trigger (auto-
                    // approve, human→bot). Principal = the applicant (caller),
                    // since the auto-approve is decided on visibility/allowlist,
                    // not a separate owner action.
                    if caller_kind == ActorKind::Human && target_kind == ActorKind::Bot {
                        let owner_work_no = self
                            .owner_work_no_from_bot_config(&to_bot, &self.env)
                            .await
                            .unwrap_or_default();
                        let command = FriendAuthSyncCommand {
                            env: self.env.clone(),
                            bot_id: to_bot.to_string(),
                            owner_work_no,
                            human_work_no: caller
                                .strip_prefix("human_")
                                .unwrap_or(&caller)
                                .to_string(),
                            action: FriendAuthSyncAction::Grant,
                            request_id: request_ids.first().map(|s| s.to_string()),
                            request_auth: request_auth.clone(),
                        };
                        if let Err(err) = self.friend_auth_sync.sync(command).await {
                            warn!(
                                error = %err,
                                bot_id = %to_bot,
                                "friend-auth-sync grant (auto) failed"
                            );
                        }
                    }

                    Ok(ConnectResult {
                        request_ids,
                        edge_ids,
                        status: ConnectStatus::Approved,
                        auto_accepted: true,
                    })
                }
            }
            other => Err(ServiceError::InvalidOperation {
                message: format!("unsupported bot visibility '{other}' for '{to_bot}'"),
                request_id: None,
            }),
        }
    }

    async fn approve(
        &self,
        request_id: &str,
        decider: &str,
        request_auth: Option<RequestAuthHeaders>,
        operation: BotOperationContext,
    ) -> ServiceResult<Vec<u64>> {
        Self::require_operation(&operation)?;
        let req = self
            .requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| {
                self.log_request_lookup_miss("approve", request_id, Some(decider));
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })?;

        // Only pending requests can be approved.
        if req.status == RequestStatus::Approved {
            // Idempotent: return existing edge ids (if any).
            return Ok(req.edge_id.into_iter().collect());
        }
        if req.status != RequestStatus::Pending {
            return Err(ServiceError::CannotAcceptRejected);
        }
        if req.request_kind != RequestKind::Connect {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "approve: request {} is not a connect request (kind={:?})",
                    req.request_id, req.request_kind
                ),
                request_id: Some(req.request_id.to_string()),
            });
        }

        let caller = req.from_id.clone();
        let to_bot = req.to_id.clone();
        let caller_kind = actor_kind_of(&caller);
        let target_kind = actor_kind_of(&to_bot);

        // Build the edge(s) ONLY (no snapshot request rows — the pending row
        // is decided in place below, avoiding duplicate request records).
        let (edge_ids, _default_refs) = self
            .build_connect_edges(&caller, &to_bot, caller_kind, target_kind, &operation)
            .await?;

        // Decide the original pending FORWARD row approved + backfill edge.
        // Each persisted record audited under its own sub-operation (plan
        // Task 12: one operation slot covers one logical step on one
        // record), keeping the SAME verified operator identity.
        let forward_edge = edge_ids.first().cloned();
        if let Some(eid) = forward_edge.as_ref() {
            self.requests
                .backfill_edge_id(
                    req.request_id.as_str(),
                    &self.env,
                    *eid,
                    &operation.for_sub_record(&format!("backfill-{}", req.request_id)),
                )
                .await?;
        }
        self.requests
            .decide(
                req.request_id.as_str(),
                &self.env,
                RequestStatus::Approved,
                decider,
                None,
                &operation.for_sub_record(&format!("decide-{}", req.request_id)),
            )
            .await?;

        // §4.1: a single accept on a Bot↔Bot connect approves BOTH requests
        // together. If this is a Bot↔Bot connect, find the reverse pending
        // request (to_bot→caller) and approve it + backfill the reverse edge.
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            let reverse_edge = edge_ids.get(1).cloned();
            let reverse_pending = self.find_pending_connect(&to_bot, &caller).await;
            for r in reverse_pending {
                if let Some(eid) = reverse_edge.as_ref() {
                    self.requests
                        .backfill_edge_id(
                            r.request_id.as_str(),
                            &self.env,
                            *eid,
                            &operation.for_sub_record(&format!("backfill-{}", r.request_id)),
                        )
                        .await?;
                }
                self.requests
                    .decide(
                        r.request_id.as_str(),
                        &self.env,
                        RequestStatus::Approved,
                        decider,
                        None,
                        &operation.for_sub_record(&format!("decide-{}", r.request_id)),
                    )
                    .await?;
            }
        }

        // 11b: best-effort friend-auth-sync grant trigger (manual approve,
        // human→bot). The inbound principal (owner doing the approving) is
        // forwarded so the backend can authenticate the work-order call.
        if caller_kind == ActorKind::Human && target_kind == ActorKind::Bot {
            let owner_work_no = self
                .owner_work_no_from_bot_config(&to_bot, &self.env)
                .await
                .unwrap_or_default();
            let command = FriendAuthSyncCommand {
                env: self.env.clone(),
                bot_id: to_bot.clone(),
                owner_work_no,
                human_work_no: caller
                    .strip_prefix("human_")
                    .unwrap_or(&caller)
                    .to_string(),
                action: FriendAuthSyncAction::Grant,
                request_id: Some(request_id.to_string()),
                request_auth: request_auth.clone(),
            };
            if let Err(err) = self.friend_auth_sync.sync(command).await {
                warn!(
                    error = %err,
                    bot_id = %to_bot,
                    "friend-auth-sync grant (manual) failed"
                );
            }
        }

        Ok(edge_ids)
    }

    async fn reject(
        &self,
        request_id: &str,
        decider: &str,
        reason: Option<String>,
        operation: BotOperationContext,
    ) -> ServiceResult<()> {
        Self::require_operation(&operation)?;
        let req = self
            .requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| {
                self.log_request_lookup_miss("reject", request_id, Some(decider));
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })?;

        if req.status == RequestStatus::Approved {
            return Err(ServiceError::CannotRejectAccepted);
        }
        if req.status != RequestStatus::Pending {
            // Already rejected/cancelled — idempotent no-op.
            return Ok(());
        }

        self.requests
            .decide(
                req.request_id.as_str(),
                &self.env,
                RequestStatus::Rejected,
                decider,
                reason.as_deref(),
                &operation.for_sub_record(&format!("decide-{}", req.request_id)),
            )
            .await?;

        // §4.1: Bot↔Bot — reject the reverse pending request too.
        if actor_kind_of(&req.from_id) == ActorKind::Bot
            && actor_kind_of(&req.to_id) == ActorKind::Bot
            && req.request_kind == RequestKind::Connect
        {
            let reverse_pending = self
                .find_pending_connect(&req.to_id, &req.from_id)
                .await;
            for r in reverse_pending {
                self.requests
                    .decide(
                        r.request_id.as_str(),
                        &self.env,
                        RequestStatus::Rejected,
                        decider,
                        reason.as_deref(),
                        &operation.for_sub_record(&format!("decide-{}", r.request_id)),
                    )
                    .await?;
            }
        }
        Ok(())
    }

    async fn cancel(&self, request_id: &str, operator: &str, operation: BotOperationContext) -> ServiceResult<()> {
        Self::require_operation(&operation)?;
        let req = self
            .requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| {
                self.log_request_lookup_miss("cancel", request_id, Some(operator));
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })?;

        // Idempotent: an already-cancelled or rejected request is a no-op Ok
        // (spec: "已 rejected/cancelled 幂等"). Only pending requests can be
        // transitioned to Cancelled; an Approved request cannot be cancelled
        // (that's an unfriend/revoke, not a cancel).
        match req.status {
            RequestStatus::Pending => {}
            RequestStatus::Cancelled | RequestStatus::Rejected => return Ok(()),
            RequestStatus::Approved => {
                return Err(ServiceError::InvalidOperation {
                    message: format!(
                        "cancel: request {} is approved (cancel not allowed; use revoke_friend)",
                        req.request_id
                    ),
                    request_id: Some(req.request_id.to_string()),
                });
            }
        }

        // The withdrawing caller is verified against the request's own
        // creator/requester identity — the Friend lane keeps this decision
        // fully separate from any role/manager lifecycle (the decider below
        // only ever records a connect-lane actor id, never a manager audit
        // id).
        if req.created_by.as_str() != operator && req.from_id.as_str() != operator {
            return Err(ServiceError::Forbidden(format!(
                "actor '{operator}' cannot cancel request '{}' created by '{}'",
                request_id, req.created_by
            )));
        }
        let decider = req.created_by.as_str();
        self.requests
            .decide(
                req.request_id.as_str(),
                &self.env,
                RequestStatus::Cancelled,
                decider,
                Some("cancelled by caller"),
                &operation.for_sub_record(&format!("decide-{}", req.request_id)),
            )
            .await?;

        // Bot↔Bot: cancel the reverse pending request as well.
        if actor_kind_of(&req.from_id) == ActorKind::Bot
            && actor_kind_of(&req.to_id) == ActorKind::Bot
            && req.request_kind == RequestKind::Connect
        {
            let reverse_pending = self
                .find_pending_connect(&req.to_id, &req.from_id)
                .await;
            for r in reverse_pending {
                self.requests
                    .decide(
                        r.request_id.as_str(),
                        &self.env,
                        RequestStatus::Cancelled,
                        decider,
                        Some("cancelled by caller"),
                        &operation.for_sub_record(&format!("decide-{}", r.request_id)),
                    )
                    .await?;
            }
        }
        Ok(())
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<PermissionRequest> {
        self.requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| {
                self.log_request_lookup_miss("get_request", request_id, None);
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })
    }

    async fn revoke_friend(
        &self,
        caller: &str,
        target: &str,
        request_auth: Option<RequestAuthHeaders>,
        operation: BotOperationContext,
    ) -> ServiceResult<Vec<u64>> {
        Self::require_operation(&operation)?;
        // D12 friend edges are `grant_ref_id == target.default` (caller→target)
        // or `grant_ref_id == caller.default` (target→caller, Bot↔Bot). Revoke
        // exactly those friend edges; leave other (profile/rules) edges alone.
        // Returns the revoked edge_ids (B4c fix — previously a count).
        let mut revoked: Vec<u64> = Vec::new();

        // Forward: caller → target, ref == target's default profile id.
        if let Some(target_default) = self
            .edge_grants
            .get_default_profile_id(target, &self.env)
            .await
        {
            let forward = self
                .edge_grants
                .list_active_grants(caller, target, &self.env)
                .await;
            for g in forward {
                if g.grant_ref_id == target_default && g.grant_kind == GrantKind::PermissionProfile
                {
                    self.edge_grants
                        .revoke_grant(
                            g.edge_id,
                            &self.env,
                            &operation.for_sub_record(&format!("edge-{}-fwd", g.edge_id)),
                        )
                        .await?;
                    revoked.push(g.edge_id);
                }
            }
        }

        // Reverse: target → caller, ref == caller's default profile id
        // (only meaningful for bot↔bot, where caller is itself a bot).
        if let Some(caller_default) = self
            .edge_grants
            .get_default_profile_id(caller, &self.env)
            .await
        {
            let reverse = self
                .edge_grants
                .list_active_grants(target, caller, &self.env)
                .await;
            for g in reverse {
                if g.grant_ref_id == caller_default && g.grant_kind == GrantKind::PermissionProfile
                {
                    self.edge_grants
                        .revoke_grant(
                            g.edge_id,
                            &self.env,
                            &operation.for_sub_record(&format!("edge-{}-rev", g.edge_id)),
                        )
                        .await?;
                    revoked.push(g.edge_id);
                }
            }
        }

        // 11d: best-effort friend-auth-sync revoke trigger (human→bot). The
        // inbound principal (the user doing the unfriend) is forwarded so the
        // backend can authenticate the work-order teardown call.
        let caller_kind = actor_kind_of(caller);
        let target_kind = actor_kind_of(target);
        if caller_kind == ActorKind::Human && target_kind == ActorKind::Bot {
            let owner_work_no = self
                .owner_work_no_from_bot_config(target, &self.env)
                .await
                .unwrap_or_default();
            let command = FriendAuthSyncCommand {
                env: self.env.clone(),
                bot_id: target.to_string(),
                owner_work_no,
                human_work_no: caller
                    .strip_prefix("human_")
                    .unwrap_or(caller)
                    .to_string(),
                action: FriendAuthSyncAction::Revoke,
                request_id: None,
                request_auth: request_auth.clone(),
            };
            if let Err(err) = self.friend_auth_sync.sync(command).await {
                warn!(
                    error = %err,
                    bot_id = %target,
                    "friend-auth-sync revoke failed (best-effort)"
                );
            }
        }

        // TODO(installment-5): spec §4.1 models unfriend as a Revoke-kind
        // request that the owner directly approves; this direct-revoke path is
        // sufficient for T13. The revoke-request flow can layer on later.
        Ok(revoked)
    }

    async fn list_friends(&self, actor: &str) -> ServiceResult<Vec<FriendListEntry>> {
        let ids = self.edge_grants.list_friends(actor, &self.env).await;
        let entries = ids
            .into_iter()
            .map(|id| FriendListEntry {
                actor_id: id.clone(),
                // TODO(installment-3): enrich name/summary/is_online via the
                // bot registry / presence port. Left None for now; the plan
                // marks enrichment as optional for T13.
                name: None,
                summary: None,
                is_online: false,
                kind: actor_kind_of(&id),
            })
            .collect();
        Ok(entries)
    }

    async fn list_friends_paginated(
        &self,
        actor: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendEntriesPage> {
        let page = self.edge_grants.list_friends_paginated(actor, &self.env, query).await?;
        let items = page.items.into_iter().map(|id| FriendListEntry {
            kind: actor_kind_of(&id),
            actor_id: id,
            name: None,
            summary: None,
            is_online: false,
        }).collect();
        Ok(FriendEntriesPage { items, total: page.total })
    }

    async fn list_requests(
        &self,
        actor: &str,
        direction: RequestDirection,
        status: Option<RequestStatus>,
        page: u32,
        page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        // Received: to_id == actor (inbox). Sent: from_id == actor (outbox).
        // All: inbox ∪ sent, deduped by request_id. Each branch is backed by a
        // repo call; status is pushed down to SQL where possible (the All
        // union filters in memory after the two repo calls, since the two
        // queries are independent and a single SQL UNION would bypass the
        // repo-port abstraction).
        let all: Vec<PermissionRequest> = match direction {
            RequestDirection::Received => {
                self.requests.list_inbox(actor, &self.env, status).await
            }
            RequestDirection::Sent => {
                self.requests.list_sent(actor, &self.env, status).await
            }
            RequestDirection::All => {
                let inbox = self.requests.list_inbox(actor, &self.env, status).await;
                let sent = self.requests.list_sent(actor, &self.env, status).await;
                // Dedup by request_id (a self-connect Bot↔Bot produces two
                // rows for the same pair, but request_ids are unique per row,
                // so dedup only collapses the identity overlap where the same
                // request is both from+to — which cannot happen here; this is
                // defensive). Each repo list is already `gmt_modified DESC`,
                // so chaining inbox then sent preserves recency within each
                // direction (a global re-sort would need a row timestamp the
                // domain `PermissionRequest` no longer carries).
                let mut seen: HashSet<String> =
                    HashSet::new();
                let mut combined: Vec<PermissionRequest> = Vec::with_capacity(
                    inbox.len() + sent.len(),
                );
                for r in inbox.into_iter().chain(sent.into_iter()) {
                    if seen.insert(r.request_id.clone()) {
                        combined.push(r);
                    }
                }
                combined
            }
        };

        let total = all.len() as u32;
        let page_size = if page_size == 0 { 20 } else { page_size };
        let page = if page == 0 { 1 } else { page };
        let start = ((page - 1) * page_size) as usize;
        let items = if start >= all.len() {
            Vec::new()
        } else {
            let end = (start + page_size as usize).min(all.len());
            all[start..end].to_vec()
        };

        Ok(RequestsPage {
            items,
            total,
            page,
            page_size,
        })
    }

}
