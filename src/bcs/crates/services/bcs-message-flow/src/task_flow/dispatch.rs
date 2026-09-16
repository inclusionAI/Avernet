use super::*;

pub async fn handle_task_dispatch(
    flow: &BcsMessageFlow,
    cmd: TaskDispatchCommand,
) -> ServiceResult<TaskDispatchOutcome> {
    let (group_id, manager_session_id) = task_dispatch_scope(&cmd.group_id, &cmd.payload);
    let mut group = flow
        .group
        .get(&group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(group_id.clone()))?;
    if let Some(session_id) = manager_session_id.as_deref() {
        apply_session_participants(flow, &mut group, &group_id, session_id).await?;
    }

    ensure_task_dispatch_allowed(&group)?;
    if !is_task_manager(&group, &cmd.driver_bot_id) {
        return Err(ServiceError::Unauthorized(
            task_manager_error_message(&group, "dispatch tasks").to_string(),
        ));
    }

    // Resolve target bot: match by bot_uuid first, then bot_name.
    // If neither matches, fall back to registry capabilities.name.
    let mut target = group
        .participants
        .iter()
        .find(|participant| {
            participant.bot_uuid == cmd.target_bot_id
                || participant.bot_name.as_deref() == Some(cmd.target_bot_id.as_str())
        })
        .cloned();
    if target.is_none() {
        for p in group.participants.iter().filter(|p| p.is_bot()) {
            if let Some(bot) = flow.registry.get(&p.bot_uuid).await {
                if bot.capabilities.name.as_deref() == Some(cmd.target_bot_id.as_str()) {
                    target = Some(p.clone());
                    break;
                }
            }
        }
    }
    let Some(target) = target else {
        emit_unknown_task_target_notice(
            flow,
            &group,
            &group_id,
            manager_session_id.as_deref(),
            &cmd.target_bot_id,
        )
        .await;
        return Err(ServiceError::BotNotFound(cmd.target_bot_id.clone()));
    };
    let target_mode = target
        .mode
        .unwrap_or_else(|| ParticipantMode::default_for(target.actor_kind));
    if target_mode == ParticipantMode::Muted {
        return Err(ServiceError::InvalidOperation {
            message: "target bot is muted".to_string(),
            request_id: None,
        });
    }
    let target_bot_id = target.bot_uuid.clone();

    // Resolve target_bot_name: explicit param > participant.bot_name >
    // registry capabilities.name > bot_uuid fallback.
    let target_bot_name = if let Some(name) = cmd.target_bot_name.clone() {
        name
    } else if let Some(name) = target.bot_name.clone().filter(|n| !n.is_empty()) {
        name
    } else {
        flow.registry
            .get(&target_bot_id)
            .await
            .and_then(|bot| bot.capabilities.name.clone())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| target_bot_id.clone())
    };
    let message = task_message(&cmd.payload);
    let manager_session_id = manager_session_id.unwrap_or_else(|| group_id.clone());
    let task_id = uuid::Uuid::new_v4().to_string();
    let now = now_ms();

    // Outbound interceptor chain runs BEFORE the task is registered so a
    // Block decision never leaves a phantom task in the store. Apply chain
    // first, then register only on success.
    let effective_task_id = match crate::group_flow::apply_task_interceptors(
        flow,
        &group_id,
        &cmd.driver_bot_id,
        &target_bot_id,
        &task_id,
        &message,
    )
    .await
    {
        Ok(id) => id,
        Err(reason) => {
            tracing::warn!(
                interceptor = %reason.interceptor_id,
                code = %reason.code,
                task = %task_id,
                "task dispatch blocked by interceptor chain"
            );
            return Err(ServiceError::Forbidden(if reason.user_visible {
                reason.message
            } else {
                "task dispatch blocked by policy".to_string()
            }));
        }
    };

    let response_mode = task_response_mode(&group, &cmd.payload);
    if let Some(drain) = crate::queued_task::admission_mode(flow, &group, &manager_session_id, &target_bot_id).await? {
        let driver = group.participants.iter().find(|p| p.bot_uuid == cmd.driver_bot_id)
            .ok_or_else(|| crate::queued_task::error("task manager missing"))?;
        let name = resolve_participant_name(flow, driver).await;
        let intent = crate::queued_task::TaskIntent { leg:crate::queued_task::TaskLeg::Dispatch,
            task_id:effective_task_id.clone(), manager:cmd.driver_bot_id.clone(), worker:target_bot_id,
            worker_name:target_bot_name, response_mode };
        let admission = crate::queued_task::command(flow, &group, &manager_session_id, intent,
            &message, cmd.payload.get("attachments"), &name, drain).await?;
        crate::queued_task::admit(flow, admission).await?;
        let entry = flow.task_store.get(&effective_task_id).await
            .ok_or_else(|| crate::queued_task::error("admitted task projection missing"))?;
        record_task_event(flow, "task.assigned", &entry, &cmd.driver_bot_id, now,
            BTreeMap::from([
                ("task_id".into(), serde_json::json!(entry.task_id)),
                ("manager_id".into(), serde_json::json!(entry.driver_bot)),
                ("worker_id".into(), serde_json::json!(entry.target_bot)),
                ("session_id".into(), serde_json::json!(entry.session_id)),
                ("status".into(), serde_json::json!("queued")),
                ("assignment".into(), serde_json::json!({"content_type":"text/plain", "size_bytes":message.len(), "text":message, "truncated":false})),
            ])).await?;
        emit_task_ledger_status(flow, &group, &group_id, Some(&manager_session_id), &cmd.driver_bot_id).await;
        return Ok(TaskDispatchOutcome { task_id:effective_task_id, status:"queued".into(),
            bot_deliveries:Vec::new(), frontend_deliveries:Vec::new() });
    }
    let task_entry = new_task_entry(
        effective_task_id.clone(),
        group_id.clone(),
        (manager_session_id != group_id).then(|| manager_session_id.clone()),
        cmd.driver_bot_id.clone(),
        target_bot_id.clone(),
        Some(target_bot_name.clone()),
        now,
        response_mode,
    );
    let mut assignment_data = BTreeMap::from([
        (
            "task_id".to_string(),
            serde_json::json!(effective_task_id.clone()),
        ),
        (
            "manager_id".to_string(),
            serde_json::json!(cmd.driver_bot_id.clone()),
        ),
        (
            "worker_id".to_string(),
            serde_json::json!(target_bot_id.clone()),
        ),
        (
            "assignment".to_string(),
            serde_json::json!({
                "content_type": "text/plain",
                "size_bytes": message.len(),
                "text": message.clone(),
                "truncated": false
            }),
        ),
    ]);
    if let Some(session_id) = task_entry.session_id.as_ref() {
        assignment_data.insert("session_id".to_string(), serde_json::json!(session_id));
    }
    record_task_event(
        flow,
        "task.assigned",
        &task_entry,
        &cmd.driver_bot_id,
        now,
        assignment_data,
    )
    .await?;
    flow.task_store.register(task_entry).await;

    // Resolve driver_name with registry fallback: prefer
    // participant.bot_name, then registry capabilities.name, then bot_uuid.
    let driver_name = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == cmd.driver_bot_id)
        .and_then(|p| p.bot_name.clone().filter(|n| !n.is_empty()))
        .or_else(|| {
            // async in sync context — we resolve via a separate step
            None // fall through
        });
    // Registry fallback for driver_name must be async — handled inline below.
    let driver_name = match driver_name {
        Some(name) => name,
        None => flow
            .registry
            .get(&cmd.driver_bot_id)
            .await
            .and_then(|bot| bot.capabilities.name.clone())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| cmd.driver_bot_id.clone()),
    };

    let ledger_session_id = (manager_session_id != group_id).then_some(manager_session_id.as_str());
    log_task_dispatch_created(
        &group_id,
        &manager_session_id,
        &effective_task_id,
        &cmd.driver_bot_id,
        &target_bot_id,
        &message,
    );
    let delivery_target = match flow.registry.resolve_delivery_target(&target_bot_id).await {
        Ok(target) => target,
        Err(error) => {
            let error_text = error.to_string();
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                false,
                Some(error_text.as_str()),
                Some("resolve_target"),
            );
            flow.task_store.mark_failed(&effective_task_id).await;
            emit_task_ledger_status(
                flow,
                &group,
                &group_id,
                ledger_session_id,
                &cmd.driver_bot_id,
            )
            .await;
            return Err(error);
        }
    };
    let provider_tags = if delivery_target.is_http_provider() {
        target.tags.as_slice()
    } else {
        &[]
    };
    let frame = build_task_dispatch_frame(
        &group,
        &cmd.driver_bot_id,
        &driver_name,
        &target_bot_id,
        &target_bot_name,
        &message,
        &effective_task_id,
        &manager_session_id,
        provider_tags,
        now,
    );
    let delivery_kind = BotDeliveryKind::TaskDispatch;
    flow.register_send_context(
        DeliveryType::Send,
        &delivery_target,
        &frame,
        &effective_task_id,
        &target_bot_id,
        &group_id,
        Some(&manager_session_id),
        &[],
    )
    .await?;
    let result = match flow
        .bot_delivery
        .deliver(BotDeliveryCommand {
            target: delivery_target,
            run_id: effective_task_id.clone(),
            frame,
            delivery_kind,
            provider_transport: Default::default(),
            provider_bypass_headers: Vec::new(),
        })
        .await
    {
        Ok(result) if result.delivered => {
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                true,
                result.error.as_ref().map(ToString::to_string).as_deref(),
                None,
            );
            if group.group_strategy == GroupStrategy::ManagerWorker {
                crate::group_flow::try_persist_group_message(
                    flow,
                    &group_id,
                    Some(&manager_session_id),
                    &cmd.driver_bot_id,
                    SenderType::Bot,
                    "chat",
                    Value::String(message.clone()),
                    None,
                    Some(target_bot_id.clone()),
                    &effective_task_id,
                )
                .await?;
            }
            result
        }
        Ok(result) => {
            flow.discard_send_context(&effective_task_id).await?;
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                false,
                result.error.as_ref().map(ToString::to_string).as_deref(),
                Some("deliver"),
            );
            flow.task_store.mark_failed(&effective_task_id).await;
            emit_task_ledger_status(
                flow,
                &group,
                &group_id,
                ledger_session_id,
                &cmd.driver_bot_id,
            )
            .await;
            return Err(ServiceError::InvalidOperation {
                message: "target bot is not connected".to_string(),
                request_id: Some(effective_task_id),
            });
        }
        Err(error) => {
            flow.discard_send_context(&effective_task_id).await?;
            let error_text = error.to_string();
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &effective_task_id,
                &target_bot_id,
                Some(&cmd.driver_bot_id),
                DeliveryType::Send,
                false,
                Some(error_text.as_str()),
                Some("deliver"),
            );
            flow.task_store.mark_failed(&effective_task_id).await;
            emit_task_ledger_status(
                flow,
                &group,
                &group_id,
                ledger_session_id,
                &cmd.driver_bot_id,
            )
            .await;
            return Err(error);
        }
    };

    emit_task_ledger_status(
        flow,
        &group,
        &group_id,
        ledger_session_id,
        &cmd.driver_bot_id,
    )
    .await;

    Ok(TaskDispatchOutcome {
        task_id: effective_task_id,
        status: "dispatched".to_string(),
        bot_deliveries: vec![result],
        frontend_deliveries: Vec::new(),
    })
}
