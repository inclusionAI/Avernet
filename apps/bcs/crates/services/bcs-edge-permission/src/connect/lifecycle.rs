//! Connect request lifecycle and listing.
use super::*;

/// Bot↔Bot creation stores a generated reverse row for the second directed
/// edge. It is not an independent request from the peer and must not be exposed
/// as one. A genuine peer-initiated request has `created_by == from_id`.
fn is_internal_reverse_connect(request: &PermissionRequest) -> bool {
    request.request_kind == RequestKind::Connect && request.created_by != request.from_id
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

        let same_owner = match &self.collaboration_registry {
            Some(registry) => registry.collaboration_owner(&[caller.into(), to_bot.into()]).await?.is_some(),
            None => false,
        };
        // 4. Idempotency: already friends → Approved (no new ids).
        if self.edge_grants.has_friend_edge(caller, to_bot, &self.env).await {
            if caller_kind == ActorKind::Human {
                self.writeback_add_friendship(caller, to_bot).await?;
            }
            return Ok(ConnectResult {
                request_ids: vec![],
                edge_ids: vec![],
                status: ConnectStatus::Approved,
                auto_accepted: false,
            });
        }
        // Idempotency: pending connect already exists in this direction →
        // Pending (don't re-insert, don't 409 — return the existing state).
        let pending = self.find_pending_connect(caller, to_bot).await?;
        if !pending.is_empty() {
            let mut request_ids: Vec<String> = pending
                .iter()
                .filter(|request| !is_internal_reverse_connect(request))
                .map(|request| request.request_id.clone())
                .collect();
            // A genuine same-direction pending request means the caller is
            // re-applying. Retry the owner notification in that case: a single
            // transient notification failure (empty recipients resolution or a
            // backend intake rejection) must not be frozen forever — without a
            // retry the pending request deadlocks with nobody able to approve
            // it.
            let resend_forward = !request_ids.is_empty();
            if request_ids.is_empty() {
                // A peer creating in the opposite direction finds our generated
                // reverse mirror. Return the original logical request instead
                // of an internal ID that cannot be decided directly.
                request_ids = self
                    .find_pending_connect(to_bot, caller)
                    .await?
                    .into_iter()
                    .filter(|request| !is_internal_reverse_connect(request))
                    .map(|request| request.request_id)
                    .collect();
            }
            if request_ids.is_empty() {
                return Err(ServiceError::InternalError(
                    "pending connect pair has no user-authored request".to_string(),
                ));
            }
            if resend_forward {
                // Best-effort, idempotent retry of the owner notification. The
                // returned status stays Pending regardless of the transport
                // outcome (Spec §12.3: a notification failure must not fail the
                // friend request), so a repeated create acts as a fresh
                // notification attempt instead of a silent no-op. Reuse the
                // stored pending row's message (the reason the owner sees) so a
                // retry with a different/absent body still reproduces the
                // original request intent.
                let retry_message = pending
                    .iter()
                    .find(|request| !is_internal_reverse_connect(request))
                    .and_then(|request| request.message.as_deref());
                if let Err(error) = self
                    .emit_friend_connect_notification(
                        FriendConnectNotificationKind::ApprovalRequested,
                        request_ids.clone(),
                        caller,
                        to_bot,
                        self.target_notification_recipients(to_bot, &cfg).await,
                        retry_message,
                        request_auth.clone(),
                    )
                    .await
                {
                    warn!(
                        error = %error,
                        request_ids = ?request_ids,
                        target_bot_id = %to_bot,
                        "friend connect notification resend (dedup) failed; continuing without it"
                    );
                }
            }
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
        if caller_kind == ActorKind::Bot && !same_owner && is_private_visibility(&cfg.visibility) {
            return Err(ServiceError::PrivateBotCannotCollaborate);
        }
        if caller_kind == ActorKind::Human && !same_owner && is_private_visibility(&cfg.user_visibility) {
            return Err(ServiceError::Forbidden(format!(
                "bot '{to_bot}' is not human-addable"
            )));
        }

        if caller_kind == ActorKind::Human && !same_owner && normalize_policy_value(&cfg.user_visibility) == "protected" {
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
        if caller_kind == ActorKind::Bot && !same_owner && normalize_policy_value(&cfg.visibility) == "protected" {
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
        let needs_approval = !(same_owner || friend_strategy == "open" || dept_free_auto_approved);
        // Dispatch on the caller-appropriate visibility: bots on `visibility`,
        // humans on `user_visibility`. The matching `private` case is rejected
        // above for that kind, so only public/protected reach here in practice.
        let collab_visibility = if same_owner { "protected".to_string() } else if caller_kind == ActorKind::Human {
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
                    // Spec §12.3: the pending rows are already written and the
                    // friend request must stay runnable, so a notification
                    // transport failure (e.g. backend intake rejects the call)
                    // degrades to warn+skip instead of failing the create.
                    if let Err(error) = self
                        .emit_friend_connect_notification(
                            FriendConnectNotificationKind::ApprovalRequested,
                            request_ids.clone(),
                            caller,
                            to_bot,
                            self.target_notification_recipients(to_bot, &cfg).await,
                            message.as_deref(),
                            request_auth.clone(),
                        )
                        .await
                    {
                        warn!(
                            error = %error,
                            request_ids = ?request_ids,
                            target_bot_id = %to_bot,
                            "friend connect notification failed; continuing without it"
                        );
                    }
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

                    // Reverse-sync (coexistence): mirror the friendship
                    // into the legacy store the group gate still reads.
                    self.writeback_add_friendship(caller, to_bot).await?;

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
    ) -> ServiceResult<Vec<u64>> {
        let req = self
            .requests
            .get(request_id, &self.env)
            .await
            .ok_or_else(|| {
                self.log_request_lookup_miss("approve", request_id, Some(decider));
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })?;

        if is_internal_reverse_connect(&req) {
            return Err(ServiceError::Forbidden(format!(
                "request '{}' is a generated reverse mirror and cannot be decided directly",
                req.request_id
            )));
        }

        if let Some(registry) = &self.collaboration_registry {
            registry.collaboration_owner(&[req.from_id.clone(), req.to_id.clone()]).await?;
        }

        // Only pending requests can be approved.
        if req.status == RequestStatus::Approved {
            // Historical approval is not evidence of a current friendship.
            // In particular, a delayed Human mirror retry must not undo revoke.
            if actor_kind_of(&req.from_id) == ActorKind::Human
                && !self.edge_grants.has_friend_edge(&req.from_id, &req.to_id, &self.env).await
            {
                return Ok(vec![]);
            }
            // Idempotent: return existing edge ids (if any). Also backfill the
            // legacy friendship row — pairs approved before the reverse-sync
            // shipped have no bcs_friendships mirror (INSERT is idempotent).
            self.writeback_add_friendship(&req.from_id, &req.to_id).await?;
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
            let reverse_pending = self.find_pending_connect(&to_bot, &caller).await?;
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

        // Reverse-sync (coexistence): mirror the friendship into the
        // legacy store the group gate still reads.
        self.writeback_add_friendship(&caller, &to_bot).await?;

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

        if is_internal_reverse_connect(&req) {
            return Err(ServiceError::Forbidden(format!(
                "request '{}' is a generated reverse mirror and cannot be decided directly",
                req.request_id
            )));
        }

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
                .await?;
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
                .await?;
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

    async fn revoke_friend(
        &self,
        caller: &str,
        target: &str,
        request_auth: Option<RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
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

        // Clear any residual pending Connect requests between the pair. An
        // unfriend leaves those rows undecidable — the friendship they were
        // waiting on is gone — and they block a fresh re-apply by letting the
        // create dedup branch short-circuit. Clear both directions so a pair
        // carries no lingering pending request after revoke.
        for (from, to) in [(caller, target), (target, caller)] {
            for req in self.find_pending_connect(from, to).await? {
                self.requests
                    .decide(
                        req.request_id.as_str(),
                        &self.env,
                        RequestStatus::Cancelled,
                        "system",
                        Some("cancelled by unfriend"),
                    )
                    .await?;
            }
        }

        // Reverse-sync (coexistence): drop the legacy friendship mirror for
        // unfriends so stale rows cannot keep legacy readers open.
        self.writeback_remove_friendship(caller, target).await?;

        Ok(revoked)
    }

    /// Owner-initiated removal (bot friend roster): the same friend-edge
    /// selection as `revoke_friend`, but every candidate transitions through
    /// the atomic conditional revoke — only edges THIS call transitioned
    /// count, so concurrent removals transition exactly once. A non-empty
    /// transition set then emits one `RemovedByOwner` notice (best-effort).
    /// The auth-sync teardown and legacy mirror cleanup run exactly like
    /// `revoke_friend`: the owner-removal orientation keeps the human first
    /// so the human→bot trigger still fires.
    async fn revoke_friend_as_owner(
        &self,
        first: &str,
        second: &str,
        request_auth: Option<RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
        // Collect both directions first, then let the storage layer pick the
        // relation-level winner: the forward edge (first -> second) is the
        // token when present, otherwise the reverse edge. A caller that loses
        // the token transitions nothing and returns an empty set, so two
        // interleaved removals cannot each revoke one direction and both
        // notify (review 4693070938).
        let mut candidates: Vec<u64> = Vec::new();
        if let Some(second_default) = self
            .edge_grants
            .get_default_profile_id(second, &self.env)
            .await
        {
            for g in self
                .edge_grants
                .list_active_grants(first, second, &self.env)
                .await
            {
                if g.grant_ref_id == second_default && g.grant_kind == GrantKind::PermissionProfile
                {
                    candidates.push(g.edge_id);
                }
            }
        }
        if let Some(first_default) = self
            .edge_grants
            .get_default_profile_id(first, &self.env)
            .await
        {
            for g in self
                .edge_grants
                .list_active_grants(second, first, &self.env)
                .await
            {
                if g.grant_ref_id == first_default && g.grant_kind == GrantKind::PermissionProfile
                {
                    candidates.push(g.edge_id);
                }
            }
        }
        let mut revoked: Vec<u64> = Vec::new();
        if let Some((primary, others)) = candidates.split_first() {
            revoked = self
                .edge_grants
                .revoke_relation_if_active(*primary, others, &self.env)
                .await?;
        }

        // 11d: best-effort friend-auth-sync revoke trigger (human→bot), same
        // as `revoke_friend`.
        if actor_kind_of(first) == ActorKind::Human && actor_kind_of(second) == ActorKind::Bot {
            let owner_work_no = self
                .owner_work_no_from_bot_config(second, &self.env)
                .await
                .unwrap_or_default();
            let command = FriendAuthSyncCommand {
                env: self.env.clone(),
                bot_id: second.to_string(),
                owner_work_no,
                human_work_no: first
                    .strip_prefix("human_")
                    .unwrap_or(first)
                    .to_string(),
                action: FriendAuthSyncAction::Revoke,
                request_id: None,
                request_auth: request_auth.clone(),
            };
            if let Err(err) = self.friend_auth_sync.sync(command).await {
                warn!(
                    error = %err,
                    bot_id = %second,
                    "friend-auth-sync revoke failed (best-effort)"
                );
            }
        }

        // Reverse-sync (coexistence): drop the legacy friendship mirror.
        self.writeback_remove_friendship(first, second).await?;

        // No real transition → silent no-op (idempotent re-remove / lost
        // race): a notice would lie about a removal that did not happen.
        if revoked.is_empty() {
            return Ok(revoked);
        }

        // One `RemovedByOwner` notice for the transitioned set, addressed to
        // the removed party (or the removed Bot's CURRENT owner). The
        // applicant actor drives the adapter's HUMAN2BOT/BOT2BOT event-type
        // split (pinned by the bcs crate's payload tests).
        let request_ids: Vec<String> = revoked.iter().map(u64::to_string).collect();
        let notified = match actor_kind_of(first) {
            ActorKind::Human => {
                let staff_no = first.strip_prefix("human_").unwrap_or(first).to_string();
                self.emit_friend_connect_notification(
                    FriendConnectNotificationKind::RemovedByOwner,
                    request_ids.clone(),
                    first,
                    second,
                    vec![staff_no],
                    None,
                    request_auth,
                )
                .await
            }
            ActorKind::Bot => match self.bot_config.get(second, &self.env).await {
                Some(cfg) => {
                    let recipients = self.target_notification_recipients(second, &cfg).await;
                    self.emit_friend_connect_notification(
                        FriendConnectNotificationKind::RemovedByOwner,
                        request_ids.clone(),
                        second,
                        first,
                        recipients,
                        None,
                        request_auth,
                    )
                    .await
                }
                None => {
                    warn!(
                        bot_id = %second,
                        env = %self.env,
                        "RemovedByOwner notice skipped: the removed Bot's config \
                         is unavailable; recipients are unresolvable"
                    );
                    Ok(())
                }
            },
        };
        // Best-effort (Review Focus #4): a notification failure must not roll
        // back the removal — the edges already transitioned.
        if let Err(error) = notified {
            warn!(
                error = %error,
                request_ids = ?request_ids,
                first = %first,
                second = %second,
                "RemovedByOwner notification failed; continuing without it"
            );
        }
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

    async fn find_human_friend_bots(
        &self,
        human_id: &str,
        bot_ids: &[String],
    ) -> ServiceResult<HashSet<String>> {
        self.edge_grants.find_human_friend_bots(human_id, &self.env, bot_ids).await
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
                self.requests.list_inbox(actor, &self.env, status).await?
            }
            RequestDirection::Sent => {
                self.requests.list_sent(actor, &self.env, status).await?
            }
            RequestDirection::All => {
                let inbox = self.requests.list_inbox(actor, &self.env, status).await?;
                let sent = self.requests.list_sent(actor, &self.env, status).await?;
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

        // Hide generated reverse Connect rows before computing totals or
        // slicing the page. They implement the second directed edge, not a
        // second user-facing request from the peer.
        let all: Vec<PermissionRequest> = all
            .into_iter()
            .filter(|request| !is_internal_reverse_connect(request))
            .collect();
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

    async fn find_pending_self_connect(
        &self,
        caller: &str,
        to: &str,
    ) -> ServiceResult<Option<PermissionRequest>> {
        // Bounded existence read (LIMIT 1 at the store): the legacy
        // duplicate-pending guard must not page the caller's whole history.
        Ok(self
            .requests
            .find_request(
                caller,
                to,
                &self.env,
                RequestKind::Connect,
                RequestStatus::Pending,
                caller,
            )
            .await)
    }
}
