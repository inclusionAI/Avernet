use super::*;

pub(super) async fn handle_task_bot_event(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    task_id: &str,
) -> ServiceResult<Vec<BotDeliveryResult>> {
    let Some(entry) = flow.task_store.get(task_id).await else {
        return Ok(Vec::new());
    };
    if entry.status != TaskLedgerStatus::Dispatched || entry.target_bot != cmd.bot_id {
        return Ok(Vec::new());
    }
    if !is_terminal_state(&cmd.state) || !matches!(cmd.event_type.as_str(), "chat" | "chat.event") {
        if !entry.managed || matches!(cmd.event_type.as_str(), "chat" | "chat.event")
            || (cmd.event_type == "agent" && cmd.event_payload.get("stream").and_then(Value::as_str) == Some("tool")) {
            record_task_response_event(flow, task_id, cmd).await;
        }
        return Ok(Vec::new());
    }

    let response_text = if cmd.state == ChatEventState::Error {
        error_display_text(&cmd.event_payload)
    } else {
        preview_task_response_text(flow, &entry, cmd).await
    };
    if let Some(row) = crate::queued_admission::find_managed_run(flow, cmd).await? {
        if crate::queued_task::intent(&row)?.is_some() {
            let normalized = if cmd.state == ChatEventState::Error {
                crate::run_reply::RunReply {
                    text: response_text.clone(),
                    display: flow
                        .message_tracker
                        .peek_chat_buf(&crate::run_reply::chat_key(cmd))
                        .await
                        .unwrap_or_default(),
                    method: "error",
                    raw_final: response_text.clone(),
                    source_ids: Vec::new(),
                }
            } else {
                crate::run_reply::prepare(
                    flow,
                    cmd,
                    &extract_message_text(&cmd.event_payload),
                    true,
                )
                .await?
            };
            let response_text = if cmd.state == ChatEventState::Error
                || entry.response_mode == ChatResponseMode::Full
            {
                normalized.text.clone()
            } else {
                // Normalize final once before selecting the task window. An
                // engine may append a new final delta even after SSE deltas.
                let scratch = TaskStore::new();
                scratch.register(entry.clone()).await;
                scratch.record_final_response_text(task_id, &normalized.text).await;
                task_response_text(&scratch.get(task_id).await.unwrap_or_else(|| entry.clone()), cmd)
            };
            crate::queued_task_terminal::commit(flow, &row, cmd, &response_text, normalized).await?;
            if let Some(group) = flow.group.get(&entry.group_id).await {
                crate::task_flow::emit_task_ledger_status(flow, &group, &entry.group_id,
                    entry.session_id.as_deref(), &entry.driver_bot).await;
            }
            return Ok(Vec::new());
        }
    }
    let mut group = flow.group.get(&entry.group_id).await;
    if let (Some(group), Some(session_id)) = (group.as_mut(), entry.session_id.as_deref()) {
        crate::task_flow::apply_session_participants(flow, group, &entry.group_id, session_id)
            .await?;
    }
    if let Some(group) = &group {
        if crate::queued_task_terminal::admit_legacy_result(flow, &entry, group, cmd, &response_text).await? {
            if cmd.state == ChatEventState::Final {
                crate::task_flow::record_task_completed(flow, &entry, &response_text, now_ms()).await?;
            }
            flow.message_tracker.cleanup_run(&cmd.run_id).await;
            flow.message_tracker.cleanup_run(&crate::run_reply::chat_key(cmd)).await;
            crate::task_flow::emit_task_ledger_status(flow, group, &entry.group_id,
                entry.session_id.as_deref(), &entry.driver_bot).await;
            return Ok(Vec::new());
        }
    }

    let target_bot_name = entry
        .target_bot_name
        .as_deref()
        .unwrap_or(entry.target_bot.as_str());
    let result = if entry.terminal_result_delivered {
        BotDeliveryResult {
            target_bot_id: entry.driver_bot.clone(),
            delivered: true,
            error: None,
        }
    } else {
        let manager_result_run_id = uuid::Uuid::new_v4().to_string();
        let delivery_target = flow
            .registry
            .resolve_delivery_target(&entry.driver_bot)
            .await?;
        let provider_tags = if delivery_target.is_http_provider() {
            group
                .as_ref()
                .and_then(|group| {
                    group
                        .participants
                        .iter()
                        .find(|participant| participant.bot_uuid == entry.driver_bot)
                })
                .map(|participant| participant.tags.as_slice())
                .unwrap_or(&[])
        } else {
            &[]
        };
        let frame = build_task_result_frame(
            group.as_ref(),
            &entry.group_id,
            entry.session_id.as_deref().unwrap_or(&entry.group_id),
            &entry.driver_bot,
            &entry.target_bot,
            target_bot_name,
            &response_text,
            &entry.task_id,
            &manager_result_run_id,
            provider_tags,
        );
        let delivery_kind = BotDeliveryKind::TaskResult;
        flow.register_send_context(
            DeliveryType::Send,
            &delivery_target,
            &frame,
            &manager_result_run_id,
            &entry.driver_bot,
            &entry.group_id,
            entry.session_id.as_deref(),
            &[],
        )
        .await?;
        let delivery = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: manager_result_run_id.clone(),
                frame,
                delivery_kind,
                provider_transport: Default::default(),
                provider_bypass_headers: Vec::new(),
            })
            .await;
        let result = match delivery {
            Ok(result) => result,
            Err(error) => {
                flow.discard_send_context(&manager_result_run_id).await?;
                return Err(error);
            }
        };
        if !result.delivered {
            flow.discard_send_context(&manager_result_run_id).await?;
            return Ok(vec![result]);
        }
        if cmd.state == ChatEventState::Error {
            flow.task_store.record_terminal_delivery(task_id).await;
        }
        result
    };

    record_task_response_event(flow, task_id, cmd).await;

    // Terminal chat for a validated, still-Dispatched task run is persisted only
    // after the task result reaches the driver. If delivery fails, the task stays
    // retryable and no history side effect is committed.
    if matches!(cmd.event_type.as_str(), "chat" | "chat.event") {
        if matches!(cmd.state, ChatEventState::Final) {
            persist_task_final_chat(flow, cmd, response_text.clone()).await?;
        } else {
            flush_chat_segment(flow, cmd, None).await?;
        }
    }

    if let Some(group) = group.as_ref() {
        if group.group_strategy == GroupStrategy::ManagerWorker {
            crate::group_flow::try_persist_group_message(
                flow,
                &entry.group_id,
                Some(entry.session_id.as_deref().unwrap_or(&entry.group_id)),
                &entry.target_bot,
                SenderType::Bot,
                if cmd.state == ChatEventState::Error {
                    bcs_domain::CHAT_ERROR_MESSAGE_TYPE
                } else {
                    "chat"
                },
                Value::String(response_text.clone()),
                if cmd.state == ChatEventState::Error {
                    Some(format!("chat-error:{}", entry.task_id))
                } else {
                    None
                }
                .as_deref(),
                None,
                &entry.task_id,
            )
            .await?;
        }
    }
    if matches!(cmd.state, ChatEventState::Final) {
        crate::task_flow::record_task_completed(flow, &entry, &response_text, now_ms()).await?;
    }
    flow.message_tracker.cleanup_run(&cmd.run_id).await;
    flow.message_tracker
        .cleanup_run(&crate::run_reply::chat_key(cmd))
        .await;
    flow.task_store.mark_replied(task_id).await;
    if let Some(group) = group.as_ref() {
        crate::task_flow::emit_task_ledger_status(
            flow,
            group,
            &entry.group_id,
            entry.session_id.as_deref(),
            &entry.driver_bot,
        )
        .await;
    }
    Ok(vec![result])
}

pub(super) async fn preview_task_response_text(
    flow: &BcsMessageFlow,
    entry: &TaskEntry,
    cmd: &BotEventCommand,
) -> String {
    let scratch = TaskStore::new();
    scratch.register(entry.clone()).await;
    let is_delta_mode = flow.message_tracker.is_chat_delta_mode(&crate::run_reply::chat_key(&cmd)).await;
    record_task_response_event_in_store(&scratch, &entry.task_id, cmd, is_delta_mode).await;
    let attempted_entry = scratch
        .get(&entry.task_id)
        .await
        .unwrap_or_else(|| entry.clone());
    task_response_text(&attempted_entry, cmd)
}

pub(super) fn task_response_text(entry: &TaskEntry, cmd: &BotEventCommand) -> String {
    if entry.managed && entry.empty_tool_response_window() { return "[no response]".into(); }
    let response_text = if entry.response_mode == ChatResponseMode::Full {
        extract_message_text(&cmd.event_payload)
    } else if entry.response_content.is_empty() {
        extract_message_text(&cmd.event_payload)
    } else {
        entry.response_content.clone()
    };
    if response_text.is_empty() {
        "[no response]".to_string()
    } else {
        response_text
    }
}

pub(super) async fn record_task_response_event(flow: &BcsMessageFlow, task_id: &str, cmd: &BotEventCommand) {
    let is_delta_mode = flow.message_tracker.is_chat_delta_mode(&crate::run_reply::chat_key(&cmd)).await;
    record_task_response_event_in_store(flow.task_store.as_ref(), task_id, cmd, is_delta_mode)
        .await;
}

pub(super) async fn record_task_response_event_in_store(
    task_store: &TaskStore,
    task_id: &str,
    cmd: &BotEventCommand,
    is_delta_mode: bool,
) {
    if cmd.event_type == "agent"
        && cmd
            .event_payload
            .get("stream")
            .and_then(|value| value.as_str())
            == Some("tool")
    {
        task_store.record_response_tool_call(task_id).await;
        return;
    }
    match cmd.state {
        ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd => {
            task_store.record_response_tool_call(task_id).await;
        }
        ChatEventState::Delta => {
            if let Some(delta) = extract_delta_text(&cmd.event_payload) {
                task_store.record_response_delta(task_id, delta).await;
            } else {
                let text = extract_message_text(&cmd.event_payload);
                task_store.record_response_text(task_id, &text).await;
            }
        }
        ChatEventState::Final => {
            // In SSE delta mode every byte of visible response text was already
            // appended from `delta_text`. The synthesized final `message` is
            // only the current MessageTracker segment, not a task-level
            // snapshot, so merging it here would append that segment twice.
            if !is_delta_mode {
                let text = extract_message_text(&cmd.event_payload);
                task_store.record_final_response_text(task_id, &text).await;
            }
        }
        ChatEventState::Error | ChatEventState::Aborted => {}
    }
}
