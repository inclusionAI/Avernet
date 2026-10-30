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
            caller_execution: None,
            // Preserve execution identity by move; command Clone strips it.
            caller_ingress: cmd.caller_ingress,
            speaking_lease: None,
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

    // F4/R2 ordering: determine THIS request's branch and resolve
    // its actual write target first — the managed resolver for the
    // managed Bot-send branch (exactly-one Running Session or an
    // error), the unmanaged eventing anchor otherwise — and gate
    // the sender's membership of THAT Session BEFORE quota or any
    // write side effect. A request that fails this gate must never
    // touch the message count nor disable the Group. The managed
    // and unmanaged selection rules stay per-branch on purpose:
    // neither may borrow the other's "unique" or "first" policy.
    let managed_branch = group.group_strategy != GroupStrategy::StateMachine
        && crate::queued_admission::manages_any(flow, &group).await
        && cmd.message_type == GroupMessageType::Bot;
    let resolved_session = if managed_branch {
        Some(
            crate::queued_admission::resolve_group_session(flow, &group, None)
                .await?
                .ok_or_else(|| {
                    ServiceError::InternalError(
                        "managed admission resolved no Session".to_string(),
                    )
                })?,
        )
    } else {
        web_send::resolve_unmanaged_eventing_session(flow, &group).await?
    };
    if let Some(session_id) = resolved_session.as_deref() {
        web_send::ensure_resolved_session_sender_membership(flow, session_id, &cmd.sender)
            .await?;
    }

    // F1 (spec §8.2): the NON-managed branch delivers its downstream
    // Runs itself, so it installs the SAME operation output
    // admission WebSend gets — per-connection permits frozen before
    // the operation's first side effect, callback events tagged with
    // the operation instead of re-authorizing per event. The managed
    // branch delegates to handle_web_send, which admits its own
    // operation; preparing another one here would only leave an
    // unused duplicate for the prepare-timeout.
    let output_operation_id = if managed_branch {
        None
    } else {
        let operation_id = uuid::Uuid::new_v4().to_string();
        if let Some(output_admission) = flow.output_admission.as_ref() {
            output_admission
                .admit_output_operation(bcs_service_api::OutputOperationAdmission {
                    operation_id: operation_id.clone(),
                    group_id: cmd.group_id.clone(),
                    session_id: resolved_session.clone(),
                })
                .await?;
        }
        Some(operation_id)
    };

    // Quota gate — only Session-admitted requests may reach it;
    // everything the limit check does (including the Inactive
    // transition) is a side effect reserved for admitted sends.
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

    if managed_branch {
        let outcome = handle_web_send(flow, WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: cmd.caller.clone(), group_id: cmd.group_id.clone(),
            session_id: resolved_session.clone(),
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

    // A rejected write must not hide the count increment failure:
    // persistence-layer discipline propagates it as an error.
    flow.group.increment_message_count(&cmd.group_id).await?;

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
        resolved_session.as_deref(),
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
                flow.group.as_ref(),
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
            )
            .await;
        }
    }

    let mut routed_to = Vec::new();
    let synthetic_send = WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: cmd.caller.clone(),
        group_id: cmd.group_id.clone(),
        session_id: resolved_session.clone(),
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

    // F1 (spec §8.2): plan every downstream Run up front — the
    // operation's complete output-stream set must exist before the
    // first register_send_context, so its runs can be bound, their
    // producer facts frozen, and the whole set registered with the
    // delivery adapter before any downstream callback can fire.
    struct TargetPlan {
        run_id: String,
        target_bot_id: String,
        delivery_type: DeliveryType,
        delivery_target: BotDeliveryTarget,
        frame: BcsFrame,
    }

    let mut plans = Vec::new();
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
        plans.push(TargetPlan {
            run_id,
            target_bot_id: target.bot_uuid.clone(),
            delivery_type: target.delivery_type,
            delivery_target,
            frame,
        });
    }

    // Bind the operation's stream set before ANY downstream I/O:
    // cache the trusted operation tag, freeze each producer's output
    // facts from the admission-time snapshot, and hand the complete
    // set to the delivery adapter (Run terminal ≠ operation
    // complete). Freeze the same Session used by admission and callbacks;
    // truly Group-only sends retain Group participant facts. Only Send
    // targets produce callbacks (Inject never registers a run context).
    let freeze_session = session_snapshot_for_freeze(flow, resolved_session.as_deref()).await;
    if let Some(operation_id) = output_operation_id.as_deref() {
        let mut operation_run_ids: Vec<String> = Vec::new();
        for plan in &plans {
            if plan.delivery_type != DeliveryType::Send {
                continue;
            }
            flow.message_tracker
                .cache_run_operation(&plan.run_id, operation_id)
                .await;
            freeze_run_output_facts(flow, &group, freeze_session.as_ref(), &plan.run_id, &plan.target_bot_id)
                .await;
            operation_run_ids.push(plan.run_id.clone());
        }
        if !operation_run_ids.is_empty() {
            if let Some(output_admission) = flow.output_admission.as_ref() {
                output_admission
                    .register_operation_runs(operation_id, operation_run_ids)
                    .await?;
            }
        }
    }

    // The send's planned Send runs. Each leaves the unsettled set
    // as its outcome is decided; a register_send_context failure
    // releases exactly the still-unsettled runs (never an
    // already-delivered sibling).
    let mut unsettled_send_ids: Vec<String> = plans
        .iter()
        .filter(|plan| plan.delivery_type == DeliveryType::Send)
        .map(|plan| plan.run_id.clone())
        .collect();

    for plan in plans {
        let TargetPlan {
            run_id,
            target_bot_id,
            delivery_type,
            delivery_target,
            frame,
        } = plan;
        let delivery_kind = bot_delivery_kind(delivery_type);
        // A failure here aborts the whole send: its registered runs
        // are provably output-less — release them explicitly.
        if let Err(error) = flow
            .register_send_context(
                delivery_type,
                &delivery_target,
                &frame,
                &run_id,
                &target_bot_id,
                &cmd.group_id,
                resolved_session.as_deref(),
                &[],
            )
            .await
        {
            if let Some(operation_id) = output_operation_id.as_deref() {
                flow.release_output_runs(operation_id, &unsettled_send_ids)
                    .await;
            }
            return Err(error);
        }
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
        unsettled_send_ids.retain(|candidate| candidate != &run_id);
        match result {
            Ok(result) => {
                if !result.delivered {
                    flow.discard_send_context(&run_id).await?;
                    // The transport proved this run produces
                    // nothing: release it explicitly (spec §8.2
                    // end-states).
                    if delivery_type == DeliveryType::Send {
                        if let Some(operation_id) = output_operation_id.as_deref() {
                            flow.release_output_runs(operation_id, &[run_id.clone()])
                                .await;
                        }
                    }
                    warn!(
                        session_id = %cmd.group_id,
                        bot_uuid = %target_bot_id,
                        error = ?result.error,
                        "Failed to send message to bot"
                    );
                }
            }
            Err(error) => {
                // Preserve the ingress context and active index as well as the
                // output permit; callbacks still enforce identity and deadline.
                // A transport Err is UNCERTAIN, not terminal: the
                // remote may have accepted the run — its binding
                // and frozen facts survive until its own terminal
                // (spec §8.2 uncertainty rule), nothing to release.
                warn!(
                    session_id = %cmd.group_id,
                    bot_uuid = %target_bot_id,
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
