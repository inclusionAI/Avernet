use super::*;

impl BcsChannelService {
    pub(super) async fn start_state_machine_from_inbound(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<(), ChannelUseCaseError> {
        let guard = self.state_machine_session_resolution_lock.lock().await;
        let current = self
            .conversations
            .get(
                &ctx.binding_id,
                &msg.im_conversation_id,
                ctx.session_scope,
                ctx.im_user_id.as_deref(),
            )
            .await?;
        if let Some(mapping) = current.as_ref().filter(|_| !ctx.new_session_per_message) {
            if let Some(view) = self
                .collaboration_runtime
                .get_state_machine_run_by_session_id(&mapping.bcs_session_id)
                .await
                .map_err(|error| {
                    ChannelUseCaseError::Internal(ServiceError::InternalError(error.to_string()))
                })?
            {
                if view.run.status == bcs_domain::StateMachineRunStatus::Running
                    && view.run.group_id == ctx.group_id
                {
                    drop(guard);
                    return self
                        .continue_state_machine_from_inbound(
                            ctx,
                            msg,
                            actor_id,
                            &view.run.run_id,
                            &view.run.session_id,
                        )
                        .await;
                }
            } else if (self.now_ms)().saturating_sub(mapping.last_active_at)
                < CHANNEL_START_STALE_MS
            {
                let session_id = mapping.bcs_session_id.clone();
                drop(guard);
                self.send_state_machine_system(
                    ctx,
                    &session_id,
                    "",
                    "流程正在启动，请稍后再试。",
                    Some(&msg.msg_id),
                )
                .await?;
                return Ok(());
            }
        }
        let input = serde_json::json!({
            "source": msg.channel_type,
            "text": msg.text,
            "sender": {
                "staff_id": msg.im_user_id.trim(),
                "name": msg.im_user_nick,
                "actor_id": actor_id,
            },
            "conversation": {
                "id": msg.im_conversation_id,
                "type": msg.conversation_type,
            },
            "scope": match ctx.session_scope {
                SessionScope::Conversation => "conversation",
                SessionScope::PerSender => "per_sender",
            },
        });
        let group = self
            .groups
            .get(&ctx.group_id)
            .await
            .ok_or_else(|| ChannelUseCaseError::NotFound(ctx.group_id.clone()))?;
        let mut participants = group
            .participants
            .into_iter()
            .filter(|participant| participant.is_bot())
            .collect::<Vec<_>>();
        let mut human = Participant::human(actor_id.to_string(), ParticipantRole::Observer);
        human.mode = Some(ParticipantMode::Present);
        human.bot_name = msg.im_user_nick.clone();
        participants.push(human);
        let session = self
            .sessions
            .create_channel(
                &ctx.group_id,
                &msg.channel_type,
                NewSessionParams {
                    session_kind: SessionKind::ServiceInvocation,
                    caller_id: Some(actor_id.to_string()),
                    caller_principal: Some(ctx.caller_principal.clone()),
                    input: Some(input.clone()),
                    session_title: msg.im_user_nick.clone(),
                    meta: Some(channel_meta(ctx, msg)),
                    participants,
                    ..Default::default()
                },
            )
            .await?;
        self.conversations
            .upsert(ConversationSessionMap {
                binding_id: ctx.binding_id.clone(),
                im_conversation_id: msg.im_conversation_id.clone(),
                im_conversation_type: msg.conversation_type.clone(),
                session_scope: ctx.session_scope,
                im_user_id: ctx.im_user_id.clone(),
                bcs_session_id: session.id.clone(),
                last_active_at: (self.now_ms)(),
            })
            .await?;
        let session_id = session.id.clone();
        let start_result = self
            .collaboration_runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: ctx.group_id.clone(),
                session_id: Some(session_id.clone()),
                definition_yaml: None,
                definition: None,
                definition_ref: None,
                participant_bindings: None,
                opening_message_override: None,
                input,
                caller_id: Some(actor_id.to_string()),
                authenticated_human: Some(AuthenticatedHumanCaller {
                    actor_id: actor_id.to_string(),
                    display_name: msg.im_user_nick.clone(),
                }),
            })
            .await;
        if let Err(error) = start_result {
            if let Err(cleanup_error) = self
                .sessions
                .complete_if_running(
                    &session_id,
                    None,
                    Some("state_machine_start_failed".to_string()),
                )
                .await
            {
                warn!(
                    session_id = %session_id,
                    error = %cleanup_error,
                    "channel state-machine start: failed to complete orphan session"
                );
            }
            if let Err(cleanup_error) = self
                .conversations
                .delete_if_session(
                    &ctx.binding_id,
                    &msg.im_conversation_id,
                    ctx.session_scope,
                    ctx.im_user_id.as_deref(),
                    &session_id,
                )
                .await
            {
                warn!(
                    session_id = %session_id,
                    error = %cleanup_error,
                    "channel state-machine start: failed to remove orphan conversation mapping"
                );
            }
            drop(guard);
            return Err(ChannelUseCaseError::Internal(ServiceError::InternalError(
                error.to_string(),
            )));
        }
        drop(guard);
        Ok(())
    }

    pub(super) async fn continue_state_machine_from_inbound(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
        _actor_id: &str,
        run_id: &str,
        session_id: &str,
    ) -> Result<(), ChannelUseCaseError> {
        // HumanInput replies are selected exclusively through the persisted
        // active request before normal state-machine conversation resolution.
        // Never guess a node from a session's pending-node list and never grant
        // session participation merely because someone sent an IM message.
        self.send_state_machine_system(
            ctx,
            session_id,
            run_id,
            "流程当前没有可通过此会话回复的人工输入请求，消息未被接收。",
            Some(&msg.msg_id),
        )
        .await
    }

    pub(super) async fn send_state_machine_system(
        &self,
        ctx: &ResolvedInboundContext,
        session_id: &str,
        run_id: &str,
        text: &str,
        source_im_message_id: Option<&str>,
    ) -> Result<(), ChannelUseCaseError> {
        self.try_outbound(OutboundMessage {
            group_id: ctx.group_id.clone(),
            bcs_session_id: session_id.to_string(),
            run_id: run_id.to_string(),
            sender_actor_id: "bcs_state_machine".to_string(),
            sender_role: ParticipantRole::Driver,
            sender_label: "BCS State Machine".to_string(),
            kind: ChannelOutboundEventKind::System,
            purpose: ChannelOutboundPurpose::HumanInputAck,
            text: Some(text.to_string()),
            raw_payload: serde_json::json!({
                "type": "state_machine.system",
                "run_id": run_id,
            }),
            render_hint: ChannelRenderHint::Render,
            source_im_message_id: source_im_message_id.map(str::to_string),
            source_is_channel: false,
        })
        .await
    }

}
