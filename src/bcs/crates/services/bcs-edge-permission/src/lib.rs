//! `ConnectService` real implementation for the edge-permission model.
//!
//! T13 of the friend→edge-permission reform (installment 3). Implements
//! [`ConnectService`] over the Installment 2 repo ports
//! (`EdgeGrantRepoPort` + `PermissionProfileRepoPort` +
//! `PermissionRequestRepoPort`) plus the T12 [`BotActorConfigRepoPort`] narrow
//! read of `bcs_bots`.
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
    AdmissionReason, AdmissionResult, AuthzContext, AuthzGrantRef, EdgeGrant, EdgeStatus,
    FriendListEntry, GrantKind, GrantSource, OriginatorPolicyType, PermissionRequest, RequestKind,
    RequestStatus,
};
use bcs_service_api::application::admission::AdmissionService;
use bcs_service_api::application::connect::{
    ConnectResult, ConnectService, ConnectStatus, RequestDirection, RequestsPage,
};
use bcs_service_api::port::{
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::{EdgePermissionFriendSyncService, ServiceError, ServiceResult};
use bcs_user_directory_api::{UserDirectoryLookupContext, UserDirectoryPlugin};

/// Generate a fresh external request id (a bare UUID v4, simple form — no
/// prefix). The internal bigint PK (`permission_requests.id`) is assigned by
/// the DB; this string is the client-facing stable id stored in the
/// `request_id` column.
fn new_request_id() -> String {
    Uuid::new_v4().simple().to_string()
}

/// DB-backed `ConnectService` implementation.
///
/// Holds the four repo ports (injected as `Arc<dyn ...>`) plus the
/// env-isolation string. The env is a service-level concern (the composition
/// root knows which environment this process serves); the `ConnectService`
/// trait methods carry no env parameter, so the service owns it.
pub struct DbConnectService {
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    profiles: Arc<dyn PermissionProfileRepoPort>,
    requests: Arc<dyn PermissionRequestRepoPort>,
    bot_config: Arc<dyn BotActorConfigRepoPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    friend_connect_notification: Arc<dyn FriendConnectNotificationPort>,
    env: String,
}

impl DbConnectService {
    pub fn new(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        profiles: Arc<dyn PermissionProfileRepoPort>,
        requests: Arc<dyn PermissionRequestRepoPort>,
        bot_config: Arc<dyn BotActorConfigRepoPort>,
        user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
        friend_connect_notification: Arc<dyn FriendConnectNotificationPort>,
        env: String,
    ) -> Self {
        Self {
            edge_grants,
            profiles,
            requests,
            bot_config,
            user_directory,
            friend_connect_notification,
            env,
        }
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
fn actor_kind_of(id: &str) -> ActorKind {
    if id.starts_with("human_") {
        ActorKind::Human
    } else {
        ActorKind::Bot
    }
}

fn normalize_friend_sync_pair<'a>(
    a: &'a str,
    b: &'a str,
) -> ServiceResult<Option<(&'a str, &'a str, ActorKind, ActorKind)>> {
    if a == b {
        return Err(ServiceError::CannotAddSelf);
    }
    match (actor_kind_of(a), actor_kind_of(b)) {
        (ActorKind::Human, ActorKind::Bot) => Ok(Some((a, b, ActorKind::Human, ActorKind::Bot))),
        (ActorKind::Bot, ActorKind::Human) => Ok(Some((b, a, ActorKind::Human, ActorKind::Bot))),
        (ActorKind::Bot, ActorKind::Bot) => Ok(Some((a, b, ActorKind::Bot, ActorKind::Bot))),
        (ActorKind::Human, ActorKind::Human) => Ok(None),
    }
}

fn normalize_policy_value(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

fn department_matches_allowlist_entry(actual: &str, allowed: &str) -> bool {
    actual == allowed || actual.starts_with(&format!("{allowed}-"))
}

fn is_private_visibility(value: &str) -> bool {
    matches!(normalize_policy_value(value).as_str(), "private")
}

fn bot_friend_ext_scope_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> HashSet<String> {
    friend_ext
        .get(key)
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn bot_friend_ext_no_check_scope_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "no_check_scope_friend_deps")
}

fn bot_friend_ext_view_scope_user_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "view_scope_user_friend_deps")
}

fn bot_friend_ext_view_scope_agent_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "view_scope_agent_friend_deps")
}

fn user_directory_lookup_context(request_auth: Option<&RequestAuthHeaders>) -> UserDirectoryLookupContext {
    let Some(request_auth) = request_auth else {
        return UserDirectoryLookupContext::default();
    };
    if !request_auth.forwarded_headers.is_empty() {
        return UserDirectoryLookupContext {
            forwarded_headers: request_auth.forwarded_headers.clone(),
        };
    }
    let mut forwarded_headers = Vec::new();
    if let Some(value) = &request_auth.authorization {
        forwarded_headers.push(("authorization".to_string(), value.clone()));
    }
    if let Some(value) = &request_auth.cookie {
        forwarded_headers.push(("cookie".to_string(), value.clone()));
    }
    UserDirectoryLookupContext { forwarded_headers }
}

#[async_trait]
impl ConnectService for DbConnectService {
    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
        request_auth: Option<RequestAuthHeaders>,
    ) -> ServiceResult<ConnectResult> {
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
                        .insert_pending_connect(caller, to_bot, caller_kind, target_kind, message.clone())
                        .await?;
                    self.emit_friend_connect_notification(
                        FriendConnectNotificationKind::ApprovalRequested,
                        request_ids.clone(),
                        caller,
                        to_bot,
                        self.target_notification_recipients(&cfg),
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
                        .build_connect_edges(caller, to_bot, caller_kind, target_kind)
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
                        )
                        .await?;
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

    async fn approve(&self, request_id: &str, decider: &str) -> ServiceResult<Vec<u64>> {
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
            .build_connect_edges(&caller, &to_bot, caller_kind, target_kind)
            .await?;

        // Decide the original pending FORWARD row approved + backfill edge.
        let forward_edge = edge_ids.first().cloned();
        if let Some(eid) = forward_edge.as_ref() {
            self.requests
                .backfill_edge_id(req.request_id.as_str(), &self.env, *eid)
                .await?;
        }
        self.requests
            .decide(
                req.request_id.as_str(),
                &self.env,
                RequestStatus::Approved,
                decider,
                None,
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
                        .backfill_edge_id(r.request_id.as_str(), &self.env, *eid)
                        .await?;
                }
                self.requests
                    .decide(
                        r.request_id.as_str(),
                        &self.env,
                        RequestStatus::Approved,
                        decider,
                        None,
                    )
                    .await?;
            }
        }

        Ok(edge_ids)
    }

    async fn reject(
        &self,
        request_id: &str,
        decider: &str,
        reason: Option<String>,
    ) -> ServiceResult<()> {
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
                    )
                    .await?;
            }
        }
        Ok(())
    }

    async fn cancel(&self, request_id: &str) -> ServiceResult<()> {
        let req = self
            .requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| {
                self.log_request_lookup_miss("cancel", request_id, None);
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

        // The trait passes only request_id; use the request's own `created_by`
        // as the decider (the original requester is the one withdrawing).
        let decider = req.created_by.as_str();
        self.requests
            .decide(
                req.request_id.as_str(),
                &self.env,
                RequestStatus::Cancelled,
                decider,
                Some("cancelled by caller"),
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

    async fn revoke_friend(&self, caller: &str, target: &str) -> ServiceResult<Vec<u64>> {
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
                    self.edge_grants.revoke_grant(g.edge_id, &self.env).await?;
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
                    self.edge_grants.revoke_grant(g.edge_id, &self.env).await?;
                    revoked.push(g.edge_id);
                }
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

// ---- private helpers ------------------------------------------------------

impl DbConnectService {
    /// Verify `caller` owns `bot_id` (spec §3.2 ownership gate for config
    /// writes).
    ///
    /// Rules (mirrors `docs/CLAUDE.md` "Bot Ownership Verification"):
    /// - `created_by` present AND matches `caller` → allow.
    /// - `created_by` present AND differs from `caller` → `Forbidden`.
    /// - `created_by` absent (legacy bot) → allow (auto-claim; CLAUDE.md).
    /// - bot not found in this env → `BotNotFound` (so `PUT` on a missing bot
    ///   surfaces as 404 rather than a misleading 403).
    async fn verify_ownership(&self, bot_id: &str, caller: &str) -> ServiceResult<()> {
        match self.bot_config.get(bot_id, &self.env).await {
            Some(cfg) => match &cfg.created_by {
                Some(owner) if owner == caller => Ok(()),
                Some(_) => Err(ServiceError::Forbidden(format!(
                    "caller '{caller}' does not own bot '{bot_id}'"
                ))),
                None => Ok(()), // legacy bot (no created_by) → auto-claim
            },
            None => Err(ServiceError::BotNotFound(bot_id.to_string())),
        }
    }

    async fn resolve_user_department_code(
        &self,
        actor_id: &str,
        request_auth: Option<&RequestAuthHeaders>,
    ) -> Option<String> {
        let Some(user_directory) = self.user_directory.as_ref() else {
            info!(
                actor_id = %actor_id,
                env = %self.env,
                "skip user department lookup because user directory is not configured"
            );
            return None;
        };
        let staff_no = match actor_kind_of(actor_id) {
            ActorKind::Human => match actor_id.strip_prefix("human_").filter(|staff_no| !staff_no.is_empty()) {
                Some(staff_no) => staff_no.to_string(),
                None => {
                    warn!(
                        actor_id = %actor_id,
                        env = %self.env,
                        "skip user department lookup because human actor id has no staff_no"
                    );
                    return None;
                }
            },
            ActorKind::Bot => {
                let Some(cfg) = self.bot_config.get(actor_id, &self.env).await else {
                    info!(
                        actor_id = %actor_id,
                        env = %self.env,
                        "skip user department lookup because bot config was not found"
                    );
                    return None;
                };
                let Some(created_by) = cfg.created_by else {
                    info!(
                        actor_id = %actor_id,
                        env = %self.env,
                        "skip user department lookup because bot owner staff_no is missing"
                    );
                    return None;
                };
                created_by
            }
        };
        let lookup_context = user_directory_lookup_context(request_auth);
        match user_directory
            .lookup_department_by_staff_no_with_context(&staff_no, &lookup_context)
            .await
        {
            Ok(department) => {
                info!(
                    actor_id = %actor_id,
                    staff_no = %staff_no,
                    department_code = department.as_deref().unwrap_or(""),
                    found = department.is_some(),
                    env = %self.env,
                    "resolved user department for friend connect allowlist check"
                );
                department
            }
            Err(error) => {
                warn!(
                    actor_id = %actor_id,
                    staff_no = %staff_no,
                    error = %error,
                    env = %self.env,
                    "failed to resolve user department for friend connect allowlist check"
                );
                None
            }
        }
    }

    async fn caller_department_matches_friend_allowlist(
        &self,
        caller: &str,
        cfg: &bcs_domain::edge_permission::BotActorConfig,
        request_auth: Option<&RequestAuthHeaders>,
    ) -> bool {
        let allowlist = bot_friend_ext_no_check_scope_friend_deps(&cfg.friend_ext);
        if allowlist.is_empty() {
            return false;
        }
        let Some(caller_department) = self.resolve_user_department_code(caller, request_auth).await else {
            return false;
        };
        allowlist
            .iter()
            .any(|allowed| department_matches_allowlist_entry(&caller_department, allowed))
    }

    async fn actor_department_matches_friend_scope(
        &self,
        actor: &str,
        allowed_departments: &HashSet<String>,
        request_auth: Option<&RequestAuthHeaders>,
    ) -> bool {
        if allowed_departments.is_empty() {
            return false;
        }
        let Some(actor_department) = self.resolve_user_department_code(actor, request_auth).await else {
            return false;
        };
        allowed_departments
            .iter()
            .any(|allowed| department_matches_allowlist_entry(&actor_department, allowed))
    }

    fn target_notification_recipients(
        &self,
        cfg: &bcs_domain::edge_permission::BotActorConfig,
    ) -> Vec<String> {
        cfg.created_by.clone().into_iter().collect()
    }

    async fn emit_friend_connect_notification(
        &self,
        kind: FriendConnectNotificationKind,
        request_ids: Vec<String>,
        applicant_actor_id: &str,
        target_bot_id: &str,
        recipient_user_ids: Vec<String>,
        message: Option<&str>,
        request_auth: Option<RequestAuthHeaders>,
    ) -> ServiceResult<()> {
        if recipient_user_ids.is_empty() {
            return Ok(());
        }
        // Resolve human-readable display names so the notification body says
        // "李四 申请添加你的 Bot「本地代码专家」…" instead of raw actor ids. Falls
        // back to None (the adapter then renders the actor id) on any miss.
        let applicant_name = self.resolve_actor_display_name(applicant_actor_id).await;
        let target_bot_name = self.resolve_actor_display_name(target_bot_id).await;
        // For a bot applicant the backend work-order API expects the applicant's
        // HUMAN owner (the bot's `created_by`) as `applicant_user_id`, not the
        // bot id (which the backend rejects as not matching the acting user).
        // Human applicants leave this `None` — the adapter strips `human_` to a
        // staff_no.
        let applicant_user_id = if actor_kind_of(applicant_actor_id) == ActorKind::Bot {
            self.bot_config
                .get(applicant_actor_id, &self.env)
                .await
                .and_then(|cfg| cfg.created_by)
        } else {
            None
        };
        self.friend_connect_notification
            .notify(FriendConnectNotificationCommand {
                kind,
                env: self.env.clone(),
                request_ids,
                applicant_actor_id: applicant_actor_id.to_string(),
                target_bot_id: target_bot_id.to_string(),
                recipient_user_ids,
                message: message.map(ToOwned::to_owned),
                request_auth,
                applicant_name,
                target_bot_name,
                applicant_user_id,
            })
            .await
    }

    /// Resolve a display name for an actor id: a human's nick name (via the user
    /// directory) or a bot's `name` (from its control-plane config). Returns
    /// `None` when no directory is wired, the staff_no is absent, the lookup
    /// misses, or the bot/config is unknown — callers fall back to the raw id.
    async fn resolve_actor_display_name(&self, actor_id: &str) -> Option<String> {
        match actor_kind_of(actor_id) {
            ActorKind::Human => {
                let user_directory = self.user_directory.as_ref()?;
                let staff_no = actor_id
                    .strip_prefix("human_")
                    .filter(|staff_no| !staff_no.is_empty())?;
                let profile = user_directory
                    .lookup_by_staff_no(staff_no)
                    .await
                    .ok()
                    .flatten()?;
                profile.nick_name.filter(|name| !name.is_empty())
            }
            ActorKind::Bot => {
                let name = self.bot_config.get(actor_id, &self.env).await?.name;
                if name.is_empty() {
                    None
                } else {
                    Some(name)
                }
            }
        }
    }

    /// Find pending `Connect` requests from `from` → `to` in this env.
    ///
    /// Implemented on top of the existing `list_inbox(to, env, status)` (the
    /// repo has no `list_sent`), then filtered by `from_id`. Returns at most
    /// the matching pending connect rows (usually zero or one).
    async fn find_pending_connect(&self, from: &str, to: &str) -> Vec<PermissionRequest> {
        let inbox = self
            .requests
            .list_inbox(to, &self.env, Some(RequestStatus::Pending))
            .await;
        inbox
            .into_iter()
            .filter(|r| r.from_id == from && r.request_kind == RequestKind::Connect)
            .collect()
    }

    /// Insert pending `Connect` request(s) for a manual-approval connect.
    ///
    /// Human→Bot: 1 request (caller→to_bot). Bot↔Bot: 2 requests
    /// (caller→to_bot AND to_bot→caller), both pending.
    async fn insert_pending_connect(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
        message: Option<String>,
    ) -> ServiceResult<Vec<String>> {
        let mut ids = Vec::new();

        // Forward: caller → to_bot.
        let fwd_request_id = new_request_id();
        self.requests
            .insert(PermissionRequest {
                request_id: fwd_request_id.clone(),
                edge_id: None,
                env: self.env.clone(),
                from_id: caller.to_string(),
                to_id: to_bot.to_string(),
                request_kind: RequestKind::Connect,
                requested_ref_id: None,
                requested_rules: None,
                message: message.clone(),
                status: RequestStatus::Pending,
                decision_reason: None,
                created_by: caller.to_string(),
                decided_by: None,
                decided_at: None,
            })
            .await?;
        ids.push(fwd_request_id);

        // Reverse: to_bot → caller (Bot↔Bot only).
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            let rev_request_id = new_request_id();
            self.requests
                .insert(PermissionRequest {
                    request_id: rev_request_id.clone(),
                    edge_id: None,
                    env: self.env.clone(),
                    from_id: to_bot.to_string(),
                    to_id: caller.to_string(),
                    request_kind: RequestKind::Connect,
                    requested_ref_id: None,
                    requested_rules: None,
                    message: None,
                    status: RequestStatus::Pending,
                    decision_reason: None,
                    created_by: caller.to_string(),
                    decided_by: None,
                    decided_at: None,
                })
                .await?;
            ids.push(rev_request_id);
        }

        Ok(ids)
    }

    /// Build friend edges for a connect (auto-approve or manual-approve path).
    /// Returns `(edge_ids, default_refs)` in order: forward first, reverse
    /// second (Bot↔Bot only). `default_refs` carries the forward `to_bot`
    /// default and (Bot↔Bot) the reverse `caller` default, so callers can
    /// populate `requested_ref_id` on snapshot/approved request rows.
    ///
    /// Ensures target/caller default profiles exist (idempotent), reads their
    /// ids, and inserts `EdgeGrant{status=Approved, originator_policy=Any,
    /// grant_kind=PermissionProfile, grant_ref_id=<target.default>}`. Does NOT
    /// insert any request rows — callers own request creation/decision so the
    /// auto path inserts approved snapshots whereas the manual `approve` path
    /// decides existing pending rows in place (no duplicate rows).
    async fn build_connect_edges(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
    ) -> ServiceResult<(Vec<u64>, [u64; 2])> {
        let mut edge_ids = Vec::new();

        // Forward target default profile.
        self.profiles.ensure_default_profile(to_bot, &self.env).await?;
        let target_default = self.default_profile_id_of(to_bot).await?;

        // Forward edge: caller → to_bot (ref = to_bot.default).
        let fwd_edge_id = self
            .edge_grants
            .insert_grant(EdgeGrant {
                edge_id: 0,
                env: self.env.clone(),
                from_id: caller.to_string(),
                to_id: to_bot.to_string(),
                grant_kind: GrantKind::PermissionProfile,
                grant_ref_id: target_default,
                rules: None,
                status: EdgeStatus::Approved,
                originator_policy_type: OriginatorPolicyType::Any,
                originator_policy_data: None,
            })
            .await?;
        edge_ids.push(fwd_edge_id);

        let mut default_refs = [target_default, 0];

        // Reverse edge: to_bot → caller (ref = caller.default), Bot↔Bot only.
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            self.profiles.ensure_default_profile(caller, &self.env).await?;
            let caller_default = self.default_profile_id_of(caller).await?;

            let rev_edge_id = self
                .edge_grants
                .insert_grant(EdgeGrant {
                    edge_id: 0,
                    env: self.env.clone(),
                    from_id: to_bot.to_string(),
                    to_id: caller.to_string(),
                    grant_kind: GrantKind::PermissionProfile,
                    grant_ref_id: caller_default,
                    rules: None,
                    status: EdgeStatus::Approved,
                    originator_policy_type: OriginatorPolicyType::Any,
                    originator_policy_data: None,
                })
                .await?;
            edge_ids.push(rev_edge_id);
            default_refs[1] = caller_default;
        }

        Ok((edge_ids, default_refs))
    }

    /// Resolve a bot's default profile id, preferring the edge-grant cache and
    /// falling back to the profile store. Errors if still missing after an
    /// `ensure_default_profile` (caller's responsibility to ensure first).
    async fn default_profile_id_of(&self, bot_id: &str) -> ServiceResult<u64> {
        if let Some(id) = self
            .edge_grants
            .get_default_profile_id(bot_id, &self.env)
            .await
        {
            return Ok(id);
        }
        self.profiles
            .get_active_default(bot_id, &self.env)
            .await
            .map(|p| p.permission_profile_id)
            .ok_or_else(|| {
                ServiceError::InternalError(format!(
                    "default profile for bot '{bot_id}' missing after ensure"
                ))
            })
    }

    /// Insert already-approved snapshot `PermissionRequest` rows for a connect
    /// that was auto-approved at creation (the `create_connect` auto path).
    /// Returns the new request ids (forward first, reverse second for Bot↔Bot).
    /// The manual `approve` path does NOT call this — it decides the existing
    /// pending rows in place instead (avoids duplicate request rows).
    async fn insert_approved_connect_requests(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
        decider: &str,
        edge_ids: &[u64],
        default_refs: &[u64; 2],
        message: Option<&str>,
    ) -> ServiceResult<Vec<String>> {
        let mut request_ids = Vec::new();

        // Forward approved snapshot.
        let fwd_request_id = new_request_id();
        self.requests
            .insert(PermissionRequest {
                request_id: fwd_request_id.clone(),
                edge_id: Some(edge_ids[0]),
                env: self.env.clone(),
                from_id: caller.to_string(),
                to_id: to_bot.to_string(),
                request_kind: RequestKind::Connect,
                requested_ref_id: Some(default_refs[0]),
                requested_rules: None,
                message: message.map(|s| s.to_string()),
                status: RequestStatus::Approved,
                decision_reason: None,
                created_by: caller.to_string(),
                decided_by: Some(decider.to_string()),
                // decided_at is DB-managed (CURRENT_TIMESTAMP on an approved insert).
                decided_at: None,
            })
            .await?;
        request_ids.push(fwd_request_id);

        // Reverse approved snapshot (Bot↔Bot only).
        if caller_kind == ActorKind::Bot
            && target_kind == ActorKind::Bot
            && edge_ids.len() == 2
        {
            let rev_request_id = new_request_id();
            self.requests
                .insert(PermissionRequest {
                    request_id: rev_request_id.clone(),
                    edge_id: Some(edge_ids[1]),
                    env: self.env.clone(),
                    from_id: to_bot.to_string(),
                    to_id: caller.to_string(),
                    request_kind: RequestKind::Connect,
                    requested_ref_id: Some(default_refs[1]),
                    requested_rules: None,
                    message: None,
                    status: RequestStatus::Approved,
                    decision_reason: None,
                    created_by: caller.to_string(),
                    decided_by: Some(decider.to_string()),
                    decided_at: None,
                })
                .await?;
            request_ids.push(rev_request_id);
        }

        Ok(request_ids)
    }
}

// ---- AdmissionService (T14) ----------------------------------------------

#[async_trait]
impl EdgePermissionFriendSyncService for DbConnectService {
    async fn sync_add_friendship(&self, a: &str, b: &str) -> ServiceResult<()> {
        let Some((caller, target, caller_kind, target_kind)) = normalize_friend_sync_pair(a, b)? else {
            warn!(left = %a, right = %b, env = %self.env, "skip human-human friendship edge-permission sync");
            return Ok(());
        };
        if self.edge_grants.has_friend_edge(caller, target, &self.env).await {
            return Ok(());
        }
        self.build_connect_edges(caller, target, caller_kind, target_kind)
            .await
            .map(|_| ())
    }

    async fn sync_remove_friendship(&self, a: &str, b: &str) -> ServiceResult<()> {
        let Some((caller, target, _, _)) = normalize_friend_sync_pair(a, b)? else {
            return Ok(());
        };
        <Self as ConnectService>::revoke_friend(self, caller, target)
            .await
            .map(|_| ())
    }
}

/// DB-backed [`AdmissionService`] implementation (spec §4.3 + §4.5 + §6.2).
///
/// Unlike [`DbConnectService`] (which owns its env because `ConnectService`
/// methods take no env param), the `AdmissionService` trait methods carry an
/// `env: &str` argument, so this service holds no env field — it scopes every
/// repo call to the env passed into `check_admission` / `build_authz_context`.
///
/// Two-path same SoR (§4.3): the workbench path (`check_admission`) and the A2A
/// path (`build_authz_context`) both read the same `edge_grants` store, giving a
/// single source of truth for inbound admission and injected runtime authz.
pub struct DbAdmissionService {
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    bot_config: Arc<dyn BotActorConfigRepoPort>,
    profiles: Arc<dyn PermissionProfileRepoPort>,
}

impl DbAdmissionService {
    pub fn new(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        bot_config: Arc<dyn BotActorConfigRepoPort>,
        profiles: Arc<dyn PermissionProfileRepoPort>,
    ) -> Self {
        Self {
            edge_grants,
            bot_config,
            profiles,
        }
    }

    /// Resolve the target bot's default profile as a runtime grant ref,
    /// preferring the edge-grant cache id and enriching `revision`/`digest`
    /// from the profile store (§4.3: "profile grant 解析出 revision/digest").
    async fn default_profile_ref(
        &self,
        bot: &str,
        env: &str,
        source: GrantSource,
    ) -> Option<AuthzGrantRef> {
        // Prefer the edge-grant cache; fall back to the profile store.
        let default_id = match self.edge_grants.get_default_profile_id(bot, env).await {
            Some(id) => id,
            None => self
                .profiles
                .get_active_default(bot, env)
                .await?
                .permission_profile_id,
        };

        // Enrich revision/digest from the profile store when available.
        let (revision, digest) = self
            .profiles
            .get_active_default(bot, env)
            .await
            .map(|p| (Some(p.revision), Some(p.digest)))
            .unwrap_or((None, None));

        Some(AuthzGrantRef {
            kind: GrantKind::PermissionProfile,
            ref_id: default_id,
            revision,
            digest,
            source,
        })
    }

    /// Resolve the default-profile grant ref for `bot`, calling
    /// `ensure_default_profile` first so a freshly-onboarded bot with no
    /// `permission_profiles` row yet still resolves (idempotent, D12 rule 2).
    /// Used by `check_admission` where we expect a resolvable default.
    async fn ensure_default_profile_ref(
        &self,
        bot: &str,
        env: &str,
        source: GrantSource,
    ) -> Option<AuthzGrantRef> {
        let _ = self.profiles.ensure_default_profile(bot, env).await;
        self.default_profile_ref(bot, env, source).await
    }
}

#[async_trait]
impl AdmissionService for DbAdmissionService {
    async fn check_admission(
        &self,
        actor: &str,
        bot: &str,
        originator: &str,
        env: &str,
    ) -> ServiceResult<AdmissionResult> {
        // spec §4.3 step 1. `None` config ⇒ bot not on-boarded in this env.
        // Returned as a deny AdmissionResult (api-contract reason_code
        // `bot_not_found`), NOT a ServiceResult error.
        let cfg = match self.bot_config.get(bot, env).await {
            Some(c) => c,
            None => {
                return Ok(AdmissionResult {
                    allowed: false,
                    grants: vec![],
                    reason_code: AdmissionReason::BotNotFound,
                    public_default: false,
                });
            }
        };

        // status=hidden ⇒ collaboration switch off ⇒ deny (spec §4.3 step 1).
        if cfg.status == "hidden" {
            return Ok(AdmissionResult {
                allowed: false,
                grants: vec![],
                reason_code: AdmissionReason::BotHidden,
                public_default: false,
            });
        }

        // §4.3 step 2: friend edge (any direction, D12 default-profile edge).
        // `originator` is accepted but friend edges carry
        // `originator_policy_type=any` (D7) so they are always active for any
        // originator — no policy matching needed at T14. Left as a hook for
        // `Specific`/`Owner` policies in a later installment.
        let _ = originator;

        if self.edge_grants.is_authorized(actor, bot, env).await {
            if let Some(grant) = self
                .ensure_default_profile_ref(bot, env, GrantSource::EdgeGrant)
                .await
            {
                return Ok(AdmissionResult {
                    allowed: true,
                    grants: vec![grant],
                    reason_code: AdmissionReason::Ok,
                    public_default: false,
                });
            }
            // Friend edge exists but the default profile is unexpectedly gone
            // (shouldn't happen: D12 rule 2 keeps it live while friend edges
            // exist). Treat as deny to fail safe.
            return Ok(AdmissionResult {
                allowed: false,
                grants: vec![],
                reason_code: AdmissionReason::NoEdge,
                public_default: false,
            });
        }

        // §4.3 step 4: no edge, bot is public ⇒ public_default (§6.2).
        if cfg.visibility == "public" {
            if let Some(grant) = self
                .ensure_default_profile_ref(bot, env, GrantSource::PublicDefault)
                .await
            {
                return Ok(AdmissionResult {
                    allowed: true,
                    grants: vec![grant],
                    reason_code: AdmissionReason::PublicDefault,
                    public_default: true,
                });
            }
            // Public bot without a default profile — treat as deny rather than
            // crash. ensure_default_profile should have seeded one above.
            return Ok(AdmissionResult {
                allowed: false,
                grants: vec![],
                reason_code: AdmissionReason::NoEdge,
                public_default: false,
            });
        }

        // §4.3 step 5: protected/private bot, no edge ⇒ deny.
        Ok(AdmissionResult {
            allowed: false,
            grants: vec![],
            reason_code: AdmissionReason::NoEdge,
            public_default: false,
        })
    }

    async fn build_authz_context(
        &self,
        from: &str,
        to: &str,
        originator: &str,
        task_id: &str,
        run_id: &str,
        env: &str,
    ) -> ServiceResult<AuthzContext> {
        // §4.3 A2A path: read the same `edge_grants` SoR as admission.
        let active = self.edge_grants.list_active_grants(from, to, env).await;

        let mut grants: Vec<AuthzGrantRef> = Vec::with_capacity(active.len());
        for g in active {
            // For permission_profile edges we COULD enrich revision/digest by
            // looking up the profile by `grant_ref_id`. `get_active_default`
            // takes `bot_id`, not `profile_id`, and we only need enrichment for
            // default-profile edges — so for a default-profile friend edge we
            // resolve via the profile store keyed by the target bot. Non-default
            // profile edges leave revision/digest None (acceptable at T14).
            let (revision, digest) = if g.grant_kind == GrantKind::PermissionProfile
                && self.is_default_profile_ref(g.grant_ref_id, to, env).await
            {
                self.profiles
                    .get_active_default(to, env)
                    .await
                    .map(|p| (Some(p.revision), Some(p.digest)))
                    .unwrap_or((None, None))
            } else {
                (None, None)
            };
            grants.push(AuthzGrantRef {
                kind: g.grant_kind,
                ref_id: g.grant_ref_id,
                revision,
                digest,
                source: GrantSource::EdgeGrant,
            });
        }

        // No active edge AND target is a public, non-hidden bot ⇒ fall back to
        // public_default so the A2A context admits via the runtime default.
        if grants.is_empty() {
            if let Some(cfg) = self.bot_config.get(to, env).await {
                if cfg.visibility == "public" && cfg.status != "hidden" {
                    if let Some(g) = self
                        .ensure_default_profile_ref(to, env, GrantSource::PublicDefault)
                        .await
                    {
                        grants.push(g);
                    }
                }
            }
        }

        Ok(AuthzContext {
            task_id: task_id.to_string(),
            run_id: run_id.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            env: env.to_string(),
            originator: originator.to_string(),
            context: serde_json::json!({}),
            grants,
            signature: None,
        })
    }
}

// ---- AdmissionService private helpers ------------------------------------

impl DbAdmissionService {
    /// Is `ref_id` the default profile id of `bot`? Used to decide whether to
    /// enrich revision/digest for a permission_profile edge ref.
    async fn is_default_profile_ref(&self, ref_id: u64, bot: &str, env: &str) -> bool {
        if let Some(cached) = self.edge_grants.get_default_profile_id(bot, env).await {
            return cached == ref_id;
        }
        if let Some(p) = self.profiles.get_active_default(bot, env).await {
            return p.permission_profile_id == ref_id;
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_db_api::{DbPlugin, DbStatement, DbValue};
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_edge_permission_store::{
        DbBotActorConfigStore, DbEdgeGrantStore, DbPermissionProfileStore, DbPermissionRequestStore,
    };

    /// One shared LocalSqliteDbPlugin with all four tables, mirroring the
    /// store tests' DDL (edge_grants / permission_profiles /
    /// permission_requests / bcs_bots). Returns the four stores wrapped for
    /// injection into `DbConnectService`.
    async fn assemble() -> (
        Arc<dyn EdgeGrantRepoPort>,
        Arc<dyn PermissionProfileRepoPort>,
        Arc<dyn PermissionRequestRepoPort>,
        Arc<dyn BotActorConfigRepoPort>,
        Arc<LocalSqliteDbPlugin>,
    ) {
        let db = Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));

        db.execute(DbStatement::new(
            "CREATE TABLE edge_grants (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                grant_kind VARCHAR(32) NOT NULL, \
                grant_ref_id BIGINT NOT NULL, \
                rules TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'approved', \
                originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (from_id, to_id, env, grant_ref_id))",
        ))
        .await
        .expect("create edge_grants");

        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                bot_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                name VARCHAR(128) NOT NULL DEFAULT 'default', \
                description VARCHAR(512), \
                rules_template TEXT NOT NULL, \
                revision INTEGER NOT NULL DEFAULT 1, \
                digest VARCHAR(128) NOT NULL, \
                is_default INTEGER NOT NULL DEFAULT 0, \
                status VARCHAR(16) NOT NULL DEFAULT 'active', \
                created_by VARCHAR(128) NOT NULL, \
                updated_by VARCHAR(128), \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create permission_profiles");

        db.execute(DbStatement::new(
            "CREATE TABLE permission_requests (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                request_id VARCHAR(64) NOT NULL, \
                edge_id BIGINT, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                request_kind VARCHAR(32) NOT NULL, \
                requested_ref_id BIGINT, \
                requested_rules TEXT, \
                message TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'pending', \
                decision_reason TEXT, \
                created_by VARCHAR(128) NOT NULL, \
                decided_by VARCHAR(128), \
                decided_at TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create permission_requests");

        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (\
                bot_uuid TEXT NOT NULL, \
                env TEXT NOT NULL, \
                name TEXT NOT NULL DEFAULT '', \
                visibility TEXT NOT NULL DEFAULT 'public', \
                user_visibility TEXT NOT NULL DEFAULT 'protected', \
                friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL', \
                bot_info TEXT DEFAULT NULL, \
                friend_ext TEXT DEFAULT NULL, \
                status TEXT NOT NULL DEFAULT 'online', \
                created_by TEXT, \
                is_deleted INTEGER NOT NULL DEFAULT 0, \
                PRIMARY KEY (bot_uuid, env))",
        ))
        .await
        .expect("create bcs_bots");

        let db_ref = db.clone();
        let edge_grants: Arc<dyn EdgeGrantRepoPort> = Arc::new(DbEdgeGrantStore::sqlite(db.clone()));
        let profiles: Arc<dyn PermissionProfileRepoPort> =
            Arc::new(DbPermissionProfileStore::sqlite(db.clone()));
        let requests: Arc<dyn PermissionRequestRepoPort> =
            Arc::new(DbPermissionRequestStore::sqlite(db.clone()));
        let bot_config: Arc<dyn BotActorConfigRepoPort> =
            Arc::new(DbBotActorConfigStore::sqlite(db.clone()));

        (edge_grants, profiles, requests, bot_config, db_ref)
    }

    fn service(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
    ) -> DbConnectService {
        DbConnectService::new(
            edge_grants.clone(),
            profiles.clone(),
            requests.clone(),
            bot_config.clone(),
            None,
            Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
            "dev".to_string(),
        )
    }

    #[derive(Clone, Default)]
    struct StaticUserDirectoryPlugin {
        departments: Arc<std::collections::HashMap<String, String>>,
    }

    #[async_trait]
    impl UserDirectoryPlugin for StaticUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: None,
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Ok(self.departments.get(staff_no).cloned())
        }
    }

    /// User-directory stub that returns a fixed nick name for any staff_no —
    /// used to verify friend-connect notifications resolve the applicant's name.
    struct FixedNickUserDirectoryPlugin {
        nick: String,
    }

    #[async_trait]
    impl UserDirectoryPlugin for FixedNickUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: Some(self.nick.clone()),
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            _staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Ok(None)
        }
    }

    fn service_with_departments(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
        departments: Arc<dyn UserDirectoryPlugin>,
    ) -> DbConnectService {
        DbConnectService::new(
            edge_grants.clone(),
            profiles.clone(),
            requests.clone(),
            bot_config.clone(),
            Some(departments),
            Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
            "dev".to_string(),
        )
    }

    #[derive(Clone, Default)]
    struct RecordingContextUserDirectoryPlugin {
        department: String,
        contexts: Arc<tokio::sync::Mutex<Vec<UserDirectoryLookupContext>>>,
    }

    #[async_trait]
    impl UserDirectoryPlugin for RecordingContextUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: None,
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            _staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(self.department.clone()))
        }

        async fn lookup_department_by_staff_no_with_context(
            &self,
            _staff_no: &str,
            context: &UserDirectoryLookupContext,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            self.contexts.lock().await.push(context.clone());
            Ok(Some(self.department.clone()))
        }
    }

    #[derive(Clone, Default)]
    struct FailingUserDirectoryPlugin;

    #[async_trait]
    impl UserDirectoryPlugin for FailingUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: None,
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            _staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Err(bcs_user_directory_api::UserDirectoryError::Request("boom".to_string()))
        }

        async fn lookup_department_by_staff_no_with_context(
            &self,
            _staff_no: &str,
            _context: &UserDirectoryLookupContext,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Err(bcs_user_directory_api::UserDirectoryError::Request("boom".to_string()))
        }
    }

    #[derive(Clone, Default)]
    struct RecordingFriendConnectNotificationPort {
        events: Arc<tokio::sync::Mutex<Vec<FriendConnectNotificationCommand>>>,
    }

    #[async_trait]
    impl FriendConnectNotificationPort for RecordingFriendConnectNotificationPort {
        async fn notify(&self, command: FriendConnectNotificationCommand) -> ServiceResult<()> {
            self.events.lock().await.push(command);
            Ok(())
        }
    }

    fn service_with_notification(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
        notification: Arc<dyn FriendConnectNotificationPort>,
    ) -> DbConnectService {
        DbConnectService::new(
            edge_grants.clone(),
            profiles.clone(),
            requests.clone(),
            bot_config.clone(),
            None,
            notification,
            "dev".to_string(),
        )
    }

    async fn seed_bot(
        db: &Arc<LocalSqliteDbPlugin>,
        bot_uuid: &str,
        visibility: &str,
        user_visibility: &str,
        friend_check_in_strategy: &str,
        status: &str,
        created_by: Option<&str>,
    ) {
        seed_bot_with_friend_ext(
            db,
            bot_uuid,
            visibility,
            user_visibility,
            friend_check_in_strategy,
            status,
            created_by,
            serde_json::Map::new(),
        )
        .await;
    }

    async fn seed_bot_with_friend_ext(
        db: &Arc<LocalSqliteDbPlugin>,
        bot_uuid: &str,
        visibility: &str,
        user_visibility: &str,
        friend_check_in_strategy: &str,
        status: &str,
        created_by: Option<&str>,
        friend_ext: serde_json::Map<String, serde_json::Value>,
    ) {
        let friend_ext_json = serde_json::to_string(&friend_ext).expect("friend_ext json");
        let bot_info = serde_json::json!({
            "friend_check_in_strategy": friend_check_in_strategy,
            "friend_ext": friend_ext,
        });
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots \
             (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext, status, created_by) \
             VALUES (?, 'dev', ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::from(bot_uuid),
                DbValue::from(bot_uuid),
                DbValue::from(visibility),
                DbValue::from(user_visibility),
                DbValue::from(friend_check_in_strategy),
                DbValue::from(serde_json::to_string(&bot_info).expect("bot_info json")),
                DbValue::from(friend_ext_json),
                DbValue::from(status),
                match created_by {
                    Some(v) => DbValue::from(v),
                    None => DbValue::Null,
                },
            ],
        ))
        .await
        .expect("seed bot");
    }

    #[tokio::test]
    async fn actor_kind_helper() {
        assert_eq!(actor_kind_of("human_88001"), ActorKind::Human);
        assert_eq!(actor_kind_of("20260421_x:85020"), ActorKind::Bot);
        // BCS-native bot uuids (no `:`) now fall back to Bot (A1 fix) instead
        // of None — the direction gate in create_connect rejects invalid dirs,
        // not actor_kind_of.
        assert_eq!(actor_kind_of("plainbot"), ActorKind::Bot);
    }

    #[tokio::test]
    async fn edge_permission_friend_sync_adds_and_removes_bot_friend_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:syncA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:syncB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncA", "x:syncB")
            .await
            .expect("sync add");

        assert!(eg.has_friend_edge("x:syncA", "x:syncB", "dev").await);
        assert_eq!(eg.list_active_grants("x:syncA", "x:syncB", "dev").await.len(), 1);
        assert_eq!(eg.list_active_grants("x:syncB", "x:syncA", "dev").await.len(), 1);

        svc.sync_remove_friendship("x:syncA", "x:syncB")
            .await
            .expect("sync remove");

        assert!(!eg.has_friend_edge("x:syncA", "x:syncB", "dev").await);
    }

    #[tokio::test]
    async fn edge_permission_friend_sync_normalizes_human_bot_direction() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:syncBot", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncBot", "human_1")
            .await
            .expect("sync add");

        assert!(eg.has_friend_edge("human_1", "x:syncBot", "dev").await);
        assert_eq!(eg.list_active_grants("human_1", "x:syncBot", "dev").await.len(), 1);
        assert!(eg.list_active_grants("x:syncBot", "human_1", "dev").await.is_empty());
    }

    #[tokio::test]
    async fn cannot_add_self() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("bot_a:1", "bot_a:1", None, None)
            .await
            .expect_err("self-add rejected");
        assert!(matches!(err, ServiceError::CannotAddSelf), "got {err:?}");
    }

    #[tokio::test]
    async fn human_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "human_2", None, None)
            .await
            .expect_err("human→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn request_lookup_miss_paths_and_env_scope() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        assert_eq!(svc.env(), "dev");

        let err = svc
            .approve("missing-request-approve", "decider_1")
            .await
            .expect_err("approve missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .reject("missing-request-reject", "decider_2", None)
            .await
            .expect_err("reject missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .cancel("missing-request-cancel")
            .await
            .expect_err("cancel missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .get_request("missing-request-get")
            .await
            .expect_err("get_request missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn bot_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("x:1", "human_2", None, None)
            .await
            .expect_err("bot→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn bot_not_found() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:missing", None, None)
            .await
            .expect_err("missing bot → BotNotFound");
        assert!(
            matches!(err, ServiceError::BotNotFound(_)),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn hidden_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:hidden", "public", "protected", "OPEN", "hidden", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:hidden", None, None)
            .await
            .expect_err("hidden → BotHidden");
        assert!(matches!(err, ServiceError::BotHidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn private_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // visibility=private blocks bot→bot collaboration (a bot adding a
        // private-visibility bot). Human callers are gated by `user_visibility`,
        // not `visibility` — see user_visibility_private_for_human_caller and
        // human_adds_visibility_private_user_visibility_public_bot_succeeds.
        seed_bot(&db, "x:priv", "private", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("caller_bot:1", "x:priv", None, None)
            .await
            .expect_err("bot→private visibility → PrivateBotCannotCollaborate");
        assert!(
            matches!(err, ServiceError::PrivateBotCannotCollaborate),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn user_visibility_private_for_human_caller() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nha", "protected", "private", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:nha", None, None)
            .await
            .expect_err("user_visibility=private → Forbidden for human caller");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn human_adds_visibility_private_user_visibility_public_bot_succeeds() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // visibility=private no longer blocks a human caller — humans are gated by
        // `user_visibility=public`, so this bot is human-addable (mirrors the
        // /bots/search viewer-kind selection).
        seed_bot(&db, "x:privuvis", "private", "public", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:privuvis", None, None)
            .await
            .expect("human→(visibility=private, user_visibility=public) is human-addable");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:privuvis", "dev").await);
    }

    #[tokio::test]
    async fn human_protected_view_scope_user_friend_deps_allows_matching_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_user_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:user_scope_hit",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:user_scope_hit", None, None)
            .await
            .expect("protected user scope hit should auto-approve when OPEN");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
    }

    #[tokio::test]
    async fn human_protected_view_scope_user_friend_deps_blocks_out_of_scope_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_user_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:user_scope_miss",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-其他事业群-销售部".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let err = svc
            .create_connect("human_1", "x:user_scope_miss", None, None)
            .await
            .expect_err("out-of-scope human applicant should be rejected");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn bot_protected_view_scope_agent_friend_deps_allows_matching_owner_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "x:applicant",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_agent_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:target_scope_hit",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "owner_1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("x:applicant", "x:target_scope_hit", None, None)
            .await
            .expect("protected agent scope hit should auto-approve when OPEN");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 2);
        assert_eq!(res.request_ids.len(), 2);
    }

    #[tokio::test]
    async fn bot_protected_view_scope_agent_friend_deps_blocks_out_of_scope_owner_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "x:applicant",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_agent_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:target_scope_miss",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "owner_1".to_string(),
                "蚂蚁集团-其他事业群-销售部".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let err = svc
            .create_connect("x:applicant", "x:target_scope_miss", None, None)
            .await
            .expect_err("out-of-scope bot owner should be rejected");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }


    #[tokio::test]
    async fn public_auto_bot_returns_approved_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:pub", None, None)
            .await
            .expect("public+auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:pub", "dev").await);
        let active = eg.list_active_grants("human_1", "x:pub", "dev").await;
        assert_eq!(active.len(), 1, "public auto should create a durable edge");
        let r = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.edge_id, Some(res.edge_ids[0]));
    }


    #[tokio::test]
    async fn department_lookup_logs_success_result_path() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "F4858".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);

        assert_eq!(
            svc.resolve_user_department_code("human_1", None).await.as_deref(),
            Some("F4858")
        );
    }

    #[tokio::test]
    async fn department_lookup_logs_failure_result_path() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let svc = service_with_departments(&eg, &pp, &rq, &bc, Arc::new(FailingUserDirectoryPlugin));

        assert_eq!(svc.resolve_user_department_code("human_1", None).await, None);
    }

    #[tokio::test]
    async fn department_lookup_logs_missing_directory_path() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        assert_eq!(svc.resolve_user_department_code("human_1", None).await, None);
    }

    #[tokio::test]
    async fn department_lookup_logs_skip_paths() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::new()),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);

        assert_eq!(svc.resolve_user_department_code("human_", None).await, None);
        assert_eq!(svc.resolve_user_department_code("x:missing", None).await, None);

        seed_bot(
            &db,
            "x:no_owner",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            None,
        )
        .await;
        assert_eq!(svc.resolve_user_department_code("x:no_owner", None).await, None);
    }

    #[tokio::test]
    async fn dept_free_allowlist_stays_pending_with_noop_department_port() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_noop",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:dept_noop", None, None)
            .await
            .expect("noop department port should not auto-approve");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert!(!res.auto_accepted);
        assert_eq!(res.request_ids.len(), 1);
        assert!(res.edge_ids.is_empty());
        let req = rq.get(&res.request_ids[0], "dev").await.expect("pending req");
        assert_eq!(req.status, RequestStatus::Pending);
    }

    #[tokio::test]
    async fn dept_free_allowlist_auto_approves_when_human_department_matches() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:dept", None, None)
            .await
            .expect("dept_free ancestor hit → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
        let req = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(req.status, RequestStatus::Approved);
    }

    #[tokio::test]
    async fn dept_free_lookup_receives_forwarded_auth_context() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String("F4858".to_string())]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_context",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let contexts = Arc::new(tokio::sync::Mutex::new(Vec::new()));
        let departments = Arc::new(RecordingContextUserDirectoryPlugin {
            department: "F4858".to_string(),
            contexts: contexts.clone(),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect(
                "human_1",
                "x:dept_context",
                None,
                Some(RequestAuthHeaders {
                    authorization: Some("Bearer caller-token".to_string()),
                    cookie: None,
                    forwarded_headers: vec![(
                        "authorization".to_string(),
                        "Bearer caller-token".to_string(),
                    )],
                }),
            )
            .await
            .expect("dept_free exact match from department port → Approved");

        assert_eq!(res.status, ConnectStatus::Approved);
        assert_eq!(
            contexts.lock().await[0].forwarded_headers,
            vec![("authorization".to_string(), "Bearer caller-token".to_string())]
        );
    }

    #[tokio::test]
    async fn dept_free_allowlist_auto_approves_when_department_port_matches() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_from_port",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);

        let res = svc
            .create_connect("human_1", "x:dept_from_port", None, None)
            .await
            .expect("dept_free exact match from department port → Approved");

        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
    }

    #[tokio::test]
    async fn dept_free_bot_applicant_falls_back_to_owner_department_port() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "x:applicant",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:target",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "owner_1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);

        let res = svc
            .create_connect("x:applicant", "x:target", None, None)
            .await
            .expect("bot applicant owner ancestor dept from department port → Approved");

        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
    }

    #[tokio::test]
    async fn dept_free_allowlist_miss_keeps_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_miss",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-其他事业群-销售部".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:dept_miss", None, None)
            .await
            .expect("dept_free miss → Pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert!(res.edge_ids.is_empty());
        assert_eq!(res.request_ids.len(), 1);
        let req = rq.get(&res.request_ids[0], "dev").await.expect("pending req");
        assert_eq!(req.status, RequestStatus::Pending);
    }

    #[tokio::test]
    async fn pending_friend_request_emits_notification_to_target_owner() {

        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pending_notify", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let request_auth = RequestAuthHeaders { authorization: Some("Bearer user-token".to_string()), cookie: Some("session=abc".to_string()), forwarded_headers: Vec::new() };
        let res = svc
            .create_connect("human_1", "x:pending_notify", Some("hi".into()), Some(request_auth.clone()))
            .await
            .expect("manual pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        let event = &events[0];
        assert_eq!(event.kind, FriendConnectNotificationKind::ApprovalRequested);
        assert_eq!(event.env, "dev");
        assert_eq!(event.request_ids, res.request_ids);
        assert_eq!(event.applicant_actor_id, "human_1");
        assert_eq!(event.target_bot_id, "x:pending_notify");
        assert_eq!(event.recipient_user_ids, vec!["85020".to_string()]);
        assert_eq!(event.message.as_deref(), Some("hi"));
        assert_eq!(event.request_auth, Some(request_auth));
    }

    #[tokio::test]
    async fn friend_connect_notification_resolves_applicant_and_target_names() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // Target bot: seed (default name = bot_uuid) then set a human-friendly name.
        seed_bot(&db, "x:expert", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET name = ? WHERE bot_uuid = ?",
            vec![DbValue::from("本地代码专家"), DbValue::from("x:expert")],
        ))
        .await
        .expect("set target bot name");
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let user_directory: Arc<dyn UserDirectoryPlugin> =
            Arc::new(FixedNickUserDirectoryPlugin { nick: "李四".to_string() });
        let svc = DbConnectService::new(
            eg.clone(),
            pp.clone(),
            rq.clone(),
            bc.clone(),
            Some(user_directory),
            Arc::new(recorder),
            "dev".to_string(),
        );
        // Human applicant (nick 李四) → bot "本地代码专家" (owner 85020); APPROVAL → pending.
        svc.create_connect("human_12345", "x:expert", None, None)
            .await
            .expect("manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, FriendConnectNotificationKind::ApprovalRequested);
        // applicant nick resolved via the user directory; target name via bot config.
        assert_eq!(events[0].applicant_name.as_deref(), Some("李四"));
        assert_eq!(events[0].target_bot_name.as_deref(), Some("本地代码专家"));
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }

    #[tokio::test]
    async fn friend_connect_notification_falls_back_when_user_directory_absent() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // No UPDATE → name stays the seed default (= bot_uuid).
        seed_bot(&db, "x:noexp", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        // service_with_notification wires user_directory = None.
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        svc.create_connect("human_12345", "x:noexp", None, None)
            .await
            .expect("manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(
            events[0].applicant_name,
            None,
            "no user directory → applicant name unresolved (falls back to id)"
        );
        // Target name still resolves from the bot's config (seed default = bot_uuid).
        assert_eq!(events[0].target_bot_name.as_deref(), Some("x:noexp"));
    }

    #[tokio::test]
    async fn friend_connect_notification_uses_bot_applicant_owner_as_applicant_user_id() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // Applicant bot owned by 152819; target bot owned by 85020.
        seed_bot(&db, "x:applicant", "protected", "public", "APPROVAL", "online", Some("152819")).await;
        seed_bot(&db, "x:target", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        // Bot→Bot pending connect (APPROVAL strategy → needs approval → ApprovalRequested).
        svc.create_connect("x:applicant", "x:target", None, None)
            .await
            .expect("bot→bot manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, FriendConnectNotificationKind::ApprovalRequested);
        // applicant_user_id = the applicant bot's OWNER (user id), not the bot id.
        assert_eq!(events[0].applicant_user_id.as_deref(), Some("152819"));
        assert_eq!(events[0].applicant_actor_id, "x:applicant");
        assert_eq!(events[0].target_bot_id, "x:target");
        // Approver/recipient = the target bot's owner.
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }

    #[tokio::test]
    async fn pending_notification_is_not_duplicated_on_idempotent_create() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pending_idem", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let first = svc
            .create_connect("human_1", "x:pending_idem", None, None)
            .await
            .expect("first pending");
        let second = svc
            .create_connect("human_1", "x:pending_idem", None, None)
            .await
            .expect("idempotent pending");
        assert_eq!(first.request_ids, second.request_ids);
        assert_eq!(events.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn human_to_bot_manual_returns_pending_one_request_no_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:man", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:man", Some("hi".into()), None)
            .await
            .expect("manual → Pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert_eq!(res.request_ids.len(), 1, "exactly one pending request");
        assert!(res.edge_ids.is_empty());
        // default profile should NOT have been seeded (no edge built).
        assert!(pp.get_active_default("x:man", "dev").await.is_none());
        let id = res.request_ids[0].clone();
        let r = rq.get(&id, "dev").await.expect("pending request exists");
        assert_eq!(r.status, RequestStatus::Pending);
        assert_eq!(r.from_id, "human_1");
        assert_eq!(r.to_id, "x:man");
        assert!(r.edge_id.is_none());
    }

    #[tokio::test]
    async fn human_to_bot_auto_approves_one_edge_one_request() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:auto1", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:auto1", None, None)
            .await
            .expect("auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1, "Human→Bot: exactly 1 edge");
        assert_eq!(res.request_ids.len(), 1, "Human→Bot: 1 request");

        // The edge caller→to_bot references to_bot's default profile.
        let active = eg.list_active_grants("human_1", "x:auto1", "dev").await;
        assert_eq!(active.len(), 1);
        let default_id = eg.get_default_profile_id("x:auto1", "dev").await;
        assert_eq!(active[0].grant_ref_id, default_id.unwrap());
        assert!(eg.has_friend_edge("human_1", "x:auto1", "dev").await);

        // The request is approved, decided_by=auto, backfilled with edge_id.
        let r = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.decided_by.as_deref(), Some("auto"));
        assert_eq!(r.edge_id, Some(res.edge_ids[0]));
    }

    #[tokio::test]
    async fn bot_to_bot_auto_approves_two_edges_two_requests() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:botA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:botB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("x:botA", "x:botB", None, None)
            .await
            .expect("Bot↔Bot auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert_eq!(res.edge_ids.len(), 2, "Bot↔Bot: 2 edges");
        assert_eq!(res.request_ids.len(), 2, "Bot↔Bot: 2 requests");

        let fwd = eg.list_active_grants("x:botA", "x:botB", "dev").await;
        let rev = eg.list_active_grants("x:botB", "x:botA", "dev").await;
        assert_eq!(fwd.len(), 1);
        assert_eq!(rev.len(), 1);
        assert_eq!(
            fwd[0].grant_ref_id,
            eg.get_default_profile_id("x:botB", "dev").await.unwrap()
        );
        assert_eq!(
            rev[0].grant_ref_id,
            eg.get_default_profile_id("x:botA", "dev").await.unwrap()
        );
        assert!(eg.has_friend_edge("x:botA", "x:botB", "dev").await);
    }

    #[tokio::test]
    async fn already_friends_is_idempotent_approved() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:idem", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:idem", None, None)
            .await
            .expect("first connect");
        assert_eq!(first.status, ConnectStatus::Approved);
        let second = svc
            .create_connect("human_1", "x:idem", None, None)
            .await
            .expect("second connect idempotent");
        assert_eq!(second.status, ConnectStatus::Approved);
        assert!(second.edge_ids.is_empty(), "no new edge on idempotent");
        assert!(second.request_ids.is_empty(), "no new request on idempotent");
        // Exactly one edge overall.
        let active = eg.list_active_grants("human_1", "x:idem", "dev").await;
        assert_eq!(active.len(), 1);
    }

    #[tokio::test]
    async fn pending_connect_is_idempotent_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pend", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:pend", None, None)
            .await
            .expect("first manual");
        assert_eq!(first.status, ConnectStatus::Pending);
        let second = svc
            .create_connect("human_1", "x:pend", None, None)
            .await
            .expect("second idempotent");
        assert_eq!(second.status, ConnectStatus::Pending);
        // Returns the SAME pending request id (no duplicate insert).
        assert_eq!(first.request_ids, second.request_ids);
    }

    #[tokio::test]
    async fn approve_pending_human_to_bot_builds_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:appr", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:appr", None, None)
            .await
            .expect("manual pending");
        assert_eq!(pending.status, ConnectStatus::Pending);
        let rid = pending.request_ids[0].clone();

        let edge_ids = svc.approve(&rid, "85020").await.expect("approve ok");
        assert_eq!(edge_ids.len(), 1, "Human→Bot approve: 1 edge");
        // Original pending request is now approved + edge_id backfilled.
        let r = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.edge_id, Some(edge_ids[0]));
        assert!(eg.has_friend_edge("human_1", "x:appr", "dev").await);
    }

    #[tokio::test]
    async fn approve_does_not_duplicate_request_rows() {
        // The approve path must decide the existing pending row in place —
        // NOT insert a second approved snapshot row (only the create_connect
        // auto path inserts snapshots). Total connect request rows for this
        // Human→Bot connect must remain 1 after approve.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nodupe", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:nodupe", None, None)
            .await
            .expect("manual pending");
        let rid = pending.request_ids[0].clone();

        svc.approve(&rid, "85020").await.expect("approve ok");

        // The bot's inbox (to_id=x:nodupe) should contain exactly 1 request
        // for this connect (the original, now approved) — not 2.
        let inbox = rq.list_inbox("x:nodupe", "dev", None).await;
        let ours: Vec<&PermissionRequest> = inbox
            .iter()
            .filter(|r| r.from_id == "human_1")
            .collect();
        assert_eq!(ours.len(), 1, "approve must not duplicate the request row");
        assert_eq!(ours[0].status, RequestStatus::Approved);
    }

    #[tokio::test]
    async fn approve_bot_to_bot_approves_both_and_builds_two_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:bbA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:bbB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:bbA", "x:bbB", None, None)
            .await
            .expect("manual pending Bot↔Bot");
        assert_eq!(pending.request_ids.len(), 2);
        let fwd_id = pending.request_ids[0].clone();

        let edge_ids = svc.approve(&fwd_id, "owner").await.expect("approve ok");
        assert_eq!(edge_ids.len(), 2, "Bot↔Bot approve: 2 edges");

        // BOTH pending requests are now approved (single accept, §4.1).
        let fwd = rq.get(&fwd_id, "dev").await.expect("fwd present");
        let rev = rq
            .get(&pending.request_ids[1], "dev")
            .await
            .expect("rev present");
        assert_eq!(fwd.status, RequestStatus::Approved);
        assert_eq!(rev.status, RequestStatus::Approved);
        assert!(eg.has_friend_edge("x:bbA", "x:bbB", "dev").await);
    }

    #[tokio::test]
    async fn reject_does_not_build_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rej", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:rej", None, None)
            .await
            .expect("manual pending");
        let rid = pending.request_ids[0].clone();
        svc.reject(&rid, "85020", Some("no thanks".into()))
            .await
            .expect("reject ok");
        let r = rq.get(&rid, "dev").await.expect("request present");
        assert_eq!(r.status, RequestStatus::Rejected);
        assert!(r.edge_id.is_none());
        let active = eg.list_active_grants("human_1", "x:rej", "dev").await;
        assert!(active.is_empty(), "no edge built on reject");
    }

    #[tokio::test]
    async fn reject_bot_to_bot_rejects_both() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rbA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:rbB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:rbA", "x:rbB", None, None)
            .await
            .expect("pending");
        svc.reject(&pending.request_ids[0], "owner", None)
            .await
            .expect("reject ok");
        let fwd = rq.get(&pending.request_ids[0], "dev").await.expect("fwd");
        let rev = rq.get(&pending.request_ids[1], "dev").await.expect("rev");
        assert_eq!(fwd.status, RequestStatus::Rejected);
        assert_eq!(rev.status, RequestStatus::Rejected);
    }

    #[tokio::test]
    async fn cancel_only_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:canc", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:canc", None, None)
            .await
            .expect("pending");
        let rid = pending.request_ids[0].clone();
        svc.cancel(&rid).await.expect("cancel ok");
        let r = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Cancelled);
        // cancelling an already-cancelled request is idempotent (B4e): Ok, not
        // an error. Spec says "已 rejected/cancelled 幂等".
        svc.cancel(&rid).await.expect("idempotent cancel ok");
        let r2 = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r2.status, RequestStatus::Cancelled);
    }

    #[tokio::test]
    async fn revoke_friend_human_to_bot_one_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:unf", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let created = svc
            .create_connect("human_1", "x:unf", None, None)
            .await
            .expect("auto connect");
        assert_eq!(created.edge_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:unf", "dev").await);

        let n = svc.revoke_friend("human_1", "x:unf").await.expect("revoke ok");
        assert_eq!(n.len(), 1, "Human→Bot: revoked exactly 1 friend edge");
        assert!(!eg.has_friend_edge("human_1", "x:unf", "dev").await);
    }

    #[tokio::test]
    async fn revoke_friend_bot_to_bot_two_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:uA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:uB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("x:uA", "x:uB", None, None).await.expect("connect");
        assert!(eg.has_friend_edge("x:uA", "x:uB", "dev").await);

        let n = svc.revoke_friend("x:uA", "x:uB").await.expect("revoke ok");
        assert_eq!(n.len(), 2, "Bot↔Bot: revoked both friend edges");
        assert!(!eg.has_friend_edge("x:uA", "x:uB", "dev").await);
    }

    #[tokio::test]
    async fn revoke_friend_leaves_non_default_edges() {
        // A non-default profile edge (grant_ref_id != default) must survive
        // revoke_friend (it is not a friend edge per D12).
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:keep", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:keep", None, None).await.expect("connect");
        // Manually insert a writer-profile edge with a different ref id.
        eg.insert_grant(EdgeGrant {
            edge_id: 4001,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:keep".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: 4002, // NOT the default
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert writer edge");

        let n = svc.revoke_friend("human_1", "x:keep").await.expect("revoke");
        assert_eq!(n.len(), 1, "only the friend (default) edge revoked");
        let active = eg.list_active_grants("human_1", "x:keep", "dev").await;
        assert_eq!(active.len(), 1, "writer edge survives");
        assert_eq!(active[0].grant_ref_id, 4002);
    }

    #[tokio::test]
    async fn list_friends_after_connect() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lf1", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:lf2", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:lf1", None, None).await.expect("c1");
        svc.create_connect("human_1", "x:lf2", None, None).await.expect("c2");

        let friends = svc.list_friends("human_1").await.expect("list ok");
        let ids: Vec<String> = friends.iter().map(|f| f.actor_id.clone()).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        assert_eq!(sorted, vec!["x:lf1".to_string(), "x:lf2".to_string()]);
        // Entries carry the right kind and no enrichment (T13 leaves it None).
        for f in &friends {
            assert_eq!(f.kind, ActorKind::Bot);
            assert!(f.name.is_none());
        }
    }

    #[tokio::test]
    async fn list_requests_received_returns_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lr", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:lr", None, None)
            .await
            .expect("pending");
        // The bot's inbox should list the pending request received.
        let page = svc
            .list_requests("x:lr", RequestDirection::Received, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 1);
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].request_id, pending.request_ids[0]);
        // Status filter works.
        let page = svc
            .list_requests(
                "x:lr",
                RequestDirection::Received,
                Some(RequestStatus::Approved),
                1,
                20,
            )
            .await
            .expect("list ok");
        assert_eq!(page.total, 0, "no approved requests in inbox");
        // Sent direction is now backed by list_sent (B4d): the human caller's
        // outbox contains the pending request they just sent.
        let page = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 1, "Sent direction backed by list_sent");
        assert_eq!(page.items[0].request_id, pending.request_ids[0]);
        assert_eq!(page.items[0].from_id, "human_1");
        assert_eq!(page.items[0].to_id, "x:lr");
    }

    #[tokio::test]
    async fn list_requests_pagination() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pg", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        // Create 3 separate human callers connecting to the same bot.
        for h in ["human_a", "human_b", "human_c"] {
            svc.create_connect(h, "x:pg", None, None).await.expect("pending");
        }
        let page1 = svc
            .list_requests("x:pg", RequestDirection::Received, None, 1, 2)
            .await
            .expect("ok");
        assert_eq!(page1.total, 3);
        assert_eq!(page1.items.len(), 2, "page_size=2");
        let page2 = svc
            .list_requests("x:pg", RequestDirection::Received, None, 2, 2)
            .await
            .expect("ok");
        assert_eq!(page2.items.len(), 1, "remainder on page 2");
    }

    // ---- AdmissionService (T14) -------------------------------------------

    /// Build a `DbAdmissionService` from the assembled stores.
    fn admission_service(
        eg: &Arc<dyn EdgeGrantRepoPort>,
        bc: &Arc<dyn BotActorConfigRepoPort>,
        pp: &Arc<dyn PermissionProfileRepoPort>,
    ) -> DbAdmissionService {
        DbAdmissionService::new(eg.clone(), bc.clone(), pp.clone())
    }

    #[tokio::test]
    async fn admission_bot_not_found() {
        let (eg, pp, _rq, bc, _db) = assemble().await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:missing", "originator", "dev")
            .await
            .expect("bot-not-found deny result");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::BotNotFound);
        assert!(!r.public_default);
    }

    #[tokio::test]
    async fn admission_bot_hidden() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:hid", "public", "protected", "OPEN", "hidden", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:hid", "originator", "dev")
            .await
            .expect("hidden deny result");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::BotHidden);
        assert!(!r.public_default);
    }

    #[tokio::test]
    async fn admission_friend_edge_allowed() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:fr", "protected", "protected", "OPEN", "online", Some("85020")).await;
        // Seed a friend edge by going through ConnectService (auto path),
        // which also ensures the target default profile and builds the edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:fr", None, None)
            .await
            .expect("connect");
        assert!(eg.has_friend_edge("human_1", "x:fr", "dev").await);

        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:fr", "originator", "dev")
            .await
            .expect("friend-edge allow");
        assert!(r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::Ok);
        assert!(!r.public_default, "friend-edge path is not public_default");
        assert_eq!(r.grants.len(), 1);
        assert_eq!(r.grants[0].source, GrantSource::EdgeGrant);
        assert_eq!(r.grants[0].kind, GrantKind::PermissionProfile);
        // revision/digest enriched from the profile store.
        assert!(r.grants[0].revision.is_some(), "revision enriched");
        assert!(r.grants[0].digest.is_some(), "digest enriched");
        // ref_id matches the cached default profile id.
        let default_id = eg.get_default_profile_id("x:fr", "dev").await.unwrap();
        assert_eq!(r.grants[0].ref_id, default_id);
    }

    #[tokio::test]
    async fn admission_public_default() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:pub", "originator", "dev")
            .await
            .expect("public_default allow");
        assert!(r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::PublicDefault);
        assert!(r.public_default);
        assert_eq!(r.grants.len(), 1);
        assert_eq!(r.grants[0].source, GrantSource::PublicDefault);
        assert!(r.grants[0].revision.is_some());
        assert!(r.grants[0].digest.is_some());
    }

    #[tokio::test]
    async fn admission_no_edge_protected_bot() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:prot", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:prot", "originator", "dev")
            .await
            .expect("no-edge deny");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::NoEdge);
        assert!(!r.public_default);
    }

    #[tokio::test]
    async fn build_authz_context_with_active_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:az", "protected", "protected", "OPEN", "online", Some("85020")).await;
        // Seed an approved friend edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:az", None, None)
            .await
            .expect("connect");

        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:az", "o", "task_1", "run_1", "dev")
            .await
            .expect("authz ctx");
        assert_eq!(ctx.from_id, "human_1");
        assert_eq!(ctx.to_id, "x:az");
        assert_eq!(ctx.task_id, "task_1");
        assert_eq!(ctx.run_id, "run_1");
        assert_eq!(ctx.env, "dev");
        assert_eq!(ctx.originator, "o");
        assert!(ctx.signature.is_none());
        assert!(ctx.grants.len() >= 1, "active edge present in grants");
        assert_eq!(ctx.grants[0].source, GrantSource::EdgeGrant);
        assert_eq!(ctx.grants[0].kind, GrantKind::PermissionProfile);
    }

    #[tokio::test]
    async fn build_authz_context_empty_for_protected_no_edge() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:prot2", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:prot2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert!(
            ctx.grants.is_empty(),
            "protected bot with no edge: no grants, no public_default fallback"
        );
    }

    #[tokio::test]
    async fn build_authz_context_public_default_fallback() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub2", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:pub2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert_eq!(ctx.grants.len(), 1, "public bot: public_default grant injected");
        assert_eq!(ctx.grants[0].source, GrantSource::PublicDefault);
    }

    // ---- B4b: is_authorized distinguishes Rules-edge admission from friend ----

    #[tokio::test]
    async fn is_authorized_true_for_rules_edge_while_has_friend_edge_false() {
        // A GrantKind::Rules edge (NOT a default-profile edge) must admit via
        // `is_authorized` but NOT via `has_friend_edge`. This is the B4b
        // conformance check: admission uses is_authorized (edge superset),
        // friendship uses has_friend_edge (default-profile only).
        let (eg, pp, _rq, _bc, db) = assemble().await;
        seed_bot(&db, "x:rules", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        // Ensure the target has a default profile so has_friend_edge can resolve,
        // then insert a Rules edge (grant_kind=Rules, arbitrary ref) from→to.
        pp.ensure_default_profile("x:rules", "dev").await.expect("ensure default");
        eg.insert_grant(EdgeGrant {
            edge_id: 5001,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:rules".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: 5003,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert rules edge");

        // is_authorized ⇒ true (any active edge from→to).
        assert!(
            eg.is_authorized("human_1", "x:rules", "dev").await,
            "Rules edge must authorize via is_authorized"
        );
        // has_friend_edge ⇒ false (Rules edge is not a default-profile friend edge).
        assert!(
            !eg.has_friend_edge("human_1", "x:rules", "dev").await,
            "Rules edge is not a friend edge (D12 default-profile only)"
        );
        // list_active_grants surfaces the Rules edge.
        let active = eg.list_active_grants("human_1", "x:rules", "dev").await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].grant_kind, GrantKind::Rules);
    }

    #[tokio::test]
    async fn admission_admits_via_rules_edge_without_friend_edge() {
        // Admission must admit via a Rules edge (is_authorized superset), even
        // when no friend (default-profile) edge exists. This is the behavioral
        // proof that check_admission uses is_authorized, not has_friend_edge.
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:radm", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        pp.ensure_default_profile("x:radm", "dev").await.expect("ensure default");
        eg.insert_grant(EdgeGrant {
            edge_id: 5002,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:radm".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: 5004,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert rules edge");

        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:radm", "originator", "dev")
            .await
            .expect("rules-edge admission");
        assert!(r.allowed, "Rules edge must admit via is_authorized");
        assert_eq!(r.reason_code, AdmissionReason::Ok);
        assert!(
            !r.public_default,
            "Rules-edge admission is not the public_default path"
        );
    }

    // ---- B4d: Sent + All directions of list_requests ----

    #[tokio::test]
    async fn list_requests_sent_and_all_with_status_filter() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:sa", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:sa", Some("hi".into()), None)
            .await
            .expect("pending");
        let rid = pending.request_ids[0].clone();

        // Sent: the human caller's outbox has the pending request.
        let sent = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("sent ok");
        assert_eq!(sent.total, 1);
        assert_eq!(sent.items[0].request_id, rid);
        assert_eq!(sent.items[0].from_id, "human_1");

        // Sent + Approved filter ⇒ empty (still pending).
        let sent_approved = svc
            .list_requests(
                "human_1",
                RequestDirection::Sent,
                Some(RequestStatus::Approved),
                1,
                20,
            )
            .await
            .expect("sent approved ok");
        assert_eq!(sent_approved.total, 0);

        // All from the human caller's view: inbox (none for human_1) ∪ sent
        // (1) ⇒ total 1.
        let all = svc
            .list_requests("human_1", RequestDirection::All, None, 1, 20)
            .await
            .expect("all ok");
        assert_eq!(all.total, 1);
        assert_eq!(all.items[0].request_id, rid);

        // Approve, then All from the bot's view: inbox (1 approved) ∪ sent
        // (the bot sent nothing) ⇒ total 1, status approved.
        svc.approve(&rid, "85020").await.expect("approve");
        let all_bot = svc
            .list_requests("x:sa", RequestDirection::All, None, 1, 20)
            .await
            .expect("all bot ok");
        assert_eq!(all_bot.total, 1);
        assert_eq!(all_bot.items[0].status, RequestStatus::Approved);
    }

    #[tokio::test]
    async fn list_requests_all_dedupes_and_appplies_pagination() {
        // All = inbox ∪ sent, deduped by request_id. For a Bot↔Bot connect,
        // each side's All view sees both rows (one from_id, one to_id) — no
        // dedup collapse (distinct request_ids), exercising the union + sort.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:btA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:btB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:btA", "x:btB", None, None)
            .await
            .expect("pending bot↔bot");
        assert_eq!(pending.request_ids.len(), 2);

        // btA's All view: inbox (btB→btA) ∪ sent (btA→btB) ⇒ 2 rows.
        let all = svc
            .list_requests("x:btA", RequestDirection::All, None, 1, 20)
            .await
            .expect("all ok");
        assert_eq!(all.total, 2);
        assert_eq!(all.items.len(), 2);
        // Pagination: page_size=1 returns 1 item, total stays 2.
        let page1 = svc
            .list_requests("x:btA", RequestDirection::All, None, 1, 1)
            .await
            .expect("page1 ok");
        assert_eq!(page1.total, 2);
        assert_eq!(page1.items.len(), 1);
        let page2 = svc
            .list_requests("x:btA", RequestDirection::All, None, 2, 1)
            .await
            .expect("page2 ok");
        assert_eq!(page2.items.len(), 1);
        // The two pages return distinct request_ids (union order is stable
        // within a run: gmt_modified DESC).
        assert_ne!(page1.items[0].request_id, page2.items[0].request_id);
    }
}