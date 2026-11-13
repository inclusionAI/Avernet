//! DB-backed `ConnectService` for the edge-permission model.
//!
//! Moved verbatim from `lib.rs` during the crate split; see the
//! crate-level module docs for the D3 edge rules and spec links.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use tracing::{info, warn};
use uuid::Uuid;
use bcs_domain::actor::ActorKind;
use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus,
    FriendListEntry, GrantKind, OriginatorPolicyType, PermissionRequest, RequestKind,
    RequestStatus,
};
use bcs_service_api::application::connect::{
    ConnectResult, ConnectService, ConnectStatus, FriendEntriesPage, FriendListQuery, RequestDirection, RequestsPage,
};
use bcs_service_api::port::{
    FriendAuthSyncAction, FriendAuthSyncCommand, FriendAuthSyncPort,
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort, FriendshipWritebackPort,
};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, BotAuthorityRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
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
    collaboration_registry: Option<Arc<dyn bcs_service_api::BotRegistryCoreService>>,
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    profiles: Arc<dyn PermissionProfileRepoPort>,
    requests: Arc<dyn PermissionRequestRepoPort>,
    bot_config: Arc<dyn BotActorConfigRepoPort>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    friend_connect_notification: Arc<dyn FriendConnectNotificationPort>,
    /// BCS→backend friend-auth-sync; used by Task 11 triggers (grant/revoke).
    friend_auth_sync: Arc<dyn FriendAuthSyncPort>,
    /// Edge→legacy reverse sync: mirrors friendship decisions into the legacy
    /// repo/relation readers. Human failures propagate; Bot↔Bot is best-effort.
    /// `None` is for profiles/tests without a legacy reader.
    friendship_writeback: Option<Arc<dyn FriendshipWritebackPort>>,
    /// Strict authority reads for the CURRENT owner: friend-request
    /// notifications must reach the owner edge holder, not the historical
    /// `created_by` fact (ownership may have been transferred since).
    /// `None` = degrade to the `created_by` fallback (tests that do not
    /// seed authority state).
    authority: Option<Arc<dyn BotAuthorityRepoPort>>,
    env: String,
}

impl DbConnectService {
    pub fn with_collaboration_registry(mut self, registry: Arc<dyn bcs_service_api::BotRegistryCoreService>) -> Self {
        self.collaboration_registry = Some(registry);
        self
    }

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
            collaboration_registry: None,
            edge_grants,
            profiles,
            requests,
            bot_config,
            user_directory,
            friend_connect_notification,
            friend_auth_sync,
            friendship_writeback: None,
            authority: None,
            env,
        }
    }

    /// The env this service is scoped to.
    pub fn env(&self) -> &str {
        &self.env
    }

    /// Inject the edge→legacy friendship reverse-sync port.
    ///
    /// Builder style (consumes and returns self) so the existing `new`
    /// signature and all current callers stay untouched; bootstrap is the
    /// only production caller that sets it.
    pub fn with_friendship_writeback(
        mut self,
        writeback: Option<Arc<dyn FriendshipWritebackPort>>,
    ) -> Self {
        self.friendship_writeback = writeback;
        self
    }

    /// Inject the strict authority read port used to resolve the CURRENT
    /// owner for friend-request notifications. Builder style (consumes and
    /// returns self) so the existing `new` signature and all current callers
    /// stay untouched; the composition root wires it.
    pub fn with_bot_authority_port(
        mut self,
        authority: Option<Arc<dyn BotAuthorityRepoPort>>,
    ) -> Self {
        self.authority = authority;
        self
    }

    /// Mirror friendships for legacy readers. Human mirror failures propagate;
    /// Bot↔Bot retains the existing best-effort coexistence policy.
    async fn writeback_add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()> {
        let Some(writeback) = self.friendship_writeback.as_ref() else {
            return Ok(());
        };
        if let Err(err) = writeback.add_friendship(caller, target).await {
            // Human legacy lists still read the relation mirror, so a failed
            // write is not a successful friendship operation. Retrying repairs it.
            if actor_kind_of(caller) == ActorKind::Human || actor_kind_of(target) == ActorKind::Human {
                return Err(err);
            }
            warn!(
                caller = %caller,
                target = %target,
                error = %err,
                "friendship writeback add failed (best-effort; edge grant remains source of truth)"
            );
        }
        Ok(())
    }

    /// Reverse-sync symmetric removal; same per-actor error policy as
    /// [`Self::writeback_add_friendship`].
    async fn writeback_remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<()> {
        let Some(writeback) = self.friendship_writeback.as_ref() else {
            return Ok(());
        };
        if let Err(err) = writeback.remove_friendship(caller, target).await {
            // Human legacy lists still read the relation mirror, so a failed
            // write is not a successful friendship operation. Retrying repairs it.
            if actor_kind_of(caller) == ActorKind::Human || actor_kind_of(target) == ActorKind::Human {
                return Err(err);
            }
            warn!(
                caller = %caller,
                target = %target,
                error = %err,
                "friendship writeback remove failed (best-effort)"
            );
        }
        Ok(())
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
pub(super) fn actor_kind_of(id: &str) -> ActorKind {
    if id.starts_with("human_") {
        ActorKind::Human
    } else {
        ActorKind::Bot
    }
}

fn is_lock_wait_timeout(err: &ServiceError) -> bool {
    let message = err.to_string();
    message.contains("1205") || message.to_ascii_lowercase().contains("lock wait timeout")
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

/// Match label of a department-scope allowlist entry. The legacy form is a
/// bare string kept verbatim after trim; the new form is a
/// `{deptNo, deptName}` object (also accepting dept_no/dept_name/name keys):
/// the department name wins by key order, falling back to the department code
/// when no name is present.
///
/// Entries that yield no department (blank strings, misspelled or unrelated
/// object fields, other shapes) are dropped with a WARN recording the key,
/// index and raw entry — silently reading the allowlist as empty once routed
/// in-scope approval-exempt applicants into the approval flow with no trace,
/// so every drop must leave a record.
fn friend_ext_scope_entry_match_label(item: &serde_json::Value) -> Option<String> {
    if let Some(value) = item.as_str() {
        let trimmed = value.trim();
        return if trimmed.is_empty() { None } else { Some(trimmed.to_owned()) };
    }
    let object = item.as_object()?;
    ["deptName", "dept_name", "name", "deptNo", "dept_no"]
        .into_iter()
        .find_map(|key| object.get(key).and_then(|value| value.as_str()).map(str::trim))
        .filter(|label| !label.is_empty())
        .map(str::to_owned)
}

fn bot_friend_ext_scope_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
    key: &str,
) -> HashSet<String> {
    friend_ext
        .get(key)
        .and_then(|value| value.as_array())
        .map(|items| {
            let mut deps: HashSet<String> = HashSet::new();
            for (index, item) in items.iter().enumerate() {
                if let Some(label) = friend_ext_scope_entry_match_label(item) {
                    deps.insert(label);
                } else {
                    warn!(
                        friend_ext_key = key,
                        entry_index = index,
                        entry_kind = %item,
                        "friend_ext scope entry yielded no department: expected a department string or a {{deptNo, deptName}} object",
                    );
                }
            }
            deps
        })
        .unwrap_or_default()
}

pub(crate) fn bot_friend_ext_no_check_scope_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "no_check_scope_friend_deps")
}

pub(crate) fn bot_friend_ext_view_scope_user_friend_deps(
    friend_ext: &serde_json::Map<String, serde_json::Value>,
) -> HashSet<String> {
    bot_friend_ext_scope_friend_deps(friend_ext, "view_scope_user_friend_deps")
}

pub(crate) fn bot_friend_ext_view_scope_agent_friend_deps(
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

mod lifecycle;

// ---- private helpers ------------------------------------------------------

impl DbConnectService {
    /// Resolve the OWNER work no of `bot` from its `BotActorConfig.created_by`.
    ///
    /// `created_by` is normally a bare staff no (e.g. `"85020"`); if a legacy
    /// or migration-shaped `human_<staff>` value is encountered, the `human_`
    /// prefix is stripped. Returns `None` when the bot is unknown or has no
    /// `created_by` (legacy bot) — callers pass `String::new()` downstream so
    /// the friend-auth-sync backend best-effort fails safely on a missing owner.
    async fn owner_work_no_from_bot_config(
        &self,
        bot: &str,
        env: &str,
    ) -> Option<String> {
        self.bot_config.get(bot, env).await.and_then(|c| {
            c.created_by.map(|owner| {
                owner
                    .strip_prefix("human_")
                    .unwrap_or(&owner)
                    .to_string()
            })
        })
    }

    pub(super) async fn resolve_user_department_code(
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

    /// Recipients of the friend-request approval notification.
    ///
    /// The CURRENT authority owner of the target bot decides pending
    /// requests, so the current owner must be notified. `created_by` is only
    /// a historical creator fact: after an ownership transfer the former
    /// creator keeps receiving nothing-relevant pings while the new owner
    /// stays unaware. When the authority port is unwired or its read fails
    /// (uninitialized legacy ownership, transient lookup failure), degrade
    /// to the `created_by` fallback with a WARN so ownerless/legacy bots
    /// keep their existing notification behavior.
    async fn target_notification_recipients(
        &self,
        to_bot: &str,
        cfg: &bcs_domain::edge_permission::BotActorConfig,
    ) -> Vec<String> {
        if let Some(authority) = &self.authority {
            match authority.ownership(to_bot).await {
                Ok(view) if !view.owner_user_id.is_empty() => {
                    return vec![view.owner_user_id];
                }
                Ok(_) => {
                    warn!(
                        target_bot_id = %to_bot,
                        env = %self.env,
                        "authority ownership resolved to an empty owner id; \
                         falling back to bot created_by for friend notification"
                    );
                }
                Err(error) => {
                    warn!(
                        target_bot_id = %to_bot,
                        env = %self.env,
                        error = %error,
                        "authority ownership read failed; \
                         falling back to bot created_by for friend notification"
                    );
                }
            }
        }
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
            warn!(
                request_ids = ?request_ids,
                target_bot_id = %target_bot_id,
                env = %self.env,
                "friend connect notification skipped: no recipients"
            );
            return Ok(());
        }
        // Resolve human-readable display names so the notification body says
        // "李四 申请添加你的 Bot「本地代码专家」…" instead of raw actor ids. Falls
        // back to None (the adapter then renders the actor id) on any miss.
        let applicant_name = self.resolve_actor_display_name(applicant_actor_id).await;
        let target_bot_name = self.resolve_actor_display_name(target_bot_id).await;
        // A Bot applicant carries NO Human applicant id: the
        // work-order intake derives the acting Human from the
        // verified principal forwarded with this notification (the
        // owner/manager who actually performed the request). The
        // Bot's historical `created_by` must never substitute for
        // the acting user — ownership may have moved since, and a
        // manager acting today is not the creator at all. Human
        // applicants were already `None` here (the adapter strips
        // `human_` to a staff_no).
        let applicant_user_id = None;
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
    async fn find_pending_connect(&self, from: &str, to: &str) -> ServiceResult<Vec<PermissionRequest>> {
        let inbox = self
            .requests
            .list_inbox(to, &self.env, Some(RequestStatus::Pending))
            .await?;
        Ok(inbox
            .into_iter()
            .filter(|r| r.from_id == from && r.request_kind == RequestKind::Connect)
            .collect())
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
                // DB-managed timestamps; the store reads back gmt_* values.
                created_at: 0,
                updated_at: 0,
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
                    created_at: 0,
                    updated_at: 0,
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

    async fn build_connect_edges_with_profile_retry(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
    ) -> ServiceResult<(Vec<u64>, [u64; 2])> {
        let mut attempts = 0;
        loop {
            match self
                .build_connect_edges(caller, to_bot, caller_kind, target_kind)
                .await
            {
                Ok(result) => return Ok(result),
                Err(err) if is_lock_wait_timeout(&err) && attempts < 2 => {
                    attempts += 1;
                    warn!(
                        caller = %caller,
                        to_bot = %to_bot,
                        env = %self.env,
                        attempt = attempts,
                        error = %err,
                        "retrying edge-permission friend-edge sync after lock wait timeout"
                    );
                    tokio::time::sleep(std::time::Duration::from_millis(20 * attempts)).await;
                }
                Err(err) => return Err(err),
            }
        }
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
                // created_at/updated_at are DB-managed too; placeholders only.
                created_at: 0,
                updated_at: 0,
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
                    created_at: 0,
                    updated_at: 0,
                })
                .await?;
            request_ids.push(rev_request_id);
        }

        Ok(request_ids)
    }
}

#[async_trait]
impl EdgePermissionFriendSyncService for DbConnectService {
    async fn sync_add_friendship(&self, a: &str, b: &str) -> ServiceResult<()> {
        if a == b {
            warn!(actor = %a, env = %self.env, "skip edge-permission friendship sync for self friendship");
            return Ok(());
        }

        let a_kind = actor_kind_of(a);
        let b_kind = actor_kind_of(b);
        let (caller, target, caller_kind, target_kind) = match (a_kind, b_kind) {
            (ActorKind::Human, ActorKind::Bot) => (a, b, a_kind, b_kind),
            (ActorKind::Bot, ActorKind::Human) => (b, a, b_kind, a_kind),
            (ActorKind::Bot, ActorKind::Bot) => (a, b, a_kind, b_kind),
            (ActorKind::Human, ActorKind::Human) => {
                warn!(left = %a, right = %b, env = %self.env, "skip edge-permission friendship sync for unsupported human-human friendship");
                return Ok(());
            }
        };

        if caller_kind == ActorKind::Human
            && self.edge_grants.has_friend_edge(caller, target, &self.env).await
        {
            return Ok(());
        }

        self.build_connect_edges_with_profile_retry(caller, target, caller_kind, target_kind)
            .await?;
        Ok(())
    }

    async fn sync_remove_friendship(&self, a: &str, b: &str) -> ServiceResult<()> {
        if a == b {
            return Ok(());
        }

        let a_kind = actor_kind_of(a);
        let b_kind = actor_kind_of(b);
        let (caller, target) = match (a_kind, b_kind) {
            (ActorKind::Human, ActorKind::Bot) => (a, b),
            (ActorKind::Bot, ActorKind::Human) => (b, a),
            (ActorKind::Bot, ActorKind::Bot) => (a, b),
            (ActorKind::Human, ActorKind::Human) => {
                warn!(left = %a, right = %b, env = %self.env, "skip edge-permission friendship removal sync for unsupported human-human friendship");
                return Ok(());
            }
        };

        // The edge-permission sync path has no inbound HTTP principal; pass
        // None (the revoke trigger degrades to a best-effort, auth-less call).
        self.revoke_friend(caller, target, None).await.map(|_| ())
    }
}
