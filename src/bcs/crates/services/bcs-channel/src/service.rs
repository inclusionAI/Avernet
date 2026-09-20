use super::*;

#[async_trait]
impl ChannelService for BcsChannelService {
    async fn handle_inbound(&self, mut msg: InboundMessage) -> Result<(), ChannelInboundError> {
        msg.conversation_type = normalize_required(&msg.conversation_type, "conversation_type")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        if msg.conversation_type == "2" && !msg.is_at_bot {
            info!(
                channel_type = %msg.channel_type,
                account_ref = %msg.account_ref,
                msg_id = %msg.msg_id,
                im_conversation_id = %msg.im_conversation_id,
                conversation_type = %msg.conversation_type,
                reason = "group_message_without_at_bot",
                "channel inbound: ignored"
            );
            return Ok(());
        }
        msg.msg_id = normalize_required(&msg.msg_id, "msg_id")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.channel_type = normalize_required(&msg.channel_type, "channel_type")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.account_ref = normalize_required(&msg.account_ref, "account_ref")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.im_conversation_id = normalize_required(&msg.im_conversation_id, "im_conversation_id")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.im_user_id = normalize_required(&msg.im_user_id, "im_user_id")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        validate_inbound_content(&msg)?;
        let account_ref = msg.account_ref.clone();
        info!(
            channel_type = %msg.channel_type,
            account_ref = %account_ref,
            msg_id = %msg.msg_id,
            im_conversation_id = %msg.im_conversation_id,
            conversation_type = %msg.conversation_type,
            im_user_id = %msg.im_user_id,
            is_at_bot = msg.is_at_bot,
            text_len = msg.text.chars().count(),
            "channel inbound: received"
        );
        let Some(binding) = self
            .bindings
            .find_active_by_account(msg.channel_type.clone(), &account_ref)
            .await
            .map_err(|error| {
                inbound_failure(ChannelInboundFailureKind::BindingLookupFailed, true, error)
            })?
        else {
            return Err(ChannelInboundError::new(
                ChannelInboundFailureKind::BindingNotFound,
                false,
                format!(
                    "active binding not found for channel {} account {}",
                    msg.channel_type, account_ref
                ),
            ));
        };
        info!(
            channel_type = %binding.channel_type,
            account_ref = %binding.account_ref,
            binding_id = %binding.id,
            msg_id = %msg.msg_id,
            target_kind = binding_target_kind(&binding.target),
            "channel inbound: binding resolved"
        );
        if has_temporary_file_attachment(msg.attachments.as_deref())
            && msg.channel_type == "dingtalk"
            && msg.conversation_type == "2"
            && binding.group_chat_scope != Some(GroupChatScope::PerSender)
        {
            return Err(ChannelInboundError::new(
                ChannelInboundFailureKind::UnsupportedAttachment,
                false,
                "DingTalk file attachments require a direct or per-sender session",
            ));
        }
        let dedup_key = inbound_dedup_key(&msg.channel_type, &account_ref, &msg.msg_id);
        if let Some(key) = dedup_key.as_deref() {
            if !self.inbound_dedup.claim(key).await {
                info!(
                    channel_type = %msg.channel_type,
                    account_ref = %account_ref,
                    msg_id = %msg.msg_id,
                    reason = "duplicate_msg_id",
                    "channel inbound: ignored"
                );
                return Ok(());
            }
        }

        let result = async {
            let actor_id = self.ensure_im_human_actor(&msg).await.map_err(|error| {
                inbound_failure(
                    ChannelInboundFailureKind::ActorResolutionFailed,
                    true,
                    error,
                )
            })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                msg_id = %msg.msg_id,
                im_user_id = %msg.im_user_id,
                actor_id = %actor_id,
                "channel inbound: actor resolved"
            );
            if self
                .try_execute_channel_command(&binding, &msg, &actor_id)
                .await
                .map_err(|error| {
                    inbound_failure(ChannelInboundFailureKind::DispatchFailed, true, error)
                })?
                .is_some()
            {
                return Ok(());
            }
            if self
                .try_consume_human_input(&binding, &msg, &actor_id)
                .await
                .map_err(|error| {
                    inbound_failure(ChannelInboundFailureKind::DispatchFailed, true, error)
                })?
            {
                info!(
                    channel_type = %msg.channel_type,
                    account_ref = %account_ref,
                    binding_id = %binding.id,
                    msg_id = %msg.msg_id,
                    actor_id = %actor_id,
                    "channel inbound: HumanInput consumed"
                );
                return Ok(());
            }
            let ctx = self
                .resolve_inbound_context(&binding, &msg, &actor_id)
                .await
                .map_err(|error| {
                    inbound_failure(
                        ChannelInboundFailureKind::ContextResolutionFailed,
                        false,
                        error,
                    )
                })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %msg.msg_id,
                group_id = %ctx.group_id,
                session_scope = session_scope_label(ctx.session_scope),
                context_projection = ctx.context_projection,
                state_machine_trigger = ctx.state_machine_trigger,
                "channel inbound: context resolved"
            );
            self.maybe_execute_stale_reset(&ctx, &msg).await;

            if ctx.state_machine_trigger {
                return self
                    .start_state_machine_from_inbound(&ctx, &msg, &actor_id)
                    .await
                    .map_err(|error| {
                        inbound_failure(ChannelInboundFailureKind::DispatchFailed, true, error)
                    });
            }

            // /new 归档旧会话后，并发消息可能同时看到 Completed 状态；
            // 锁内完成"解析或创建 + 映射重指"，保证只建一个新会话。
            let (session_id, reused_session) = {
                let _guard = self.chat_session_resolution_lock.lock().await;
                let resolved = self
                    .resolve_or_create_chat_session(&ctx, &msg)
                    .await
                    .map_err(|error| {
                        inbound_failure(
                            ChannelInboundFailureKind::SessionResolutionFailed,
                            true,
                            error,
                        )
                    })?;
                self.conversations
                    .upsert(ConversationSessionMap {
                        binding_id: binding.id.clone(),
                        im_conversation_id: msg.im_conversation_id.clone(),
                        im_conversation_type: msg.conversation_type.clone(),
                        session_scope: ctx.session_scope,
                        im_user_id: ctx.im_user_id.clone(),
                        bcs_session_id: resolved.0.clone(),
                        last_active_at: (self.now_ms)(),
                    })
                    .await
                    .map_err(|error| {
                        inbound_failure(
                            ChannelInboundFailureKind::SessionResolutionFailed,
                            true,
                            error,
                        )
                    })?;
                resolved
            };
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %msg.msg_id,
                group_id = %ctx.group_id,
                bcs_session_id = %session_id,
                reused = reused_session,
                session_scope = session_scope_label(ctx.session_scope),
                "channel inbound: session resolved"
            );
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %msg.msg_id,
                bcs_session_id = %session_id,
                im_conversation_id = %msg.im_conversation_id,
                "channel inbound: conversation mapped"
            );

            let mut participant = Participant::human(actor_id.clone(), ParticipantRole::Consultant);
            participant.mode = Some(ParticipantMode::Present);
            participant.bot_name = msg.im_user_nick.clone();
            self.sessions
                .add_participant(&session_id, participant)
                .await
                .map_err(|error| {
                    inbound_failure(
                        ChannelInboundFailureKind::SessionResolutionFailed,
                        true,
                        error,
                    )
                })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                msg_id = %msg.msg_id,
                bcs_session_id = %session_id,
                actor_id = %actor_id,
                "channel inbound: participant added"
            );

            let dispatch_group_id = ctx.group_id.clone();
            let dispatch_session_id = session_id.clone();
            let dispatch_actor_id = actor_id.clone();
            let dispatch_msg_id = msg.msg_id.clone();
            let caller = CallerContext::Human(HumanActor {
                actor_id: actor_id.clone(),
                staff_no: msg.im_user_id.trim().to_string(),
            });
            let channel_sender_identity = channel_sender_identity(&binding, &msg, &caller);
            let outcome = self
                .message_flow
                .handle_web_send(WebSendCommand {
                    caller,
                    group_id: dispatch_group_id.clone(),
                    session_id: Some(dispatch_session_id.clone()),
                    from_actor_id: dispatch_actor_id.clone(),
                    from_name: msg.im_user_nick,
                    message: msg.text,
                    mentions: Vec::new(),
                    attachments: msg.attachments,
                    thinking: None,
                    idempotency_key: Some(dispatch_msg_id.clone()),
                    source_im_message_id: Some(dispatch_msg_id.clone()),
                    channel_sender_identity,
                    sender_conn_id: None,
                    provider_bypass_headers: Vec::new(),
                })
                .await
                .map_err(|error| {
                    inbound_failure(ChannelInboundFailureKind::DispatchFailed, true, error)
                })?;
            if outcome.active_run_ids.is_empty() && outcome.failed_count > 0 {
                return Err(ChannelInboundError::new(
                    ChannelInboundFailureKind::DispatchFailed,
                    true,
                    format!(
                        "message flow reported {} failed deliveries without an active run",
                        outcome.failed_count
                    ),
                ));
            }
            self.session_reset_tracker
                .seed_runs(&dispatch_session_id, &outcome.active_run_ids)
                .await;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %dispatch_msg_id,
                group_id = %dispatch_group_id,
                bcs_session_id = %dispatch_session_id,
                actor_id = %dispatch_actor_id,
                "channel inbound: dispatched"
            );
            Ok(())
        }
        .await;
        if result.is_err() {
            if let Some(key) = dedup_key.as_deref() {
                self.inbound_dedup.forget(key).await;
            }
        }
        result
    }

    async fn try_outbound(&self, msg: OutboundMessage) -> Result<(), ChannelUseCaseError> {
        if msg.source_is_channel {
            return Ok(());
        }
        self.observe_outbound_terminal(&msg).await;
        let require_confirmed_delivery = msg
            .raw_payload
            .get("type")
            .and_then(serde_json::Value::as_str)
            == Some("state_machine.human_input_ready");
        let mut confirmed_deliveries = 0usize;
        let Some(group) = self.groups.get(&msg.group_id).await else {
            return Ok(());
        };
        let Some(session) = self.sessions.get(&msg.bcs_session_id).await else {
            return Ok(());
        };
        if session.group_id != msg.group_id {
            return Ok(());
        }
        let mut conversations = self
            .conversations
            .list_by_bcs_session(&msg.bcs_session_id)
            .await?;
        if let Some(conv) = self.channel_route_from_session_meta(&session) {
            if !conversations
                .iter()
                .any(|existing| existing.binding_id == conv.binding_id)
            {
                conversations.push(conv);
            }
        }
        let mut seen_bindings = HashSet::new();
        for conv in conversations {
            if !seen_bindings.insert(conv.binding_id.clone()) {
                continue;
            }
            let Some(binding) = self.bindings.get(&conv.binding_id).await? else {
                continue;
            };
            if binding.status != BindingStatus::Active {
                continue;
            }
            if !binding_relevant_to_group(&binding, &msg.group_id, &session) {
                continue;
            }
            if msg.kind != ChannelOutboundEventKind::System
                && !visibility_allows(
                    group.group_strategy,
                    binding.outbound_visibility,
                    msg.sender_role,
                )
            {
                continue;
            }
            let binding_ref = ChannelBindingRef {
                channel_type: binding.channel_type.clone(),
                account_ref: binding.account_ref.clone(),
            };
            let Some(provider) = self.providers.get(&binding.channel_type) else {
                warn!(
                    channel_type = %binding.channel_type,
                    binding_id = %binding.id,
                    "channel outbound: provider is not registered"
                );
                continue;
            };
            let delivery = provider.delivery();
            if !delivery.is_available(&binding_ref).await {
                info!(
                    binding_id = %binding.id,
                    channel_type = %binding.channel_type,
                    account_ref = %binding.account_ref,
                    bcs_session_id = %msg.bcs_session_id,
                    run_id = %msg.run_id,
                    reason = "delivery_unavailable",
                    "channel outbound: skipped"
                );
                continue;
            }
            let im_user_display_name = match conv.im_user_id.as_deref() {
                Some(user_id) => self
                    .im_participants
                    .get(binding.channel_type.clone(), &binding.account_ref, user_id)
                    .await?
                    .and_then(|participant| participant.display_name),
                None => None,
            };
            let im_conversation_id = conv.im_conversation_id.clone();
            let im_conversation_type = conv.im_conversation_type.clone();
            let im_user_id = conv.im_user_id.clone();
            info!(
                binding_id = %binding.id,
                channel_type = %binding.channel_type,
                account_ref = %binding.account_ref,
                bcs_session_id = %msg.bcs_session_id,
                run_id = %msg.run_id,
                im_conversation_id = %im_conversation_id,
                conversation_type = %im_conversation_type,
                im_user_id = im_user_id.as_deref().unwrap_or(""),
                event_kind = ?msg.kind,
                text_len = msg.text.as_deref().map(|text| text.chars().count()).unwrap_or(0),
                "channel outbound: selected"
            );
            let result = match delivery
                .deliver_event(ChannelOutboundEvent {
                    binding_ref,
                    im_conversation_id,
                    im_conversation_type,
                    im_user_id,
                    im_user_display_name,
                    bcs_session_id: msg.bcs_session_id.clone(),
                    run_id: msg.run_id.clone(),
                    sender_actor_id: msg.sender_actor_id.clone(),
                    sender_label: msg.sender_label.clone(),
                    render_sender_label: matches!(binding.target, BindingTarget::Group { .. })
                        && binding.outbound_visibility == Visibility::FullTranscript,
                    sender_role: msg.sender_role,
                    kind: msg.kind,
                    purpose: msg.purpose,
                    text: msg.text.clone(),
                    raw_payload: msg.raw_payload.clone(),
                    render_hint: msg.render_hint,
                    source_im_message_id: msg.source_im_message_id.clone(),
                })
                .await
            {
                Ok(result) => result,
                Err(error) => {
                    warn!(
                        binding_id = %binding.id,
                        channel_type = %binding.channel_type,
                        account_ref = %binding.account_ref,
                        bcs_session_id = %msg.bcs_session_id,
                        run_id = %msg.run_id,
                        error = %error,
                        "channel outbound: delivery call failed"
                    );
                    continue;
                }
            };
            if !result.delivered {
                let delivery_error = result.error.as_ref().map(ToString::to_string);
                warn!(
                    binding_id = %binding.id,
                    channel_type = %binding.channel_type,
                    account_ref = %binding.account_ref,
                    bcs_session_id = %msg.bcs_session_id,
                    run_id = %msg.run_id,
                    error = delivery_error.as_deref().unwrap_or(""),
                    "channel outbound: delivery not confirmed"
                );
            } else {
                confirmed_deliveries += 1;
                info!(
                    binding_id = %binding.id,
                    channel_type = %binding.channel_type,
                    account_ref = %binding.account_ref,
                    bcs_session_id = %msg.bcs_session_id,
                    run_id = %msg.run_id,
                    "channel outbound: delivered"
                );
            }
        }
        if require_confirmed_delivery && confirmed_deliveries == 0 {
            return Err(ChannelUseCaseError::Internal(ServiceError::InternalError(
                "HumanInput ready event had no confirmed channel delivery".to_string(),
            )));
        }
        Ok(())
    }

    async fn create_binding(
        &self,
        cmd: CreateBindingCommand,
    ) -> Result<ChannelBinding, ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let account_ref = normalize_required(&cmd.account_ref, "account_ref")?.to_string();
        if self.env.is_empty() {
            return Err(ChannelUseCaseError::Internal(ServiceError::InternalError(
                "server environment configuration 'env' is empty".to_string(),
            )));
        }
        let env = self.env.clone();
        let target = validate_target(&*self.groups, &*self.registry, &cmd).await?;
        let group_chat_scope = match (&target, cmd.group_chat_scope) {
            (BindingTarget::Bot { .. }, None) => Some(GroupChatScope::PerSender),
            (_, scope) => scope,
        };
        let provider = self.provider_for(&cmd.channel_type)?;
        provider
            .validate_config(&cmd.config)
            .map_err(provider_error)?;
        validate_group_chat_session_config(&cmd.config)?;
        validate_forward_sender_identity_config(&target, &cmd.config)?;
        let binding_id = (self.new_id)();
        if matches!(&target, BindingTarget::Bot { .. }) {
            channel_owned_group_id(&cmd.channel_type, GroupKind::Dm, &binding_id)?;
        }
        if self
            .bindings
            .find_active_by_account(cmd.channel_type.clone(), &account_ref)
            .await?
            .is_some()
        {
            return Err(ChannelUseCaseError::Conflict(format!(
                "active binding already exists for account_ref {account_ref}"
            )));
        }

        let binding = ChannelBinding {
            id: binding_id,
            channel_type: cmd.channel_type,
            account_ref,
            target,
            group_chat_scope,
            outbound_visibility: cmd.outbound_visibility,
            env,
            status: BindingStatus::Active,
            created_by: cmd.created_by,
            config: cmd.config,
        };
        self.bindings.create(binding.clone()).await?;
        let mut redacted = binding;
        redacted.config = provider.redact_config(&redacted.config);
        Ok(redacted)
    }

    async fn list_bindings(&self) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        let bindings = self.bindings.list().await?;
        self.redact_bindings(bindings)
    }

    async fn list_bindings_by_target(
        &self,
        target: BindingTarget,
        channel_type: Option<ChannelType>,
    ) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        let bindings = self
            .bindings
            .list_by_target(&target, channel_type.as_deref())
            .await?;
        self.redact_bindings(bindings)
    }

    async fn list_conversations_by_session(
        &self,
        bcs_session_id: &str,
        channel_type: Option<ChannelType>,
    ) -> Result<Vec<ConversationSessionMap>, ChannelUseCaseError> {
        let bcs_session_id = normalize_required(bcs_session_id, "bcs_session_id")?;
        let channel_type = channel_type
            .as_deref()
            .map(|value| normalize_required(value, "channel_type"))
            .transpose()?;
        let mappings = self
            .conversations
            .list_by_bcs_session(bcs_session_id)
            .await?;
        // Rollover replaces the live mapping, but the old session retains its source.
        if mappings.is_empty() {
            let Some(session) = self.sessions.try_get(bcs_session_id).await? else {
                return Ok(Vec::new());
            };
            let source = session.meta.as_ref()
                .and_then(|meta| meta.get("channel"))
                .and_then(|channel| channel.get("source"))
                .and_then(|value| value.as_str())
                .filter(|source| !source.trim().is_empty());
            let Some(source) = source else {
                return Ok(Vec::new());
            };
            if channel_type.is_some_and(|expected| source != expected) {
                return Ok(Vec::new());
            }
            return Ok(self.channel_route_from_session_meta(&session)
                .filter(|mapping| {
                    !mapping.binding_id.trim().is_empty()
                        && !mapping.im_conversation_id.trim().is_empty()
                })
                .into_iter()
                .collect());
        }
        let mut filtered = Vec::with_capacity(mappings.len());
        for mapping in mappings {
            let Some(binding) = self.bindings.get(&mapping.binding_id).await? else {
                continue;
            };
            if channel_type.is_none_or(|expected| binding.channel_type == expected) {
                filtered.push(mapping);
            }
        }
        Ok(filtered)
    }

    async fn set_binding_status(&self, id: &str, active: bool) -> Result<(), ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let Some(binding) = self.bindings.get(id).await? else {
            return Err(ChannelUseCaseError::NotFound(id.to_string()));
        };
        if active {
            if let Some(existing) = self
                .bindings
                .find_active_by_account(binding.channel_type.clone(), &binding.account_ref)
                .await?
            {
                if existing.id != id {
                    return Err(ChannelUseCaseError::InvalidParams(format!(
                        "active binding already exists for account_ref {}",
                        binding.account_ref
                    )));
                }
            }
        }
        self.bindings.set_status(id, active).await?;
        Ok(())
    }

    async fn update_binding_config(
        &self,
        id: &str,
        config: serde_json::Value,
    ) -> Result<(), ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let Some(binding) = self.bindings.get(id).await? else {
            return Err(ChannelUseCaseError::NotFound(id.to_string()));
        };
        let provider = self.provider_for(&binding.channel_type)?;
        provider.validate_config(&config).map_err(provider_error)?;
        validate_group_chat_session_config(&config)?;
        validate_forward_sender_identity_config(&binding.target, &config)?;
        self.bindings.set_config(id, config).await?;
        Ok(())
    }

    async fn delete_binding(&self, id: &str) -> Result<(), ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let Some(binding) = self.bindings.get(id).await? else {
            return Err(ChannelUseCaseError::NotFound(id.to_string()));
        };
        self.delete_binding_locked(&binding).await?;
        Ok(())
    }
}
