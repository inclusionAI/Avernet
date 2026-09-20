use super::*;

pub async fn handle_group_chat(
    flow: &BcsMessageFlow,
    cmd: GroupChatCommand,
) -> ServiceResult<GroupChatOutcome> {
    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    apply_session_participant_scope(flow, &mut group, cmd.session_id.as_deref()).await?;
    verify_group_chat_caller_access(flow, &group, &cmd.caller).await?;
    let sender_id = resolve_group_chat_sender(&cmd)?;
    verify_group_chat_sender(flow, &group, &sender_id, &cmd.caller).await?;
    let from_name = sender_name_for_chat(flow, &cmd.caller, &sender_id).await;

    let outcome = handle_web_send(
        flow,
        WebSendCommand {
            caller: cmd.caller,
            group_id: cmd.group_id.clone(),
            session_id: cmd.session_id,
            from_actor_id: sender_id,
            from_name,
            message: cmd.message,
            mentions: Vec::new(),
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: cmd.provider_bypass_headers,
        },
    )
    .await?;

    Ok(GroupChatOutcome {
        queue_admission: outcome.queue_admission,
        group_id: cmd.group_id,
        driver_bot_id: group.driver_bot,
        delivered_count: outcome.delivered_count,
        failed_count: outcome.failed_count,
        delivery_results: outcome.delivery_results,
        mentions: outcome.mentions,
        hidden_mentions: outcome.hidden_mentions,
    })
}

pub async fn handle_persistent_group_send(
    flow: &BcsMessageFlow,
    cmd: PersistentGroupSendCommand,
) -> ServiceResult<PersistentGroupSendOutcome> {
    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    verify_http_group_message_caller_access(flow, &group, &cmd.caller).await?;
    verify_http_group_message_sender(flow, &group, &cmd.sender, &cmd.caller).await?;
    if group.get_participant(&cmd.sender).is_none() {
        return Err(ServiceError::Unauthorized(format!(
            "sender '{}' is not a participant of group '{}'",
            cmd.sender, cmd.group_id
        )));
    }

    backfill_bot_names(flow.registry.as_ref(), &mut group).await;

    if flow.managed_deliveries.is_some() && group.group_strategy != GroupStrategy::StateMachine && cmd.message_type == GroupMessageType::Bot {
        let preview = if group.group_kind == GroupKind::Dm {
            let overlay = build_route_overlay(flow, &group).await;
            flow.routing.route_dm_with_overlay(&group, &cmd.content, &cmd.sender, &overlay).await
        } else { flow.routing.route(&group, &cmd.content, Some(&cmd.sender)).await };
        crate::queued_admission::guard_legacy_targets(flow, &preview.targets, None).await?;
    }

    if group.status != GroupStatus::Active {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "Group '{}' is not active (status: {:?})",
                cmd.group_id, group.status
            ),
            request_id: None,
        });
    }

    if cmd.max_group_messages > 0 {
        let count = flow.group.message_count(&cmd.group_id).await?;
        if count >= cmd.max_group_messages as usize {
            crate::update_group_status(
                flow.group.as_ref(),
                &cmd.group_id,
                GroupStatus::Inactive,
                "message_limit_reached",
                crate::caller_event_actor(&cmd.caller),
            )
            .await?;
            return Err(ServiceError::MessageLimitReached(format!(
                "Group '{}' already has {} messages (max {})",
                cmd.group_id, count, cmd.max_group_messages
            )));
        }
    }

    if group.group_strategy != GroupStrategy::StateMachine
        && crate::queued_admission::manages_any(flow, &group).await
        && cmd.message_type == GroupMessageType::Bot
    {
        let outcome = handle_web_send(flow, WebSendCommand {
            caller: cmd.caller.clone(), group_id: cmd.group_id.clone(), session_id: None,
            from_actor_id: cmd.sender.clone(), from_name: None, message: cmd.content.clone(),
            mentions: Vec::new(), attachments: None, thinking: None, idempotency_key: None,
            source_im_message_id: None, channel_sender_identity: None, sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        }).await?;
        let message_id = outcome.queue_admission.as_ref().map(|a| a.message_id.clone())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        flow.group.increment_message_count(&cmd.group_id).await?;
        if cmd.store_messages {
            flow.group.add_message(&cmd.group_id, GroupMessage {
                id: message_id.clone(), timestamp: now_ms(), sender: cmd.sender, content: cmd.content,
                message_type: cmd.message_type, bot_name: None, role: cmd.role, run_id: outcome.primary_run_id,
                history_meta: None, metadata: None, attachments: None,
            }).await?;
        }
        let mut routed_to: Vec<_> = outcome.delivery_results.iter().filter(|r| r.success).map(|r| r.bot_uuid.clone()).collect();
        if let Some(admission) = &outcome.queue_admission {
            routed_to.extend(admission.deliveries.iter().filter(|d| !matches!(d.status, bcs_domain::message_delivery::MessageDeliveryStatus::RejectedCapacity | bcs_domain::message_delivery::MessageDeliveryStatus::Failed)).map(|d| d.target_bot_id.clone()));
        }
        routed_to.sort(); routed_to.dedup();
        return Ok(PersistentGroupSendOutcome { queue_admission: outcome.queue_admission, message_id, routed_to, mentions: outcome.mentions });
    }
    let _ = flow.group.increment_message_count(&cmd.group_id).await;

    let message = GroupMessage {
        id: uuid::Uuid::new_v4().to_string(),
        timestamp: now_ms(),
        sender: cmd.sender.clone(),
        content: cmd.content.clone(),
        message_type: cmd.message_type,
        bot_name: None,
        role: cmd.role,
        run_id: String::new(),
        history_meta: None,
        metadata: None,
        attachments: None,
    };

    let sender_type = if cmd.sender.starts_with("human_") {
        SenderType::Human
    } else {
        SenderType::Bot
    };
    try_persist_group_message(
        flow,
        &cmd.group_id,
        None,
        &cmd.sender,
        sender_type,
        "chat",
        Value::String(cmd.content.clone()),
        None,
        None,
        "", // run_id: persistent send
    )
    .await?;

    let overlay = build_route_overlay(flow, &group).await;
    let decision = if group.group_kind == GroupKind::Dm {
        flow.routing
            .route_dm_with_overlay(&group, &message.content, message.sender.as_str(), &overlay)
            .await
    } else {
        flow.routing
            .route(&group, &message.content, Some(message.sender.as_str()))
            .await
    };

    let sender_display_name = sender_display_name(flow, &cmd.sender).await;
    let from_bot_owner = from_bot_owner(flow, &cmd.sender).await;
    // Notify @-mentioned humans (non-DM only) once the message is persisted
    // and the content passes the same outbound policy as bot deliveries.
    if group.group_kind != GroupKind::Dm {
        if let Some(notify_text) = apply_notify_outbound_policy(
            flow,
            &cmd.group_id,
            &cmd.sender,
            &decision.cleaned_message,
            &decision.targets,
        )
        .await
        {
            crate::human_notify_hook::spawn_human_mention_notify(
                &flow.human_mention_notify,
                &flow.session_management,
                Some(decision.mentions.as_slice()),
                &overlay,
                crate::human_notify_hook::MentionNotifyContext {
                    session_id: String::new(),
                    group_id: cmd.group_id.clone(),
                    group_name: group.label.clone(),
                    sender_actor_id: cmd.sender.clone(),
                    sender_label: sender_display_name.clone(),
                    message_text: notify_text,
                    timestamp_ms: now_ms(),
                },
            );
        }
    }

    let mut routed_to = Vec::new();
    let synthetic_send = WebSendCommand {
        caller: cmd.caller.clone(),
        group_id: cmd.group_id.clone(),
        session_id: None,
        from_actor_id: cmd.sender.clone(),
        from_name: Some(sender_display_name.clone()),
        message: cmd.content.clone(),
        mentions: decision.mentions.clone(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    };
    for target in &decision.targets {
        let outbound =
            match apply_outbound_interceptors(flow, &cmd.group_id, &message, target).await {
                Ok(message) => message,
                Err(reason) => {
                    warn!(
                        session_id = %cmd.group_id,
                        bot_uuid = %target.bot_uuid,
                        interceptor = %reason.interceptor_id,
                        code = %reason.code,
                        "outbound message blocked by interceptor"
                    );
                    continue;
                }
            };
        routed_to.push(target.bot_uuid.clone());

        let run_id = uuid::Uuid::new_v4().to_string();
        let delivery_target = match flow
            .registry
            .resolve_delivery_target(&target.bot_uuid)
            .await
        {
            Ok(delivery_target) => delivery_target,
            Err(error) => {
                warn!(
                    session_id = %cmd.group_id,
                    bot_uuid = %target.bot_uuid,
                    error = %error,
                    "Failed to resolve delivery target"
                );
                continue;
            }
        };
        let frame = frame_for_target(
            flow,
            &group,
            &synthetic_send,
            &decision,
            target,
            &delivery_target,
            &run_id,
            &outbound.content,
            &sender_display_name,
            from_bot_owner.clone(),
        )
        .await;
        let delivery_kind = bot_delivery_kind(target.delivery_type);
        flow.register_send_context(
            target.delivery_type,
            &delivery_target,
            &frame,
            &run_id,
            &target.bot_uuid,
            &cmd.group_id,
            None,
            &[],
        )
        .await?;
        let result = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            })
            .await;
        match result {
            Ok(result) => {
                if !result.delivered {
                    flow.discard_send_context(&run_id).await?;
                    warn!(
                        session_id = %cmd.group_id,
                        bot_uuid = %target.bot_uuid,
                        error = ?result.error,
                        "Failed to send message to bot"
                    );
                }
            }
            Err(error) => {
                flow.discard_send_context(&run_id).await?;
                warn!(
                    session_id = %cmd.group_id,
                    bot_uuid = %target.bot_uuid,
                    error = %error,
                    "Failed to send message to bot"
                );
            }
        }
    }

    if cmd.store_messages {
        flow.group
            .add_message(&cmd.group_id, message.clone())
            .await?;
    }

    Ok(PersistentGroupSendOutcome {
        queue_admission: None,
        message_id: message.id,
        routed_to,
        mentions: decision.mentions,
    })
}
