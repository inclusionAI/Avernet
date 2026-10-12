//! DbConnectService private helpers (plan Task 12 fix round, behavior-
//! preserving move out of the former over-limit `lib.rs`): notification and
//! friend-scope plumbing, pending/approved request seeding, edge building
//! with the §12.5 sub-operation audits, and the idempotency lookups.

use std::collections::HashSet;
use std::sync::Arc;

use tracing::{info, warn};

use bcs_domain::actor::ActorKind;
use bcs_domain::edge_permission::{
    EdgeGrant, GrantKind, PermissionRequest, RequestKind, RequestStatus,
};

use bcs_service_api::application::connect::{ConnectStatus, FriendListQuery, RequestDirection, RequestsPage};
use bcs_service_api::port::{
    FriendAuthSyncAction, FriendAuthSyncCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort, PermissionRequestRepoPort,
};
use bcs_service_api::types::BotOperationContext;
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_user_directory_api::UserDirectoryLookupContext;

use bcs_service_api::port::FriendConnectNotificationCommand;
use crate::{
    actor_kind_of, bot_friend_ext_no_check_scope_friend_deps,
    bot_friend_ext_view_scope_agent_friend_deps,
    bot_friend_ext_view_scope_user_friend_deps, department_matches_allowlist_entry, is_lock_wait_timeout,
    is_private_visibility, new_request_id, DbConnectService, normalize_policy_value,
    user_directory_lookup_context,
};

// ---- private helpers ------------------------------------------------------

impl DbConnectService {
    /// Resolve the EXTERNAL owner work no of `bot` from its
    /// `BotActorConfig.created_by` — this is the back-end `FriendAuthSyncPort`
    /// addressing convention ONLY (spec §12.3).
    ///
    /// Retention note (plan Task 12 audit): the external friend-auth sync
    /// addresses its backend by the historical creator work no and the
    /// external Bot-ID suffix convention; the CURRENT BCS owner is never
    /// projected into this external addressing, and ownership transfers or
    /// manager changes must never trigger this sync at all.
    ///
    /// `created_by` is normally a bare staff no (e.g. `"85020"`); if a legacy
    /// or migration-shaped `human_<staff>` value is encountered, the `human_`
    /// prefix is stripped. Returns `None` when the bot is unknown or has no
    /// `created_by` (legacy bot) — callers pass `String::new()` downstream so
    /// the friend-auth-sync backend best-effort fails safely on a missing owner.
    pub(crate) async fn owner_work_no_from_bot_config(
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

    pub(crate) async fn resolve_user_department_code(
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
                // The friend-scope policy follows the Bot's CURRENT owner
                // (plan Task 12, spec §12.3 internal-authz routes through the
                // new authority); the historical creator remains only the
                // fallback documented for v0/unwired assemblies.
                let cfg = self.bot_config.get(actor_id, &self.env).await;
                let owner = self
                    .current_bot_owner_work_no(actor_id, cfg.as_ref().and_then(|cfg| cfg.created_by.as_deref()))
                    .await;
                if owner.is_empty() {
                    info!(
                        actor_id = %actor_id,
                        env = %self.env,
                        "skip user department lookup because bot owner staff_no is missing"
                    );
                    return None;
                }
                owner
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

    pub(crate) async fn caller_department_matches_friend_allowlist(
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

    pub(crate) async fn actor_department_matches_friend_scope(
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

    /// Friend-request approval addressees (plan Task 12, spec §12.4/§12.3):
    /// the CURRENT Human owner of the target Bot through the strict
    /// authority core — a creator who transferred ownership away no longer
    /// receives friend approvals. Uninitialized (version 0) or
    /// authority-less assemblies keep the historical creator as addressee.
    pub(crate) async fn target_notification_recipients(
        &self,
        cfg: &bcs_domain::edge_permission::BotActorConfig,
    ) -> Vec<String> {
        let recipient = self
            .current_bot_owner_work_no(cfg.bot_id.as_str(), cfg.created_by.as_deref())
            .await;
        if recipient.is_empty() {
            Vec::new()
        } else {
            vec![recipient]
        }
    }

    pub(crate) async fn emit_friend_connect_notification(
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
            let cfg = self.bot_config.get(applicant_actor_id, &self.env).await;
            self.current_bot_owner_work_no(applicant_actor_id, cfg.as_ref().and_then(|cfg| cfg.created_by.as_deref()))
                .await
                .into()
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
    pub(crate) async fn resolve_actor_display_name(&self, actor_id: &str) -> Option<String> {
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
    pub(crate) async fn find_pending_connect(&self, from: &str, to: &str) -> Vec<PermissionRequest> {
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
    pub(crate) async fn insert_pending_connect(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
        message: Option<String>,
        operation: &BotOperationContext,
    ) -> ServiceResult<Vec<String>> {
        let mut ids = Vec::new();

        // Forward: caller → to_bot.
        let fwd_request_id = new_request_id();
        self.requests
            .insert(
                PermissionRequest {
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
            },
                &operation.for_sub_record(&format!("req-{fwd_request_id}")),
            )
            .await?;
        ids.push(fwd_request_id);

        // Reverse: to_bot → caller (Bot↔Bot only).
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            let rev_request_id = new_request_id();
            self.requests
                .insert(
                    PermissionRequest {
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
                    },
                    &operation.for_sub_record(&format!("req-{rev_request_id}")),
                )
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
    pub(crate) async fn build_connect_edges(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
        operation: &BotOperationContext,
    ) -> ServiceResult<(Vec<u64>, [u64; 2])> {
        let mut edge_ids = Vec::new();

        // Forward target default profile.
        self.profiles.ensure_default_profile(to_bot, &self.env).await?;
        let target_default = self.default_profile_id_of(to_bot).await?;

        // Forward edge: caller → to_bot (ref = to_bot.default). One audit
        // slot per persisted record (plan Task 12 sub-operation).
        let fwd_edge_id = self
            .edge_grants
            .insert_grant(
                EdgeGrant::new_non_role(
                    0,
                    self.env.clone(),
                    caller.to_string(),
                    to_bot.to_string(),
                    GrantKind::PermissionProfile,
                    target_default,
                    None,
                ),
                &operation.for_sub_record("edge-fwd"),
            )
            .await?;
        edge_ids.push(fwd_edge_id);

        let mut default_refs = [target_default, 0];

        // Reverse edge: to_bot → caller (ref = caller.default), Bot↔Bot only.
        if caller_kind == ActorKind::Bot && target_kind == ActorKind::Bot {
            self.profiles.ensure_default_profile(caller, &self.env).await?;
            let caller_default = self.default_profile_id_of(caller).await?;

            let rev_edge_id = self
                .edge_grants
                .insert_grant(
                    EdgeGrant::new_non_role(
                        0,
                        self.env.clone(),
                        to_bot.to_string(),
                        caller.to_string(),
                        GrantKind::PermissionProfile,
                        caller_default,
                        None,
                    ),
                    &operation.for_sub_record("edge-rev"),
                )
                .await?;
            edge_ids.push(rev_edge_id);
            default_refs[1] = caller_default;
        }

        Ok((edge_ids, default_refs))
    }

    pub(crate) async fn build_connect_edges_with_profile_retry(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
        operation: &BotOperationContext,
    ) -> ServiceResult<(Vec<u64>, [u64; 2])> {
        let mut attempts = 0;
        loop {
            match self
                .build_connect_edges(caller, to_bot, caller_kind, target_kind, operation)
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
    pub(crate) async fn default_profile_id_of(&self, bot_id: &str) -> ServiceResult<u64> {
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
    pub(crate) async fn insert_approved_connect_requests(
        &self,
        caller: &str,
        to_bot: &str,
        caller_kind: ActorKind,
        target_kind: ActorKind,
        decider: &str,
        edge_ids: &[u64],
        default_refs: &[u64; 2],
        message: Option<&str>,
        operation: &BotOperationContext,
    ) -> ServiceResult<Vec<String>> {
        let mut request_ids = Vec::new();

        // Forward approved snapshot.
        let fwd_request_id = new_request_id();
        self.requests
            .insert(
                PermissionRequest {
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
                },
                &operation.for_sub_record(&format!("req-{fwd_request_id}")),
            )
            .await?;
        request_ids.push(fwd_request_id);

        // Reverse approved snapshot (Bot↔Bot only).
        if caller_kind == ActorKind::Bot
            && target_kind == ActorKind::Bot
            && edge_ids.len() == 2
        {
            let rev_request_id = new_request_id();
            self.requests
                .insert(
                    PermissionRequest {
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
                    },
                    &operation.for_sub_record(&format!("req-{rev_request_id}")),
                )
                .await?;
            request_ids.push(rev_request_id);
        }

        Ok(request_ids)
    }
}
