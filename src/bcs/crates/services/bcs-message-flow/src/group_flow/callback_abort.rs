use super::*;

pub async fn handle_group_callback(
    flow: &BcsMessageFlow,
    cmd: GroupCallbackCommand,
) -> ServiceResult<GroupCallbackOutcome> {
    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    backfill_bot_names(flow.registry.as_ref(), &mut group).await;
    let overlay = build_route_overlay(flow, &group).await;
    let routable_message = callback_routable_message(&group, &cmd);
    let decision = if cmd.mentions.is_empty() || mentions_all(&cmd.mentions) {
        flow.routing
            .route_with_overlay(&group, &routable_message, None, &overlay)
            .await
    } else {
        build_explicit_mention_decision(&group, &cmd.mentions, &routable_message, &overlay)
    };

    // NOTE: group callbacks deliberately do NOT trigger human mention
    // notifications. `POST /groups/{id}/callback` carries no caller identity,
    // so request-controlled `mentions` would let anyone able to reach the
    // route push external notifications (e.g. IM DMs) to arbitrary human
    // participants. Re-enable only behind an authenticated callback caller.

    if cmd.store_message {
        let group_message = GroupMessage {
            id: uuid::Uuid::new_v4().to_string(),
            timestamp: now_ms(),
            sender: "system".to_string(),
            content: cmd.message.clone(),
            message_type: GroupMessageType::System,
            bot_name: None,
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: cmd.metadata.clone(),
            attachments: None,
        };
        try_persist_group_message(
            flow,
            &cmd.group_id,
            None,
            "system",
            SenderType::System,
            "system",
            Value::String(cmd.message.clone()),
            None,
            None,
            "", // run_id: group callback
        )
        .await?;
        flow.group.add_message(&cmd.group_id, group_message).await?;
    }

    let mut bot_deliveries = Vec::new();
    let mut delivery_results = Vec::new();
    let sender_display_name = "system".to_string();
    let callback_send = WebSendCommand {
        caller: CallerContext::Public,
        group_id: cmd.group_id.clone(),
        session_id: None,
        from_actor_id: "system".to_string(),
        from_name: Some(sender_display_name.clone()),
        message: cmd.message.clone(),
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
        let run_id = uuid::Uuid::new_v4().to_string();
        let target_bot_id = target.bot_uuid.clone();
        let delivery_type = target.delivery_type;

        // Run outbound interceptor chain. The callback originates from "system"
        // and lacks AgentPass credentials, so SecurityInterceptor will skip the
        // chain per the missing-credentials guard. The hook is still wired so
        // future interceptors (audit, rate-limit) see callback traffic.
        let synthetic_message = GroupMessage {
            id: run_id.clone(),
            timestamp: now_ms(),
            sender: "system".to_string(),
            content: decision.cleaned_message.clone(),
            message_type: GroupMessageType::System,
            bot_name: None,
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: cmd.metadata.clone(),
            attachments: None,
        };
        let outbound_message = match apply_outbound_interceptors(
            flow,
            &cmd.group_id,
            &synthetic_message,
            target,
        )
        .await
        {
            Ok(message) => message,
            Err(reason) => {
                warn!(
                    group_id = %cmd.group_id,
                    target_bot_id = %target_bot_id,
                    interceptor = %reason.interceptor_id,
                    code = %reason.code,
                    "group callback delivery blocked by interceptor chain"
                );
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(reason.message.clone()),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(ServiceError::Forbidden(reason.message)),
                });
                continue;
            }
        };

        let delivery_target = match flow.registry.resolve_delivery_target(&target_bot_id).await {
            Ok(target) => target,
            Err(error) => {
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error.to_string()),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
                continue;
            }
        };
        let frame = frame_for_target(
            flow,
            &group,
            &callback_send,
            &decision,
            target,
            &delivery_target,
            &run_id,
            &outbound_message.content,
            &sender_display_name,
            None,
        )
        .await;

        let delivery_kind = bot_delivery_kind(delivery_type);
        flow.register_send_context(
            delivery_type,
            &delivery_target,
            &frame,
            &run_id,
            &target_bot_id,
            &cmd.group_id,
            None,
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
                if !result.delivered {
                    flow.discard_send_context(&run_id).await?;
                }
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string),
                ));
                bot_deliveries.push(result);
            }
            Err(error) => {
                flow.discard_send_context(&run_id).await?;
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error.to_string()),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    let frontend_deliveries = publish_group_callback_event(flow, &cmd).await;

    Ok(GroupCallbackOutcome {
        bot_deliveries,
        frontend_deliveries,
        mentions: decision.mentions,
        delivered_count: delivery_results
            .iter()
            .filter(|result| result.success)
            .count(),
        failed_count: delivery_results
            .iter()
            .filter(|result| !result.success)
            .count(),
        delivery_results,
    })
}

pub async fn handle_chat_abort(
    flow: &BcsMessageFlow,
    cmd: ChatAbortCommand,
) -> ServiceResult<ChatAbortOutcome> {
    const ABORT_DELIVERY_TIMEOUT_MS: u64 = 60_000;
    const MAX_PLUGIN_ABORT_CONCURRENCY: usize = 8;

    let Some(run_context) = flow.bot_run_context.as_ref() else {
        return Err(ServiceError::InvalidOperation {
            message: "chat.abort requires the Bot run context index".to_string(),
            request_id: cmd.run_id,
        });
    };
    let scope = BotRunScope {
        group_id: cmd.group_id.clone(),
        session_id: cmd.session_id.clone(),
        bot_id: cmd.bot_id.clone(),
    };
    let mut active = run_context.list_active_runs(&scope).await?;
    let managed_abort = crate::delivery_abort::select(flow, &cmd, &mut active).await?;
    if let Some(requested_run_id) = cmd.run_id.as_deref() {
        active.retain(|context| {
            context.canonical_run_id == requested_run_id
                || context.downstream_run_id == requested_run_id
        });
    }
    if active.is_empty() {
        return Ok(ChatAbortOutcome {
            aborted: false,
            aborted_run_ids: Vec::new(),
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            failures: managed_abort.failures,
        });
    }

    let (plugin_runs, provider_runs): (Vec<_>, Vec<_>) = active
        .into_iter()
        .partition(|context| matches!(context.transport_owner, BotRunTransportOwner::WebSocket));
    let plugin_results = stream::iter(plugin_runs.into_iter().map(|context| {
        let delivery = flow.bot_delivery.clone();
        let scope = scope.clone();
        let managed = managed_abort.owned.get(&context.canonical_run_id).cloned();
        async move {
            let command = BotAbortDeliveryCommand {
                target: BotDeliveryTarget::WebSocket {
                    bot_id: scope.bot_id.clone(),
                },
                command_id: managed.as_ref().and_then(|row| row.abort_request_id.clone()).unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
                group_id: scope.group_id,
                session_id: context
                    .downstream_session_key
                    .clone()
                    .unwrap_or(scope.session_id),
                run_id: Some(context.downstream_run_id.clone()),
                provider_bypass_headers: Vec::new(),
                timeout_ms: ABORT_DELIVERY_TIMEOUT_MS,
            };
            let result = if let Some(row) = managed {
                match row.transport_context_json.as_ref().and_then(|v| v.get("connection_id")).and_then(Value::as_str) {
                    Some(id) => delivery.abort_on_connection(command, id).await,
                    None => Err(ServiceError::InvalidOperation { message: "original Bot connection identity missing".into(), request_id: Some(context.canonical_run_id.clone()) }),
                }
            } else { delivery.abort(command).await };
            (context, result)
        }
    }))
    .buffer_unordered(MAX_PLUGIN_ABORT_CONCURRENCY)
    .collect::<Vec<_>>()
    .await;

    let mut bot_deliveries = Vec::new();
    let mut confirmed = Vec::new();
    let mut failures = managed_abort.failures;
    for (context, result) in plugin_results {
        match result {
            Ok(result) => {
                let acknowledged = result.target_bot_id == cmd.bot_id && result
                    .aborted_run_ids
                    .iter()
                    .any(|run_id| run_id == &context.downstream_run_id)
                    && (!managed_abort.owned.contains_key(&context.canonical_run_id) || result.aborted_run_ids.len() == 1);
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: result.target_bot_id,
                    delivered: true,
                    error: None,
                });
                if acknowledged {
                    confirmed.push(context);
                }
            }
            Err(error) => {
                failures.push(ChatAbortFailure {
                    run_id: context.canonical_run_id.clone(),
                    code: chat_abort_failure_code(&error).to_string(),
                    message: error.to_string(),
                });
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: cmd.bot_id.clone(),
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    if !provider_runs.is_empty() {
        // Empty means the default route, not a wildcard: every run in a
        // scope-wide abort must agree, including recovered managed runs.
        let mut header_sets = provider_runs.iter().map(|context| {
            let mut headers = context.provider_bypass_headers.clone();
            headers.sort();
            headers
        });
        let provider_bypass_headers = header_sets.next().unwrap_or_default();
        let provider_headers_match = header_sets.all(|headers| headers == provider_bypass_headers);
        // The Provider adapter sends the canonical BCS session id. The stored
        // downstream_session_key is the Bot WS/plugin wire scope and may still
        // be a legacy group key, so it must not drive a Provider scope abort.
        let expected_by_downstream: HashMap<String, ActiveBotRunContext> = provider_runs
            .iter()
            .cloned()
            .map(|context| (context.downstream_run_id.clone(), context))
            .collect();
        let owners: HashSet<BotRunTransportOwner> = provider_runs
            .iter()
            .map(|context| context.transport_owner.clone())
            .collect();
        let current_target = flow.registry.resolve_delivery_target(&cmd.bot_id).await;
        let delivery = match (provider_headers_match, owners.len(), current_target) {
            (false, _, _) => Err(ServiceError::InvalidOperation {
                message: "active Provider runs have conflicting routing headers".to_string(),
                request_id: cmd.run_id.clone(),
            }),
            (true, 1, Ok(target))
                if provider_owner_matches_target(
                    owners.iter().next().expect("one owner"),
                    &target,
                ) =>
            {
                flow.bot_delivery
                    .abort(BotAbortDeliveryCommand {
                        target,
                        command_id: uuid::Uuid::new_v4().to_string(),
                        group_id: cmd.group_id.clone(),
                        session_id: cmd.session_id.clone(),
                        run_id: None,
                        provider_bypass_headers,
                        timeout_ms: ABORT_DELIVERY_TIMEOUT_MS,
                    })
                    .await
            }
            (true, 1, Ok(_)) => Err(ServiceError::InvalidOperation {
                message: "Provider transport ownership changed after run creation".to_string(),
                request_id: cmd.run_id.clone(),
            }),
            (true, _, Ok(_)) => Err(ServiceError::InvalidOperation {
                message: "active Provider runs have conflicting transport ownership".to_string(),
                request_id: cmd.run_id.clone(),
            }),
            (true, _, Err(error)) => Err(error),
        };
        match delivery {
            Ok(result) => {
                let mut invalid_ids = Vec::new();
                for downstream_run_id in result.aborted_run_ids {
                    if let Some(context) = expected_by_downstream.get(&downstream_run_id).filter(|_| result.target_bot_id == cmd.bot_id) {
                        confirmed.push(context.clone());
                    } else {
                        invalid_ids.push(downstream_run_id);
                    }
                }
                if !invalid_ids.is_empty() {
                    failures.push(ChatAbortFailure {
                        run_id: invalid_ids.join(","),
                        code: "scope_mismatch".to_string(),
                        message:
                            "Provider returned run ids outside the requested Bot/Session scope"
                                .to_string(),
                    });
                }
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: result.target_bot_id,
                    delivered: invalid_ids.is_empty(),
                    error: None,
                });
            }
            Err(error) => {
                let message = error.to_string();
                failures.extend(provider_runs.iter().map(|context| ChatAbortFailure {
                    run_id: context.canonical_run_id.clone(),
                    code: chat_abort_failure_code(&error).to_string(),
                    message: message.clone(),
                }));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id: cmd.bot_id.clone(),
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }

    let mut aborted_run_ids = Vec::new();
    let confirmed_managed: HashSet<_> = confirmed.iter().map(|c| c.canonical_run_id.clone()).collect();
    for (run_id, row) in &managed_abort.owned {
        if crate::delivery_abort::finish(flow, row, confirmed_managed.contains(run_id)).await? {
            aborted_run_ids.push(run_id.clone());
        }
    }
    for context in confirmed {
        if commit_aborted_run(run_context.as_ref(), &context).await? && !managed_abort.owned.contains_key(&context.canonical_run_id) {
            aborted_run_ids.push(context.canonical_run_id);
        }
    }
    aborted_run_ids.sort();
    aborted_run_ids.dedup();
    let frontend_deliveries =
        publish_chat_abort_event(flow, &cmd.group_id, Some(&cmd.session_id), &aborted_run_ids)
            .await;

    Ok(ChatAbortOutcome {
        aborted: !aborted_run_ids.is_empty(),
        aborted_run_ids,
        bot_deliveries,
        frontend_deliveries,
        failures,
    })
}

fn chat_abort_failure_code(error: &ServiceError) -> &'static str {
    match error {
        ServiceError::BotMethodUnsupported { .. } => "chat_abort_not_supported",
        _ => "chat_abort_failed",
    }
}

pub(super) fn provider_owner_matches_target(owner: &BotRunTransportOwner, target: &BotDeliveryTarget) -> bool {
    matches!(
        (owner, target),
        (
            BotRunTransportOwner::HttpProvider {
                provider_id: owner_provider_id,
                provider_bot_ref: owner_bot_ref,
            },
            BotDeliveryTarget::HttpProvider {
                provider_id,
                provider_bot_ref,
                ..
            }
        ) if owner_provider_id == provider_id && owner_bot_ref == provider_bot_ref
    )
}

async fn commit_aborted_run(
    run_context: &dyn BotRunContextPort,
    context: &ActiveBotRunContext,
) -> ServiceResult<bool> {
    let committed = if run_context
        .try_begin_terminal(&context.canonical_run_id)
        .await
    {
        let committed = run_context.mark_terminal(&context.canonical_run_id).await;
        if !committed {
            run_context
                .release_terminal(&context.canonical_run_id)
                .await;
        }
        committed
    } else {
        run_context
            .get_context(&context.canonical_run_id)
            .await
            .is_some_and(|run| run.terminal)
    };
    if committed {
        run_context
            .mark_provider_transport_terminal(&context.canonical_run_id)
            .await;
        run_context
            .remove_active_run(&context.scope, &context.canonical_run_id)
            .await?;
    }
    Ok(committed)
}
