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

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
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
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};
use uuid::Uuid;

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
    env: String,
}

impl DbConnectService {
    pub fn new(
        edge_grants: Arc<dyn EdgeGrantRepoPort>,
        profiles: Arc<dyn PermissionProfileRepoPort>,
        requests: Arc<dyn PermissionRequestRepoPort>,
        bot_config: Arc<dyn BotActorConfigRepoPort>,
        env: String,
    ) -> Self {
        Self {
            edge_grants,
            profiles,
            requests,
            bot_config,
            env,
        }
    }

    /// The env this service is scoped to.
    pub fn env(&self) -> &str {
        &self.env
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

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Fresh opaque id for a new edge grant. UUID v4 — collision-safe, unlike a
/// formatted `from_to` id which collides on re-create after revoke.
fn new_edge_id() -> String {
    format!("eg_{}", Uuid::new_v4().simple())
}

/// Fresh opaque id for a new permission request.
fn new_request_id() -> String {
    format!("req_{}", Uuid::new_v4().simple())
}

#[async_trait]
impl ConnectService for DbConnectService {
    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
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

        // 4. Visibility / status gates (§4.2).
        if cfg.status == "hidden" {
            return Err(ServiceError::BotHidden(to_bot.to_string()));
        }
        if cfg.visibility == "private" {
            // Private bots never initiate collaboration (AC-33).
            return Err(ServiceError::PrivateBotCannotCollaborate);
        }
        // Human-direction add gate: a Human caller may only connect to a bot
        // whose `human_addable` is true.
        if caller_kind == ActorKind::Human && !cfg.human_addable {
            return Err(ServiceError::Forbidden(format!(
                "bot '{to_bot}' is not human-addable"
            )));
        }

        // 5. Idempotency: already friends → Approved (no new ids).
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

        // 6. §4.2 decision tree.
        // Fully-public + auto → no edge (runtime public_default admits at check time).
        let is_fully_public =
            cfg.visibility == "public" && cfg.friend_approval == "auto";
        if is_fully_public {
            return Ok(ConnectResult {
                request_ids: vec![],
                edge_ids: vec![],
                status: ConnectStatus::PublicNoEdge,
                auto_accepted: false,
            });
        }

        if cfg.friend_approval == "auto" {
            // Auto-approve: build edges + approved snapshot requests.
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
        } else {
            // Manual: insert pending request(s), no edges.
            let request_ids = self
                .insert_pending_connect(caller, to_bot, caller_kind, target_kind, message)
                .await?;
            Ok(ConnectResult {
                request_ids,
                edge_ids: vec![],
                status: ConnectStatus::Pending,
                auto_accepted: false,
            })
        }
    }

    async fn approve(&self, request_id: &str, decider: &str) -> ServiceResult<Vec<String>> {
        let req = self
            .requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| ServiceError::FriendRequestNotFound(request_id.to_string()))?;

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
                request_id: Some(req.request_id.clone()),
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
                .backfill_edge_id(&req.request_id, &self.env, eid)
                .await?;
        }
        self.requests
            .decide(
                &req.request_id,
                &self.env,
                RequestStatus::Approved,
                decider,
                None,
                now_millis(),
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
                        .backfill_edge_id(&r.request_id, &self.env, eid)
                        .await?;
                }
                self.requests
                    .decide(
                        &r.request_id,
                        &self.env,
                        RequestStatus::Approved,
                        decider,
                        None,
                        now_millis(),
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
            .ok_or_else(|| ServiceError::FriendRequestNotFound(request_id.to_string()))?;

        if req.status == RequestStatus::Approved {
            return Err(ServiceError::CannotRejectAccepted);
        }
        if req.status != RequestStatus::Pending {
            // Already rejected/cancelled — idempotent no-op.
            return Ok(());
        }

        self.requests
            .decide(
                &req.request_id,
                &self.env,
                RequestStatus::Rejected,
                decider,
                reason.as_deref(),
                now_millis(),
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
                        &r.request_id,
                        &self.env,
                        RequestStatus::Rejected,
                        decider,
                        reason.as_deref(),
                        now_millis(),
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
            .ok_or_else(|| ServiceError::FriendRequestNotFound(request_id.to_string()))?;

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
                    request_id: Some(req.request_id.clone()),
                });
            }
        }

        // The trait passes only request_id; use the request's own `created_by`
        // as the decider (the original requester is the one withdrawing).
        let decider = req.created_by.as_str();
        self.requests
            .decide(
                &req.request_id,
                &self.env,
                RequestStatus::Cancelled,
                decider,
                Some("cancelled by caller"),
                now_millis(),
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
                        &r.request_id,
                        &self.env,
                        RequestStatus::Cancelled,
                        decider,
                        Some("cancelled by caller"),
                        now_millis(),
                    )
                    .await?;
            }
        }
        Ok(())
    }

    async fn revoke_friend(&self, caller: &str, target: &str) -> ServiceResult<Vec<String>> {
        // D12 friend edges are `grant_ref_id == target.default` (caller→target)
        // or `grant_ref_id == caller.default` (target→caller, Bot↔Bot). Revoke
        // exactly those friend edges; leave other (profile/rules) edges alone.
        // Returns the revoked edge_ids (B4c fix — previously a count).
        let mut revoked: Vec<String> = Vec::new();

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
                    self.edge_grants.revoke_grant(&g.edge_id, &self.env).await?;
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
                    self.edge_grants.revoke_grant(&g.edge_id, &self.env).await?;
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
        // Received: to_id == actor — fully supported via list_inbox.
        // Sent / All: the repo port only exposes `list_inbox` (to_id filter).
        // A `list_sent` (from_id filter) is T9 scope creep; for T13 Sent and
        // All return empty with the total we can compute (Received). Marked
        // TODO for a later installment.
        let all: Vec<PermissionRequest> = match direction {
            RequestDirection::Received => {
                self.requests.list_inbox(actor, &self.env, status).await
            }
            RequestDirection::Sent | RequestDirection::All => {
                // TODO(installment-3): add `list_sent(from_id, env, status)`
                // to `PermissionRequestRepoPort` (or extend list_inbox) so the
                // sent direction is backed by the repo. Until then, return an
                // empty page with total=0.
                Vec::new()
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
        let now = now_millis();
        let mut ids = Vec::new();

        // Forward: caller → to_bot.
        let fwd_id = new_request_id();
        self.requests
            .insert(PermissionRequest {
                request_id: fwd_id.clone(),
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
                created_at: now,
                updated_at: now,
                decided_at: None,
            })
            .await?;
        ids.push(fwd_id);

        // Reverse: to_bot → caller (Bot↔Bot only).
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            let rev_id = new_request_id();
            self.requests
                .insert(PermissionRequest {
                    request_id: rev_id.clone(),
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
                    created_at: now,
                    updated_at: now,
                    decided_at: None,
                })
                .await?;
            ids.push(rev_id);
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
    ) -> ServiceResult<(Vec<String>, [String; 2])> {
        let mut edge_ids = Vec::new();

        // Forward target default profile.
        self.profiles.ensure_default_profile(to_bot, &self.env).await?;
        let target_default = self.default_profile_id_of(to_bot).await?;

        // Forward edge: caller → to_bot (ref = to_bot.default).
        let fwd_edge_id = new_edge_id();
        self.edge_grants
            .insert_grant(EdgeGrant {
                edge_id: fwd_edge_id.clone(),
                env: self.env.clone(),
                from_id: caller.to_string(),
                to_id: to_bot.to_string(),
                grant_kind: GrantKind::PermissionProfile,
                grant_ref_id: target_default.clone(),
                rules: None,
                status: EdgeStatus::Approved,
                originator_policy_type: OriginatorPolicyType::Any,
                originator_policy_data: None,
            })
            .await?;
        edge_ids.push(fwd_edge_id);

        let mut default_refs = [target_default, String::new()];

        // Reverse edge: to_bot → caller (ref = caller.default), Bot↔Bot only.
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            self.profiles.ensure_default_profile(caller, &self.env).await?;
            let caller_default = self.default_profile_id_of(caller).await?;

            let rev_edge_id = new_edge_id();
            self.edge_grants
                .insert_grant(EdgeGrant {
                    edge_id: rev_edge_id.clone(),
                    env: self.env.clone(),
                    from_id: to_bot.to_string(),
                    to_id: caller.to_string(),
                    grant_kind: GrantKind::PermissionProfile,
                    grant_ref_id: caller_default.clone(),
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
    async fn default_profile_id_of(&self, bot_id: &str) -> ServiceResult<String> {
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
        edge_ids: &[String],
        default_refs: &[String; 2],
        message: Option<&str>,
    ) -> ServiceResult<Vec<String>> {
        let now = now_millis();
        let mut request_ids = Vec::new();

        // Forward approved snapshot.
        let fwd_req_id = new_request_id();
        self.requests
            .insert(PermissionRequest {
                request_id: fwd_req_id.clone(),
                edge_id: Some(edge_ids[0].clone()),
                env: self.env.clone(),
                from_id: caller.to_string(),
                to_id: to_bot.to_string(),
                request_kind: RequestKind::Connect,
                requested_ref_id: Some(default_refs[0].clone()),
                requested_rules: None,
                message: message.map(|s| s.to_string()),
                status: RequestStatus::Approved,
                decision_reason: None,
                created_by: caller.to_string(),
                decided_by: Some(decider.to_string()),
                created_at: now,
                updated_at: now,
                decided_at: Some(now),
            })
            .await?;
        request_ids.push(fwd_req_id);

        // Reverse approved snapshot (Bot↔Bot only).
        if caller_kind == ActorKind::Bot
            && target_kind == ActorKind::Bot
            && edge_ids.len() == 2
        {
            let rev_req_id = new_request_id();
            self.requests
                .insert(PermissionRequest {
                    request_id: rev_req_id.clone(),
                    edge_id: Some(edge_ids[1].clone()),
                    env: self.env.clone(),
                    from_id: to_bot.to_string(),
                    to_id: caller.to_string(),
                    request_kind: RequestKind::Connect,
                    requested_ref_id: Some(default_refs[1].clone()),
                    requested_rules: None,
                    message: None,
                    status: RequestStatus::Approved,
                    decision_reason: None,
                    created_by: caller.to_string(),
                    decided_by: Some(decider.to_string()),
                    created_at: now,
                    updated_at: now,
                    decided_at: Some(now),
                })
                .await?;
            request_ids.push(rev_req_id);
        }

        Ok(request_ids)
    }
}

// ---- AdmissionService (T14) ----------------------------------------------

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

        if self.edge_grants.has_friend_edge(actor, bot, env).await {
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
                && self.is_default_profile_ref(&g.grant_ref_id, to, env).await
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
    async fn is_default_profile_ref(&self, ref_id: &str, bot: &str, env: &str) -> bool {
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
                edge_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                grant_kind VARCHAR(32) NOT NULL, \
                grant_ref_id VARCHAR(128) NOT NULL, \
                rules TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'approved', \
                originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                PRIMARY KEY (edge_id), \
                UNIQUE (from_id, to_id, env, grant_ref_id))",
        ))
        .await
        .expect("create edge_grants");

        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                permission_profile_id VARCHAR(128) NOT NULL, \
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
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                PRIMARY KEY (permission_profile_id))",
        ))
        .await
        .expect("create permission_profiles");

        db.execute(DbStatement::new(
            "CREATE TABLE permission_requests (\
                request_id VARCHAR(128) NOT NULL, \
                edge_id VARCHAR(128), \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                request_kind VARCHAR(32) NOT NULL, \
                requested_ref_id VARCHAR(128), \
                requested_rules TEXT, \
                message TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'pending', \
                decision_reason TEXT, \
                created_by VARCHAR(128) NOT NULL, \
                decided_by VARCHAR(128), \
                created_at INTEGER NOT NULL, \
                updated_at INTEGER NOT NULL, \
                decided_at INTEGER, \
                PRIMARY KEY (request_id))",
        ))
        .await
        .expect("create permission_requests");

        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (\
                bot_uuid TEXT NOT NULL, \
                env TEXT NOT NULL, \
                visibility TEXT NOT NULL DEFAULT 'public', \
                human_addable INTEGER NOT NULL DEFAULT 0, \
                friend_approval TEXT NOT NULL DEFAULT 'auto', \
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
            "dev".to_string(),
        )
    }

    async fn seed_bot(
        db: &Arc<LocalSqliteDbPlugin>,
        bot_uuid: &str,
        visibility: &str,
        human_addable: bool,
        friend_approval: &str,
        status: &str,
        created_by: Option<&str>,
    ) {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots \
             (bot_uuid, env, visibility, human_addable, friend_approval, status, created_by) \
             VALUES (?, 'dev', ?, ?, ?, ?, ?)",
            vec![
                DbValue::from(bot_uuid),
                DbValue::from(visibility),
                DbValue::from(human_addable),
                DbValue::from(friend_approval),
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
    async fn cannot_add_self() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("bot_a:1", "bot_a:1", None)
            .await
            .expect_err("self-add rejected");
        assert!(matches!(err, ServiceError::CannotAddSelf), "got {err:?}");
    }

    #[tokio::test]
    async fn human_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "human_2", None)
            .await
            .expect_err("human→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn bot_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("x:1", "human_2", None)
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
            .create_connect("human_1", "x:missing", None)
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
        seed_bot(&db, "x:hidden", "public", true, "auto", "hidden", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:hidden", None)
            .await
            .expect_err("hidden → BotHidden");
        assert!(matches!(err, ServiceError::BotHidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn private_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:priv", "private", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:priv", None)
            .await
            .expect_err("private → PrivateBotCannotCollaborate");
        assert!(
            matches!(err, ServiceError::PrivateBotCannotCollaborate),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn human_addable_false_for_human_caller() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nha", "protected", false, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:nha", None)
            .await
            .expect_err("!human_addable → Forbidden for human caller");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn public_auto_bot_returns_public_no_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub", "public", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:pub", None)
            .await
            .expect("public+auto → PublicNoEdge");
        assert_eq!(res.status, ConnectStatus::PublicNoEdge);
        assert!(res.edge_ids.is_empty());
        assert!(res.request_ids.is_empty());
        assert!(!res.auto_accepted);
        // No edge, no request were created: PublicNoEdge builds nothing (admission
        // uses runtime public_default instead). has_friend_edge is therefore
        // false, and no edge row exists.
        assert!(
            !eg.has_friend_edge("human_1", "x:pub", "dev").await,
            "PublicNoEdge must not create a friend edge"
        );
        let active = eg.list_active_grants("human_1", "x:pub", "dev").await;
        assert!(active.is_empty(), "PublicNoEdge must not create an edge");
    }

    #[tokio::test]
    async fn human_to_bot_manual_returns_pending_one_request_no_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:man", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:man", Some("hi".into()))
            .await
            .expect("manual → Pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert_eq!(res.request_ids.len(), 1, "exactly one pending request");
        assert!(res.edge_ids.is_empty());
        // default profile should NOT have been seeded (no edge built).
        assert!(pp.get_active_default("x:man", "dev").await.is_none());
        let id = &res.request_ids[0];
        let r = rq.get(id, "dev").await.expect("pending request exists");
        assert_eq!(r.status, RequestStatus::Pending);
        assert_eq!(r.from_id, "human_1");
        assert_eq!(r.to_id, "x:man");
        assert!(r.edge_id.is_none());
    }

    #[tokio::test]
    async fn human_to_bot_auto_approves_one_edge_one_request() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:auto1", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:auto1", None)
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
        assert_eq!(r.edge_id.as_deref(), Some(res.edge_ids[0].as_str()));
    }

    #[tokio::test]
    async fn bot_to_bot_auto_approves_two_edges_two_requests() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:botA", "protected", true, "auto", "online", Some("85020")).await;
        seed_bot(&db, "x:botB", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("x:botA", "x:botB", None)
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
        seed_bot(&db, "x:idem", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:idem", None)
            .await
            .expect("first connect");
        assert_eq!(first.status, ConnectStatus::Approved);
        let second = svc
            .create_connect("human_1", "x:idem", None)
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
        seed_bot(&db, "x:pend", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:pend", None)
            .await
            .expect("first manual");
        assert_eq!(first.status, ConnectStatus::Pending);
        let second = svc
            .create_connect("human_1", "x:pend", None)
            .await
            .expect("second idempotent");
        assert_eq!(second.status, ConnectStatus::Pending);
        // Returns the SAME pending request id (no duplicate insert).
        assert_eq!(first.request_ids, second.request_ids);
    }

    #[tokio::test]
    async fn approve_pending_human_to_bot_builds_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:appr", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:appr", None)
            .await
            .expect("manual pending");
        assert_eq!(pending.status, ConnectStatus::Pending);
        let rid = &pending.request_ids[0];

        let edge_ids = svc.approve(rid, "85020").await.expect("approve ok");
        assert_eq!(edge_ids.len(), 1, "Human→Bot approve: 1 edge");
        // Original pending request is now approved + edge_id backfilled.
        let r = rq.get(rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.edge_id.as_deref(), Some(edge_ids[0].as_str()));
        assert!(eg.has_friend_edge("human_1", "x:appr", "dev").await);
    }

    #[tokio::test]
    async fn approve_does_not_duplicate_request_rows() {
        // The approve path must decide the existing pending row in place —
        // NOT insert a second approved snapshot row (only the create_connect
        // auto path inserts snapshots). Total connect request rows for this
        // Human→Bot connect must remain 1 after approve.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nodupe", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:nodupe", None)
            .await
            .expect("manual pending");
        let rid = &pending.request_ids[0];

        svc.approve(rid, "85020").await.expect("approve ok");

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
        seed_bot(&db, "x:bbA", "protected", true, "manual", "online", Some("85020")).await;
        seed_bot(&db, "x:bbB", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:bbA", "x:bbB", None)
            .await
            .expect("manual pending Bot↔Bot");
        assert_eq!(pending.request_ids.len(), 2);
        let fwd_id = &pending.request_ids[0];

        let edge_ids = svc.approve(fwd_id, "owner").await.expect("approve ok");
        assert_eq!(edge_ids.len(), 2, "Bot↔Bot approve: 2 edges");

        // BOTH pending requests are now approved (single accept, §4.1).
        let fwd = rq.get(fwd_id, "dev").await.expect("fwd present");
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
        seed_bot(&db, "x:rej", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:rej", None)
            .await
            .expect("manual pending");
        let rid = &pending.request_ids[0];
        svc.reject(rid, "85020", Some("no thanks".into()))
            .await
            .expect("reject ok");
        let r = rq.get(rid, "dev").await.expect("request present");
        assert_eq!(r.status, RequestStatus::Rejected);
        assert!(r.edge_id.is_none());
        let active = eg.list_active_grants("human_1", "x:rej", "dev").await;
        assert!(active.is_empty(), "no edge built on reject");
    }

    #[tokio::test]
    async fn reject_bot_to_bot_rejects_both() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rbA", "protected", true, "manual", "online", Some("85020")).await;
        seed_bot(&db, "x:rbB", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:rbA", "x:rbB", None)
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
        seed_bot(&db, "x:canc", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:canc", None)
            .await
            .expect("pending");
        let rid = &pending.request_ids[0];
        svc.cancel(rid).await.expect("cancel ok");
        let r = rq.get(rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Cancelled);
        // cancelling an already-cancelled request is idempotent (B4e): Ok, not
        // an error. Spec says "已 rejected/cancelled 幂等".
        svc.cancel(rid).await.expect("idempotent cancel ok");
        let r2 = rq.get(rid, "dev").await.expect("request still exists");
        assert_eq!(r2.status, RequestStatus::Cancelled);
    }

    #[tokio::test]
    async fn revoke_friend_human_to_bot_one_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:unf", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let created = svc
            .create_connect("human_1", "x:unf", None)
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
        seed_bot(&db, "x:uA", "protected", true, "auto", "online", Some("85020")).await;
        seed_bot(&db, "x:uB", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("x:uA", "x:uB", None).await.expect("connect");
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
        seed_bot(&db, "x:keep", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:keep", None).await.expect("connect");
        // Manually insert a writer-profile edge with a different ref id.
        eg.insert_grant(EdgeGrant {
            edge_id: "eg_writer_1".to_string(),
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:keep".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: "pp_x:keep_writer".to_string(), // NOT the default
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
        assert_eq!(active[0].grant_ref_id, "pp_x:keep_writer");
    }

    #[tokio::test]
    async fn list_friends_after_connect() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lf1", "protected", true, "auto", "online", Some("85020")).await;
        seed_bot(&db, "x:lf2", "protected", true, "auto", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:lf1", None).await.expect("c1");
        svc.create_connect("human_1", "x:lf2", None).await.expect("c2");

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
        seed_bot(&db, "x:lr", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:lr", None)
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
        // Sent direction is a T13 TODO (empty).
        let page = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 0, "Sent direction not yet backed by repo");
    }

    #[tokio::test]
    async fn list_requests_pagination() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pg", "protected", true, "manual", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        // Create 3 separate human callers connecting to the same bot.
        for h in ["human_a", "human_b", "human_c"] {
            svc.create_connect(h, "x:pg", None).await.expect("pending");
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
        seed_bot(&db, "x:hid", "public", true, "auto", "hidden", Some("85020")).await;
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
        seed_bot(&db, "x:fr", "protected", true, "auto", "online", Some("85020")).await;
        // Seed a friend edge by going through ConnectService (auto path),
        // which also ensures the target default profile and builds the edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:fr", None)
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
        seed_bot(&db, "x:pub", "public", true, "auto", "online", Some("85020")).await;
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
        seed_bot(&db, "x:prot", "protected", true, "auto", "online", Some("85020")).await;
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
        seed_bot(&db, "x:az", "protected", true, "auto", "online", Some("85020")).await;
        // Seed an approved friend edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:az", None)
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
        seed_bot(&db, "x:prot2", "protected", true, "auto", "online", Some("85020")).await;
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
        seed_bot(&db, "x:pub2", "public", true, "auto", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:pub2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert_eq!(ctx.grants.len(), 1, "public bot: public_default grant injected");
        assert_eq!(ctx.grants[0].source, GrantSource::PublicDefault);
    }
}