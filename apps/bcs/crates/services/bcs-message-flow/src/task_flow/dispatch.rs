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

    if group.group_strategy == GroupStrategy::ManagerWorker {
        if let (Some(sessions), Some(session_id)) = (&flow.session_management, manager_session_id.as_deref()) {
            let session = sessions.get(session_id).await.map_err(|e| ServiceError::InternalError(e.to_string()))?.ok_or_else(|| ServiceError::SessionNotFound(session_id.into()))?;
            if session.status != SessionStatus::Running {
                return Err(crate::task_rejection::reject(flow, &cmd, ServiceError::InvalidOperation {
                    message:"task Session is not running".into(), request_id:None,
                }).await);
            }
        }
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
        emit_unknown_task_target_notice(flow, &group, &group_id, manager_session_id.as_deref(), &cmd.target_bot_id).await;
        return Err(crate::task_rejection::reject(flow, &cmd, ServiceError::BotNotFound(cmd.target_bot_id.clone())).await);
    };
    if group.group_strategy == GroupStrategy::ManagerWorker && target.role != ParticipantRole::Worker {
        return Err(crate::task_rejection::reject(flow, &cmd, ServiceError::InvalidOperation {
            message:"target is not a Worker".into(), request_id:None,
        }).await);
    }
    let target_mode = target
        .mode
        .unwrap_or_else(|| ParticipantMode::default_for(target.actor_kind));
    if target_mode == ParticipantMode::Muted {
        return Err(crate::task_rejection::reject(flow, &cmd, ServiceError::InvalidOperation { message:"target bot is muted".into(), request_id:None }).await);
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
            return Err(crate::task_rejection::reject(flow, &cmd,
                ServiceError::Forbidden(if reason.user_visible { reason.message } else { "task dispatch blocked by policy".into() })).await);
        }
    };

    let response_mode = task_response_mode(&group, &cmd.payload);
    let timeout_s = if let Some(value) = cmd.payload.get("timeout_s") {
        let requested = value.as_u64().filter(|v| *v > 0).ok_or_else(|| ServiceError::InvalidOperation {
            message: "timeout_s must be a positive integer".into(), request_id: None,
        })?;
        let policy = match &flow.delivery_policy {
            Some(live) => live.snapshot.read().await.policy.clone(),
            None => bcs_config_api::message_delivery::DeliveryPolicy::default(),
        };
        Some(requested.clamp(policy.task_timeout_min_s, policy.task_timeout_max_s))
    } else { None };
    if let Some(drain) = crate::queued_task::admission_mode(flow, &group, &manager_session_id, &target_bot_id).await? {
        let driver = group.participants.iter().find(|p| p.bot_uuid == cmd.driver_bot_id)
            .ok_or_else(|| crate::queued_task::error("task manager missing"))?;
        let name = resolve_participant_name(flow, driver).await;
        let intent = crate::queued_task::TaskIntent { leg:crate::queued_task::TaskLeg::Dispatch,
            task_id:effective_task_id.clone(), manager:cmd.driver_bot_id.clone(), worker:target_bot_id.clone(),
            worker_name:target_bot_name, response_mode,
            assignment_intent_id:cmd.payload.get("assignment_intent_id").and_then(Value::as_str).map(str::to_owned),
            summary:message.chars().take(80).collect(), timeout_s };
        let mut admission = crate::queued_task::command(flow, &group, &manager_session_id, intent,
            &message, cmd.payload.get("attachments"), &name, drain).await?;
        // Existing admission owns the real task/run identity. Persist only
        // the trusted coordination edge's non-secret parent/invocation and
        // issuance purpose; no separate child registration or lifecycle.
        if let Some(delegation) = cmd.caller_delegation.as_ref() {
            // Target's own caller configuration decides the issuance
            // purpose (spec §8.3, review P1-2): the SAME per-target read
            // web_send performs (control-plane enabled snapshot).
            // Caller-enabled Worker → Execution identity; an absent or
            // disabled config → DelegationOnly middle-hop — never a
            // blanket purpose either way.
            let target_caller_enabled = match flow.caller_admission.as_ref() {
                Some(deps) => deps
                    .control_plane
                    .get_caller_configuration(&target_bot_id, &bcs_config::resolve_env_str())
                    .await
                    .ok()
                    .flatten()
                    .and_then(|config| config.snapshot)
                    .is_some_and(|snapshot| snapshot.caller_identity.enabled),
                None => false,
            };
            let purpose = if target_caller_enabled { "execution" } else { "delegation_only" };
            for target in admission.targets.iter_mut() {
                if let Value::Object(map) = &mut target.semantic_projection_json {
                    map.insert("caller_required".into(), Value::Bool(true));
                    map.insert("caller_task".into(), Value::String(message.clone()));
                    // Trusted ordinary sender facts of the assign's own
                    // sender (the Manager): the protected dispatch guard reads
                    // them BEFORE choosing the delegate branch, and the
                    // delegate wire derives its envelope `from` from them.
                    // Same position and trust level as the queued admission
                    // seam's `caller_channel` (canonical sender id + resolved
                    // participant name) — never the protected subject.
                    map.insert("caller_channel".into(), serde_json::json!({
                        "actor_id": cmd.driver_bot_id.clone(),
                        "actor_name": name.clone(),
                    }));
                    // Nested under the DECLARED caller_delegation field: the
                    // strict projection decode (deny_unknown_fields) accepts
                    // the row on every task path (run this line through
                    // intent/terminal/reply decoding unchanged).
                    let mut nested = serde_json::Map::new();
                    nested.insert("parent_run_id".to_string(), Value::String(delegation.parent_run_id.clone()));
                    nested.insert("parent_attempt".to_string(), Value::from(delegation.parent_attempt));
                    nested.insert("parent_bot_id".to_string(), Value::String(delegation.parent_bot_id.clone()));
                    nested.insert("invocation_id".to_string(), Value::String(delegation.invocation_id.clone()));
                    nested.insert("execution_purpose".to_string(), Value::String(purpose.to_string()));
                    map.insert("caller_delegation".into(), Value::Object(nested));
                }
            }
        }
        // HEAD's output::admit_queued_dispatch discipline: the run
        // identity, the operation registration and the frozen producer
        // facts must precede the durable admit commit (F1); ours' caller
        // projection above rides the same admission targets untouched.
        let mut admission = admission;
        if let Some(target) = admission.targets.first_mut() {
            target.run_id = Some(uuid::Uuid::new_v4().to_string());
        }
        let real_run_id = admission.targets.first().and_then(|t| t.run_id.clone());
        let committed_row = output::admit_queued_dispatch(flow, &group, admission).await?;
        // Owner→caller identity relay (spec L62 direction): with the
        // deployment's allowlist naming this exact (middle, target) edge,
        // redeem the session's captured human ingress and park it on the
        // worker's handoff scope — built from the COMMITTED row's own facts
        // so the runtime's claim reads the identical key. The middle bot
        // touches no token; budget and TTL are ledger-bound; any refusal
        // stays fail-closed exactly like relay-disabled deployments (the
        // row converges IdentityUnavailable later, nothing upgrades it).
        if let Some(relay) = flow.caller_relay.as_ref() {
            if cmd.caller_delegation.is_some()
                && relay.allows_relay_edge(&cmd.driver_bot_id, &target_bot_id)
            {
                match (flow.caller_admission.as_ref(), committed_row.as_ref(),
                       relay.ledger.redeem(&bcs_config::resolve_env_str(), &manager_session_id, now as i64)) {
                    (Some(deps), Some(row), Some((root_turn, ingress))) => {
                        let scope = HandoffScope {
                            env: bcs_config::resolve_env_str(),
                            session_id: row.session_id.clone(),
                            group_id: Some(row.group_id.clone()),
                            source_message_id: row.source_message_id.clone(),
                            target_bot_id: row.target_bot_id.clone(),
                        };
                        let receipt = QueuedAdmissionReceipt::new(
                            row.delivery_id.clone(), row.state.state_version, now as i64,
                        );
                        let parked = match deps.handoff.reserve(&scope, deps.reserve_ttl_ms, now as i64).await {
                            Ok(()) => deps.handoff.publish(&scope, ingress, receipt).await,
                            Err(error) => Err(error),
                        };
                        match parked {
                            Ok(()) => tracing::info!(
                                task = %effective_task_id,
                                from = %cmd.driver_bot_id,
                                to = %target_bot_id,
                                root_turn = %root_turn,
                                "CALLER_RELAY_PARKED: the session's captured human identity was relayed onto the worker's handoff scope"
                            ),
                            Err(error) => tracing::warn!(
                                task = %effective_task_id,
                                error = %error,
                                "CALLER_RELAY_PARK_FAILED: identity did not park; the task stays fail-closed at claim time"
                            ),
                        }
                    }
                    (Some(_), Some(_), None) => tracing::warn!(
                        task = %effective_task_id,
                        "CALLER_RELAY_REFUSED: no live captured identity for this session (expired, unbudgeted or never spoken); the task stays fail-closed"
                    ),
                    (None, _, _) => tracing::warn!(
                        task = %effective_task_id,
                        "CALLER_RELAY_REFUSED: relay edge allowed but no caller admission seam; the task stays fail-closed"
                    ),
                    _ => {}
                }
            }
        }
        // finish_admission bookkeeping already ran inside
        // admit_queued_dispatch; the entry read below stands in for the
        // old acknowledged-row handle (real_run_id carries the shared identity).
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
            real_run_id,
            bot_deliveries:Vec::new(), frontend_deliveries:Vec::new() });
    }
    if cmd.caller_delegation.is_some() {
        return Err(crate::queued_task::error("caller delegation requires managed task admission"));
    }
    let operation_id = output::admit_output(
        flow, &group, &manager_session_id, &effective_task_id, &target_bot_id,
    ).await?;
    let mut task_entry = new_task_entry(
        effective_task_id.clone(),
        group_id.clone(),
        (manager_session_id != group_id).then(|| manager_session_id.clone()),
        cmd.driver_bot_id.clone(),
        target_bot_id.clone(),
        Some(target_bot_name.clone()),
        now,
        response_mode,
    );
    task_entry.assignment_intent_id = cmd.payload.get("assignment_intent_id").and_then(Value::as_str).map(str::to_owned);
    task_entry.summary = message.chars().take(80).collect();
    task_entry.timeout_s = timeout_s;
    let mut assignment_data = BTreeMap::from([
        ("assignment_intent_id".into(), serde_json::json!(task_entry.assignment_intent_id)),
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
    if let Err(error) = record_task_event(
        flow,
        "task.assigned",
        &task_entry,
        &cmd.driver_bot_id,
        now,
        assignment_data,
    )
    .await {
        flow.release_output_runs(&operation_id, std::slice::from_ref(&effective_task_id)).await;
        return Err(error);
    }
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
            flow.release_output_runs(&operation_id, std::slice::from_ref(&effective_task_id)).await;
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
            crate::task_failure::finish_legacy(flow, &effective_task_id, false, "未能连接到 Worker，任务尚未开始执行。").await?;
            return Err(if cmd.payload.get("assignment_intent_id").and_then(Value::as_str).is_some() {
                crate::task_rejection::reported_failure(error.to_string())
            } else { error });
        }
    };
    let provider_tags = if delivery_target.is_http_provider() {
        target.tags.as_slice()
    } else {
        &[]
    };
    let mut frame = build_task_dispatch_frame(
        &group,
        &cmd.driver_bot_id,
        &driver_name,
        &target_bot_id,
        &target_bot_name,
        &message,
        &effective_task_id,
        cmd.payload.get("assignment_intent_id").and_then(Value::as_str),
        &manager_session_id,
        provider_tags,
        now,
    );
    if delivery_target.is_http_provider() {
        if let (Some(seconds), BcsFrame::Request(request)) = (timeout_s, &mut frame) {
            if let Some(params) = request.params.as_mut() {
                let budget_ms = seconds.saturating_mul(1000)
                    .saturating_add(crate::delivery_runtime::TASK_ABORT_RESERVE.as_millis() as u64)
                    .max(flow.provider_chat_run_timeout_ms);
                params["timeout_ms"] = serde_json::json!(budget_ms);
            }
        }
    }
    let delivery_kind = BotDeliveryKind::TaskDispatch;
    if let Err(error) = flow.register_send_context(
        DeliveryType::Send,
        &delivery_target,
        &frame,
        &effective_task_id,
        &target_bot_id,
        &group_id,
        Some(&manager_session_id),
        &[],
    )
    .await {
        flow.release_output_runs(&operation_id, std::slice::from_ref(&effective_task_id)).await;
        return Err(error);
    }
    if group.group_strategy == GroupStrategy::ManagerWorker {
        crate::task_handle::schedule_legacy_timeout(flow, effective_task_id.clone(), timeout_s.unwrap_or(crate::task_store::TASK_TTL_MS / 1000));
    }
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
            flow.release_output_runs(&operation_id, std::slice::from_ref(&effective_task_id)).await;
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
            crate::task_failure::finish_legacy(flow, &effective_task_id, false, "Worker 未接受本次任务，任务未完成。").await?;
            return Err(if cmd.payload.get("assignment_intent_id").and_then(Value::as_str).is_some() {
                crate::task_rejection::reported_failure("target bot is not connected".into())
            } else { ServiceError::InvalidOperation {
                message:"target bot is not connected".into(), request_id:Some(effective_task_id),
            } });
        }
        Err(error) => {
            // The Worker may have accepted the request. Keep BOTH output permit
            // and ingress context so late callbacks retain their original Run.
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
            // A transport error alone does not prove Worker failure. Keep the
            // task pending for an authoritative callback or existing timeout.
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

    // The direct (legacy) branch never carries a delegation: the protected
    // worker path is queue-backed by construction; a delegation here is a
    // wiring error and refuses closed rather than dispatching ordinary.
    if cmd.caller_delegation.is_some() {
        return Err(ServiceError::InvalidOperation {
            message: "caller delegation requires the queued protected path; direct dispatch refused".to_string(),
            request_id: Some(effective_task_id),
        });
    }
    Ok(TaskDispatchOutcome {
        task_id: effective_task_id,
        status: "dispatched".to_string(),
        // The direct branch's frame identity is the task id itself (the
        // immediate chat.send the remainder of the legacy flow drives).
        real_run_id: None,
        bot_deliveries: vec![result],
        frontend_deliveries: Vec::new(),
    })
}
