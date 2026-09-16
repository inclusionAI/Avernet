use super::*;

pub(super) struct RelayOutcome {
    pub(super) bot_deliveries: Vec<BotDeliveryResult>,
    pub(super) frontend_deliveries: Vec<FrontendDeliveryResult>,
    pub(super) mentions: Vec<String>,
    pub(super) delivery_results: Vec<MessageDeliveryResult>,
}

pub(super) async fn relay_final_chat_event(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    normalized: Option<&crate::run_reply::RunReply>,
) -> ServiceResult<RelayOutcome> {
    // A2A direct chat has no group context; skip the broadcast-to-group leg
    // silently. Without this, every A2A→provider final would log a
    // "bot relay skipped: group not found" warn even though there is nothing
    // wrong.
    if cmd.group_id.is_empty() {
        return Ok(RelayOutcome {
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: Vec::new(),
            delivery_results: Vec::new(),
        });
    }

    if flow.bot_relay_turn_limit > 0 {
        let count = flow.group.message_count(&cmd.group_id).await.unwrap_or(0);
        if count >= flow.bot_relay_turn_limit as usize {
            crate::update_group_status(
                flow.group.as_ref(),
                &cmd.group_id,
                GroupStatus::Inactive,
                "bot_relay_turn_limit_reached",
                crate::bot_event_actor(&cmd.bot_id),
            )
            .await?;
            let frontend = publish_system_event(
                flow,
                &cmd.group_id,
                "system",
                &format!(
                    "[system] 群聊消息数量已达上限 ({}/{}), 请发送新消息继续讨论",
                    count, flow.bot_relay_turn_limit
                ),
            )
            .await?;
            return Ok(RelayOutcome {
                bot_deliveries: Vec::new(),
                frontend_deliveries: vec![frontend],
                mentions: Vec::new(),
                delivery_results: Vec::new(),
            });
        }
    }

    let mut group = match flow.group.get(&cmd.group_id).await {
        Some(group) => group,
        None => {
            warn!(
                group_id = %cmd.group_id,
                sender = %cmd.bot_id,
                "bot relay skipped: group not found"
            );
            return Ok(RelayOutcome {
                bot_deliveries: Vec::new(),
                frontend_deliveries: Vec::new(),
                mentions: Vec::new(),
                delivery_results: Vec::new(),
            });
        }
    };

    // Preserve legacy group routing when no usable session membership exists.
    if let (Some(sessions), Some(session_id)) = (&flow.session_management, cmd.bcs_session_id.as_deref()) {
        if let Ok(Some(session)) = sessions.get(session_id).await {
            if !session.participants.is_empty() {
                group.participants = session.participants;
            }
        }
    }
    backfill_bot_names(flow.registry.as_ref(), &mut group).await;

    // Unmanaged events have already been published. Only their queue path
    // depends on membership and reconstruction reads.
    let reconstructed;
    let normalized = if normalized.is_none()
        && crate::queued_admission::needs_reply(flow, &group).await {
        reconstructed = crate::run_reply::prepare(flow, cmd, &extract_message_text(&cmd.event_payload), false).await?;
        Some(&reconstructed)
    } else { normalized };

    let raw_text = extract_message_text(&cmd.event_payload);
    // An empty final can still finish a queued reply assembled from segments.
    // Non-queued deliveries below continue to use the original final only.
    let message_text = if raw_text.is_empty() {
        normalized.map(|reply| reply.text.clone()).unwrap_or_default()
    } else { raw_text.clone() };
    if message_text.is_empty() {
        info!(
            group_id = %cmd.group_id,
            sender = %cmd.bot_id,
            "bot relay skipped: empty message text"
        );
        return Ok(RelayOutcome {
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: Vec::new(),
            delivery_results: Vec::new(),
        });
    }

    let overlay = build_route_overlay(flow, &group).await;
    let routing_meta: Option<ChatEventRouting> = cmd
        .event_payload
        .get("routing")
        .and_then(|value| serde_json::from_value(value.clone()).ok());
    let routing_policy = group.routing_policy.clone().unwrap_or_default();
    let hop_count = cmd
        .event_payload
        .get("_forward_hop")
        .and_then(|value| value.as_u64())
        .unwrap_or(0) as u32;
    let sender_route_targets = routing_policy
        .sender_routes
        .get(&cmd.bot_id)
        .filter(|targets| !targets.is_empty());
    let path = if group.group_kind == GroupKind::Dm {
        RoutingPath::LegacyMention
    } else {
        determine_routing_path(
            routing_meta.is_some(),
            routing_policy.mode,
            sender_route_targets.map(|targets| targets.as_slice()),
            hop_count,
        )
    };

    let (mut decision, routing_source) = if group.group_kind == GroupKind::Dm {
        (
            flow.routing
                .route_dm_with_overlay(&group, &message_text, &cmd.bot_id, &overlay)
                .await,
            RequestSource::LegacyMention,
        )
    } else {
        match path {
            RoutingPath::Structured => {
                let meta = routing_meta
                    .as_ref()
                    .expect("structured path requires metadata");
                let decision = flow
                    .routing
                    .route_structured(&group, meta, &cmd.bot_id, &*flow.registry)
                    .await
                    .map_err(|error| ServiceError::InvalidOperation {
                        message: error.to_string(),
                        request_id: Some(cmd.run_id.clone()),
                    })?;
                (decision, RequestSource::StructuredMetadata)
            }
            RoutingPath::SenderRoutes => {
                let targets = sender_route_targets.expect("sender routes path requires targets");
                (
                    build_sender_route_decision(&group, &cmd.bot_id, targets),
                    RequestSource::SenderRoutes,
                )
            }
            RoutingPath::LegacyMention => {
                let mut decision = flow
                    .routing
                    .route_with_overlay(&group, &message_text, Some(&cmd.bot_id), &overlay)
                    .await;
                if decision.mentions.is_empty()
                    && routing_policy.default_bot_final_delivery == DefaultDelivery::InjectObservers
                {
                    for target in &mut decision.targets {
                        if target.is_driver && target.delivery_type == DeliveryType::Send {
                            target.delivery_type = DeliveryType::Inject;
                        }
                    }
                }
                (decision, RequestSource::LegacyMention)
            }
            RoutingPath::DefaultPolicy => (
                build_default_policy_decision(
                    &group,
                    &cmd.bot_id,
                    routing_policy.default_bot_final_delivery,
                ),
                RequestSource::DefaultPolicy,
            ),
        }
    };
    decision = apply_overlay_to_decision(decision, &overlay);

    log_route_digest(cmd, &decision, &message_text, &routing_source);

    let cleaned = if routing_source == RequestSource::LegacyMention {
        decision.cleaned_message.clone()
    } else {
        message_text
    };
    let sender_display_name = sender_display_name(flow, &cmd.bot_id).await;
    let from_bot_owner = from_bot_owner(flow, &cmd.bot_id).await;
    let routing_mode = routing_meta
        .as_ref()
        .and_then(|meta| meta.mode.clone())
        .unwrap_or(ResponseMode::Required);
    let routing_reason = routing_meta.as_ref().map(|meta| meta.reason.clone());
    let policy_mode = routing_mode_slug(routing_policy.mode).to_string();
    let mut protocol_group = group_context_input(&group);
    if let Some(ref bcs_session_id) = cmd.bcs_session_id {
        protocol_group.session_id = bcs_session_id.clone();
        protocol_group.bcs_session_id = Some(bcs_session_id.clone());
    }
    let mut bot_deliveries = Vec::new();
    let mut delivery_results = Vec::new();
    let mut provider_send_failed = false;

    // Finalize the run's open chat segment: flush the buffered streaming text
    // as ONE row, using the final frame's complete text. (Final-only runs with
    // no buffered deltas just insert the final text.)
    let mut queued_contexts = Vec::new();
    for target in &decision.targets {
        let directive = build_response_directive(target, &routing_source, &routing_mode, routing_reason.as_deref());
        let context = build_recipient_group_context(&protocol_group, &target.bot_uuid, &cmd.bot_id, "", &decision.mentions,
            group_context_delivery_type(target.delivery_type), Some(directive), Some(policy_mode.clone()),
            group_type_wire(group.group_strategy), from_bot_owner.clone());
        queued_contexts.push((target.clone(), context));
    }
    let queued_reply_targets = crate::queued_admission::commit_routed_reply(flow, &group, cmd,
        &raw_text, &decision, queued_contexts, &sender_display_name, from_bot_owner.clone(),
        (path == RoutingPath::SenderRoutes).then_some(hop_count + 1), routing_source == RequestSource::LegacyMention, normalized).await?;
    if queued_reply_targets.is_none() {
        persist_final_chat(flow, &cmd, cleaned.clone()).await?;
    } else {
        flow.message_tracker.take_chat_buf(&crate::run_reply::chat_key(&cmd)).await;
    }

    // Notify @-mentioned humans only after the message is persisted, and only
    // if the content passes the outbound policy that governs bot deliveries
    // of the same message.
    if !raw_text.is_empty() && routing_source == RequestSource::LegacyMention && group.group_kind != GroupKind::Dm {
        if let Some(notify_text) = crate::group_flow::apply_notify_outbound_policy(
            flow,
            &cmd.group_id,
            &cmd.bot_id,
            &decision.cleaned_message,
            &decision.targets,
        )
        .await
        {
            crate::human_notify_hook::spawn_human_mention_notify(
                &flow.human_mention_notify,
                Some(decision.mentions.as_slice()),
                &overlay,
                crate::human_notify_hook::MentionNotifyContext {
                    session_id: cmd.bcs_session_id.clone().unwrap_or_default(),
                    group_id: cmd.group_id.clone(),
                    sender_actor_id: cmd.bot_id.clone(),
                    sender_label: sender_display_name.clone(),
                    message_text: notify_text,
                    timestamp_ms: now_ms(),
                },
            );
        }
    }

    for target in &decision.targets {
        if raw_text.is_empty() { continue; }
        if queued_reply_targets.as_ref().is_some_and(|reply| reply.target_ids.contains(&target.bot_uuid)) { continue; }
        let directive = build_response_directive(
            target,
            &routing_source,
            &routing_mode,
            routing_reason.as_deref(),
        );
        let group_context = build_recipient_group_context(
            &protocol_group,
            &target.bot_uuid,
            &cmd.bot_id,
            &cleaned,
            &decision.mentions,
            group_context_delivery_type(target.delivery_type),
            Some(directive),
            Some(policy_mode.clone()),
            group_type_wire(group.group_strategy),
            from_bot_owner.clone(),
        );
        let outbound_message = bcs_service_api::GroupMessage {
            id: uuid::Uuid::new_v4().to_string(),
            timestamp: now_ms(),
            sender: cmd.bot_id.clone(),
            content: cleaned.clone(),
            message_type: bcs_service_api::GroupMessageType::Bot,
            bot_name: Some(sender_display_name.clone()),
            role: bcs_service_api::MessageRole::Assistant,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
            attachments: None,
        };
        let outbound_message = match crate::group_flow::apply_outbound_interceptors(
            flow,
            &cmd.group_id,
            &outbound_message,
            target,
        )
        .await
        {
            Ok(message) => message,
            Err(reason) => {
                log_relay_deliver_result(
                    cmd,
                    &cmd.run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    false,
                    Some(reason.message.as_str()),
                    Some("interceptor"),
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: false,
                    error: Some(reason.message),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: target.bot_uuid.clone(),
                    delivered: false,
                    error: Some(ServiceError::Unauthorized(format!(
                        "outbound blocked by interceptor '{}'",
                        reason.interceptor_id
                    ))),
                });
                continue;
            }
        };

        let delivery_target = match flow
            .registry
            .resolve_delivery_target(&target.bot_uuid)
            .await
        {
            Ok(target) => target,
            Err(error) => {
                let error_text = error.to_string();
                log_relay_deliver_result(
                    cmd,
                    &cmd.run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("resolve_target"),
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: false,
                    error: Some(error_text),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: target.bot_uuid.clone(),
                    delivered: false,
                    error: Some(error),
                });
                continue;
            }
        };
        let protocol_version = frame_protocol_version(
            flow.registry.get_protocol_version(&target.bot_uuid).await,
            &delivery_target,
        );
        let is_provider_send =
            delivery_target.is_http_provider() && target.delivery_type == DeliveryType::Send;
        let provider_tags = if delivery_target.is_http_provider() {
            group
                .participants
                .iter()
                .find(|participant| participant.bot_uuid == target.bot_uuid)
                .map(|participant| participant.tags.as_slice())
                .unwrap_or(&[])
        } else {
            &[]
        };
        let mut frame = match target.delivery_type {
            DeliveryType::Send => build_send_frame(
                &cmd.group_id,
                cmd.bcs_session_id.as_deref(),
                &cmd.bot_id,
                &sender_display_name,
                &outbound_message.content,
                &group_context,
                provider_tags,
                protocol_version,
                None,
            ),
            DeliveryType::Inject => build_inject_frame(
                &cmd.group_id,
                cmd.bcs_session_id.as_deref(),
                &cmd.bot_id,
                &sender_display_name,
                &outbound_message.content,
                &group_context,
                provider_tags,
                protocol_version,
                None,
            ),
        };
        if path == RoutingPath::SenderRoutes {
            stamp_forward_hop(&mut frame, hop_count + 1);
        }

        let run_id = request_id(&frame).unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        // FIXME(interceptor-modify): if SecurityInterceptor rewrote
        // outbound_message.id (security gateway task_id), it isn't reaching
        // the bot — frame's request_id is generated upstream from cmd, not
        // from outbound_message. Threading the modified id requires changing
        // build_send_frame / build_inject_frame signatures. Tracked in the
        // Phase-5 follow-up list (Modify semantics completeness).
        let delivery_kind = bot_delivery_kind(target.delivery_type);
        flow.register_send_context(
            target.delivery_type,
            &delivery_target,
            &frame,
            &run_id,
            &target.bot_uuid,
            &cmd.group_id,
            cmd.bcs_session_id.as_deref(),
            &[],
        )
        .await?;
        let delivery = flow
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
        match delivery {
            Ok(result) => {
                if is_provider_send && !result.delivered {
                    provider_send_failed = true;
                }
                if !result.delivered {
                    flow.discard_send_context(&run_id).await?;
                }
                log_relay_deliver_result(
                    cmd,
                    &run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string).as_deref(),
                    None,
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: result.delivered,
                    error: result.error.as_ref().map(ToString::to_string),
                });
                bot_deliveries.push(result);
            }
            Err(error) => {
                flow.discard_send_context(&run_id).await?;
                if is_provider_send {
                    provider_send_failed = true;
                }
                let error_text = error.to_string();
                log_relay_deliver_result(
                    cmd,
                    &run_id,
                    &target.bot_uuid,
                    target.delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("deliver"),
                    &routing_source,
                );
                delivery_results.push(MessageDeliveryResult {
                    bot_uuid: target.bot_uuid.clone(),
                    delivery_type: target.delivery_type,
                    success: false,
                    error: Some(error_text),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: target.bot_uuid.clone(),
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    // Queue acceptance is the relay boundary for managed targets. Count one
    // logical reply even for fan-out or mixed managed/legacy recipients.
    if queued_reply_targets.as_ref().is_some_and(|reply| reply.relayed)
        || bot_deliveries.iter().any(|delivery| delivery.delivered) {
        flow.group.increment_message_count(&cmd.group_id).await?;
    }

    if provider_send_failed {
        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.bcs_session_id.as_deref().unwrap_or(&cmd.group_id);
            let receivers = group
                .participants
                .iter()
                .filter(|participant| participant.is_bot() && participant.bot_uuid != cmd.bot_id)
                .cloned()
                .collect();
            let event = SystemMessageEvent::GenericNotification {
                group_id: cmd.group_id.clone(),
                message: "消息投递失败，请稍后重试。".to_string(),
                receivers,
            };
            let _ = system_message
                .notify(&cmd.group_id, event, session_id, &group.participants)
                .await;
        }
    }

    if !decision.hidden_mentions.is_empty() {
        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.bcs_session_id.as_deref().unwrap_or(&cmd.group_id);
            for hidden in &decision.hidden_mentions {
                let event = SystemMessageEvent::BotHiddenNotice {
                    group_id: cmd.group_id.clone(),
                    mentioner_bot_id: cmd.bot_id.clone(),
                    hidden_bot_name: hidden.hidden_bot_name.clone(),
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    Ok(RelayOutcome {
        bot_deliveries,
        frontend_deliveries: Vec::new(),
        mentions: decision.mentions,
        delivery_results,
    })
}
