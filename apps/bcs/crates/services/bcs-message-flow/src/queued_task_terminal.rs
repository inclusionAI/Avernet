//! Atomic task completion and independent Manager result admission.
use crate::{BcsMessageFlow, queued_task::{self, TaskLeg, TaskIntent}};
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::{BotEventCommand, ChatEventState, ServiceResult, DeliveryTransitionCommand, ManagedDeliveryError};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;

pub(crate) async fn commit(flow: &BcsMessageFlow, row: &PersistedMessageDelivery,
    cmd: &BotEventCommand, result_text: &str, normalized: crate::run_reply::RunReply,
) -> ServiceResult<()> {
    let mut task = queued_task::intent(row)?.ok_or_else(|| queued_task::error("task terminal intent missing"))?;
    if task.leg != TaskLeg::Dispatch { return Err(queued_task::error("task terminal is not an assignment")); }
    task.leg = TaskLeg::Result;
    // Completion is a fact about an already authorized run. Membership changes
    // must not strand its lane; current authorization is checked before the
    // independent Manager delivery performs network I/O.
    let session = flow.session_management.as_ref()
        .ok_or_else(|| queued_task::error("task result session service unavailable"))?
        .get(&row.session_id).await.map_err(|e| queued_task::error(&format!("task result session read failed: {e}")))?;
    if session.as_ref().is_some_and(|session| session.group_id != row.group_id) {
        return Err(queued_task::error("task result session scope mismatch"));
    }
    let tags = session.and_then(|session| session.participants.into_iter()
        .find(|p| p.bot_uuid == task.manager).map(|p| p.tags)).unwrap_or_default();
    let now = chrono::Utc::now().timestamp_millis();
    // Protected result routing (caller-enabled Manager): decide BEFORE the
    // admission builds so the projection carries its own material. The
    // §15.4 gate otherwise refuses the ordinary content dispatch and every
    // result leg toward a caller-enabled Manager strands terminal-failed.
    let protected_text = terminal_result_text(cmd, result_text, &normalized);
    let parked = protect_result_leg(flow, &task, &row.session_id, &row.group_id, &protected_text, now).await?;
    let projection = crate::queued_group::QueuedGroupProjection::task_result(
        row, task.clone(), tags, parked.as_ref().map(|_| protected_text.as_str()))?;
    let (visibility_domain, audience) = crate::group_flow::persisted_message_visibility(
        None, &task.worker, bcs_domain::SenderType::Bot, "chat", None)?;
    let reply = bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries {
        display_message:None, message_id:result_message_id(&task.task_id), event:None,
        message:bcs_domain::NewMessage { group_id:row.group_id.clone(), session_id:row.session_id.clone(),
            sender_id:task.worker.clone(), sender_type:bcs_domain::SenderType::Bot, message_type:"run_reply".into(),
            content:serde_json::json!({}), client_msg_id:None, owner_bot_id:None, visibility_domain, audience,
            created_at:now as u64, run_id:cmd.run_id.clone() },
        flow_kind:bcs_domain::message_delivery::DeliveryFlowKind::Task,
        targets:vec![bcs_service_api::port::repo::message_delivery::DeliveryAdmissionTarget {
            rejection:None, target_bot_id:task.manager.clone(), kind:bcs_domain::DeliveryType::Send,
            max_queued:100, semantic_projection_json:serde_json::to_value(projection)?, run_id: None,
}],
        now_ms:now, expire_at_ms:None,
    };
    let mut terminal = cmd.clone();
    if terminal.state == ChatEventState::Aborted && terminal.event_payload.get("reason").and_then(|v| v.as_str()).is_none() {
        if let Some(reason) = row.transport_context_json.as_ref().and_then(|v| v.get("cancel_reason")) {
            terminal.event_payload["reason"] = reason.clone();
        }
    }
    let reply = match normalize_result(flow, reply, &task, &terminal, result_text, normalized,
        crate::task_failure::is_task_timeout(row)) {
        Ok(reply) => reply,
        Err(error) => {
            release_result_leg_identity(flow, parked.as_ref()).await;
            return Err(error);
        },
    };
    let service = flow.managed_deliveries.as_ref().ok_or_else(|| queued_task::error("task queue unavailable"))?;
    let mut current = row.clone();
    for _ in 0..3 {
        let transition = DeliveryTransitionCommand { caller_dispatch_commit:None, caller_cancel:None, delivery_id:current.delivery_id.clone(), expected_state_version:current.state.state_version,
            event:match cmd.state { ChatEventState::Final => Event::Completed, ChatEventState::Aborted => Event::Aborted, _ => Event::Failed },
            now_ms:reply.now_ms, request_id:None, actor_id:None, reply:Some(reply.clone()), transport_context_json:None, deadline_at_ms:None, caller_terminal:None };
        match crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "task_terminal_commit",
            crate::storage_retry::managed_storage, || service.transition(transition.clone())).await {
            Ok(committed) => {
                queued_task::restore(flow, &committed).await?;
                // The committed result row's receipt publishes the reserved
                // identity; a refused publish stays fail-closed exactly like
                // a dispatch-leg park failure (the row converges at claim
                // time, nothing upgrades it to the ordinary transport).
                if let Some(parked) = parked.as_ref() {
                    let committed_rows = match service
                        .lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::Message(reply.message_id.clone()))
                        .await
                    {
                        Ok(rows) => rows,
                        Err(error) => {
                            tracing::warn!(task_message = %reply.message_id, %error, "result leg identity publish: committed row lookup failed; staying fail-closed at claim time");
                            Vec::new()
                        }
                    };
                    publish_result_leg_identity(flow, parked, &committed_rows, &reply.message_id, &task.manager, now).await;
                }
                return Ok(());
            }
            Err(ManagedDeliveryError::Conflict) | Err(ManagedDeliveryError::Lifecycle(_)) => {},
            Err(error) => {
                release_result_leg_identity(flow, parked.as_ref()).await;
                return Err(queued_task::error(&format!("task terminal transaction failed: {error}")));
            },
        }
        current = crate::queued_admission::find_managed_run(flow, cmd).await?
            .ok_or_else(|| queued_task::error("task terminal disappeared"))?;
        if matches!(current.state.status, Status::Completed | Status::Failed | Status::Cancelled) {
            queued_task::restore(flow, &current).await?;
            // Another terminal CAS won the race with its own result
            // admission; this reservation never meets a marked row.
            release_result_leg_identity(flow, parked.as_ref()).await;
            return Ok(());
        }
    }
    release_result_leg_identity(flow, parked.as_ref()).await;
    Err(queued_task::error("task terminal changed concurrently"))
}

/// Stable identity lets terminal callback retries read the committed result,
/// never rebuild it from a potentially different final payload.
pub(crate) fn result_message_id(task_id: &str) -> String { format!("task-result:{task_id}") }

async fn result_admission(flow: &BcsMessageFlow, group: &bcs_domain::Group, session: &str,
    task: TaskIntent, cmd: &BotEventCommand, result_text: &str, drain: bool,
) -> ServiceResult<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries> {
    let reply = queued_task::command(flow, group, session, task.clone(), result_text, None,
        &task.worker_name, drain).await?;
    let normalized = if cmd.state == ChatEventState::Error {
        let text = crate::bot_event::error_display_text(&cmd.event_payload);
        crate::run_reply::RunReply {
            text: text.clone(),
            display: flow
                .message_tracker
                .peek_chat_buf(&crate::run_reply::chat_key(cmd))
                .await
                .unwrap_or_default(),
            method: "error",
            raw_final: text,
            source_ids: Vec::new(),
        }
    } else {
        crate::run_reply::prepare(
            flow,
            cmd,
            &crate::bot_event::extract_message_text(&cmd.event_payload),
            false,
        )
        .await?
    };
    normalize_result(flow, reply, &task, cmd, result_text, normalized, false)
}

fn normalize_result(flow: &BcsMessageFlow,
    mut reply: bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries,
    task: &TaskIntent, cmd: &BotEventCommand, result_text: &str, normalized: crate::run_reply::RunReply,
    timed_out: bool,
) -> ServiceResult<bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries> {
    // The canonical result carries the response-mode projection. The full run
    // body is retained in the same source so recovery never relies on final alone.
    let result_text = match cmd.state {
        ChatEventState::Final => result_text.to_string(),
        _ => crate::task_failure::callback_body(cmd, !normalized.display.is_empty()),
    };
    let timed_out = timed_out || crate::task_failure::is_timeout_callback(cmd);
    reply.message.run_id = cmd.run_id.clone();
    reply.message_id = result_message_id(&task.task_id);
    reply.message.content = serde_json::json!({
        "task_result_text":result_text, "text":normalized.text,
        "task_state":match cmd.state {
            ChatEventState::Final => "completed",
            ChatEventState::Aborted if timed_out => "timed_out",
            ChatEventState::Aborted => "cancelled",
            _ => "failed",
        },
    });
    reply.message.client_msg_id = Some(format!("task-result:{}", task.task_id));
    reply.message.message_type = "run_reply".into();
    reply.event = None;
    if !normalized.display.is_empty() {
        let mut display = reply.message.clone();
        display.message_type = "chat".into();
        display.content = serde_json::Value::String(normalized.display);
        display.client_msg_id = Some(format!("task-display:{}", task.task_id));
        let id = uuid::Uuid::new_v4().to_string();
        let event = crate::queued_admission::prepare_message_event(flow, &id, &display)?;
        reply.display_message = Some(bcs_service_api::port::repo::message_delivery::DeliveryDisplayMessage { message_id:id, message:display, event });
    }
    Ok(reply)
}

pub(crate) async fn admit_legacy_result(flow: &BcsMessageFlow, entry: &crate::task_store::TaskEntry,
    group: &bcs_domain::Group, cmd: &BotEventCommand, result_text: &str,
) -> ServiceResult<bool> {
    let session = entry.session_id.as_deref().unwrap_or(&entry.group_id);
    let Some(drain) = queued_task::admission_mode(flow, group, session, &entry.driver_bot).await? else { return Ok(false); };
    let task = TaskIntent { leg:TaskLeg::Result, task_id:entry.task_id.clone(), manager:entry.driver_bot.clone(),
        worker:entry.target_bot.clone(), worker_name:entry.target_bot_name.clone().unwrap_or_else(|| entry.target_bot.clone()),
        response_mode:entry.response_mode, assignment_intent_id:entry.assignment_intent_id.clone(), summary:entry.summary.clone(), timeout_s:None };
    let reply = result_admission(flow, group, session, task, cmd, result_text, drain).await?;
    // Capacity rejection is itself durable. The Worker has finished even if
    // the Manager result cannot be admitted; never re-execute the Worker.
    flow.managed_deliveries.as_ref().ok_or_else(|| queued_task::error("task queue unavailable"))?
        .admit(reply).await.map_err(|e| queued_task::error(&format!("task result admission failed: {e}")))?;
    crate::task_failure::mark_terminal(flow, &entry.task_id, &cmd.state).await;
    Ok(true)
}

// ---- Protected result leg toward a caller-enabled Manager ----
//
// The §15.4 caller delivery gate refuses ORDINARY content dispatch to any
// caller-enabled bot, so a task result toward a caller-enabled Manager must
// ride the SAME protected transport the session's human turns use. The
// material is the RESULT's own (review P1.III.2 keeps the worker's dispatch
// facts off it): the canonical result text as the protected task text and
// the worker as the trusted ordinary channel sender. Identity comes from the
// session-capture relay's per-turn budget — the same redeem the dispatch leg
// uses — and parks on the exact claim-side scope the root dispatch's
// handoff_claim consumes (readiness.rs: park/claim scope drift is a silent
// permanent identity loss).

/// Parked identity for one protected result leg. The committing admission
/// must publish it against the committed row, or release the reservation.
pub(crate) struct ResultLegIdentity {
    scope: bcs_service_api::application::caller_handoff::HandoffScope,
    ingress: bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress,
}

/// The state-dependent text the protected dispatch would carry (the digest
/// input, spec §7.5.1) — the same rule `normalize_result` applies, so the
/// marked `caller_task` never disagrees with the canonical result body.
fn terminal_result_text(cmd: &BotEventCommand, result_text: &str, normalized: &crate::run_reply::RunReply) -> String {
    match cmd.state {
        ChatEventState::Final => result_text.to_string(),
        _ => crate::task_failure::callback_body(cmd, !normalized.display.is_empty()),
    }
}

/// Decide the protected routing for a result leg and RESERVE its identity
/// slot BEFORE the queue commit (the plan-A admission race: a tick landing
/// between commit and publish parks instead of converging). None keeps the
/// leg's ordinary shape — no caller-enabled Manager, no plain result text,
/// no live session capture, or a refused reservation: each is a legitimate
/// stay-ordinary outcome, never a half-marked row.
pub(crate) async fn protect_result_leg(
    flow: &BcsMessageFlow,
    task: &TaskIntent,
    session_id: &str,
    group_id: &str,
    protected_text: &str,
    now_ms: i64,
) -> ServiceResult<Option<ResultLegIdentity>> {
    if protected_text.trim().is_empty() {
        // Plain-text protected dispatch material only (v1), mirroring the
        // root admission's text rule; an empty body keeps the leg ordinary.
        return Ok(None);
    }
    let (Some(deps), Some(relay)) = (flow.caller_admission.as_ref(), flow.caller_relay.as_ref()) else {
        return Ok(None);
    };
    let env = bcs_config::resolve_env_str();
    let caller_enabled = deps
        .control_plane
        .get_caller_configuration(&task.manager, &env)
        .await?
        .and_then(|config| config.snapshot)
        .is_some_and(|snapshot| snapshot.caller_identity.enabled);
    if !caller_enabled {
        return Ok(None);
    }
    let Some((_, ingress)) = relay.ledger.redeem(&env, session_id, now_ms) else {
        tracing::info!(
            task_id = %task.task_id,
            manager = %task.manager,
            "result leg: no live session capture for the protected route; the leg stays ordinary"
        );
        return Ok(None);
    };
    let scope = crate::caller_dispatch_pipeline::dispatch_handoff_scope(
        &env,
        session_id,
        Some(group_id),
        &result_message_id(&task.task_id),
        &task.manager,
    );
    if let Err(error) = deps.handoff.reserve(&scope, deps.reserve_ttl_ms, now_ms).await {
        tracing::info!(
            task_id = %task.task_id,
            manager = %task.manager,
            error = %error,
            "result leg: identity slot reservation refused; the leg stays ordinary"
        );
        return Ok(None);
    }
    tracing::info!(
        task_id = %task.task_id,
        from = %task.worker,
        to = %task.manager,
        "RESULT_LEG_PARKED: the session's captured human identity was parked onto the manager's result-leg scope"
    );
    Ok(Some(ResultLegIdentity { scope, ingress }))
}

/// Publish the parked identity against the committed result row. A refused
/// publish stays fail-closed exactly like a dispatch-leg park failure (the
/// marked row converges at claim time; nothing upgrades it to the ordinary
/// transport). `committed_rows` are the rows the admission just created (the
/// caller filters them down to the Manager target before or at this call).
pub(crate) async fn publish_result_leg_identity(
    flow: &BcsMessageFlow,
    parked: &ResultLegIdentity,
    committed_rows: &[PersistedMessageDelivery],
    message_id: &str,
    manager: &str,
    now_ms: i64,
) {
    let Some(deps) = flow.caller_admission.as_ref() else { return };
    let Some(row) = committed_rows.iter().find(|row| row.target_bot_id == manager) else {
        tracing::warn!(task_message = %message_id, "result leg identity publish: committed result row not found; staying fail-closed at claim time");
        let _ = deps.handoff.release(&parked.scope).await;
        return;
    };
    if let Err(error) = deps
        .handoff
        .publish(
            &parked.scope,
            parked.ingress.clone(),
            bcs_service_api::application::caller_handoff::QueuedAdmissionReceipt::new(
                row.delivery_id.clone(),
                row.state.state_version,
                now_ms,
            ),
        )
        .await
    {
        tracing::warn!(task_message = %message_id, %error, "RESULT_LEG_PARK_FAILED: identity did not park; the result row stays fail-closed at claim time");
    }
}

/// Release an un-published reservation (the admission never committed, or a
/// racing terminal CAS settled the lane first).
pub(crate) async fn release_result_leg_identity(
    flow: &BcsMessageFlow,
    parked: Option<&ResultLegIdentity>,
) {
    let Some(parked) = parked else { return };
    let Some(deps) = flow.caller_admission.as_ref() else { return };
    let _ = deps.handoff.release(&parked.scope).await;
}

#[cfg(test)]
mod result_leg_identity_tests;
