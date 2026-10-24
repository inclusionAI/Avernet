//! Bot ResponseFrame handling: acknowledgement reconciliation, alias
//! projection, message logging, and state-machine delivery aliases.

use std::sync::Arc;

use bcs_protocol::{BcsFrame, EventFrame, ResponseFrame};
use bcs_service_api::{
    BotEventCommand, ChatEventState as AppChatEventState, MESSAGE_LOG_SCHEMA_VERSION,
    ServiceError,
    MSG_LOG_TARGET, MessageLogEventType, MessageLogMode, MessageLogStatus,
    TaskRunAliasRegistration,
};
use serde_json::Value;
use tracing::{debug, info, warn};

use super::{BotDispatchState, BotWsDispatchError, Result};

/// Handle a ResponseFrame from a bot.
///
/// ResponseFrames are responses to requests initiated by BCS (e.g., chat.send, chat.abort).
/// The id in the response corresponds to the run_id for the request.
pub(super) async fn handle_response_frame(
    state: &Arc<BotDispatchState>,
    res: &ResponseFrame,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let run_id = &res.id;

    if state
        .bot_connections
        .resolve_pending_abort_request(run_id, res.clone())
        .await
    {
        debug!(request_id = %run_id, ok = res.ok, "matched chat.abort ResponseFrame");
        return Ok(());
    }

    if state
        .bot_connections
        .resolve_pending_interaction_request(run_id, res.clone())
        .await
    {
        debug!(request_id = %run_id, ok = res.ok, "matched interaction.resolve ResponseFrame");
        return Ok(());
    }

    // Check if this is a one-shot pending request (e.g., chat.history)
    let payload = if res.ok {
        res.payload.clone().unwrap_or(Value::Null)
    } else {
        serde_json::json!({
            "error": res.error.as_ref().map(|e| format!("{}: {}", e.code, e.message))
        })
    };
    state
        .bot_connections
        .resolve_pending_request(run_id, payload)
        .await;

    // Check if this is a response to a known run
    let is_known_run = state.run_channels.is_registered(run_id).await;
    log_bot_response_frame(state, run_id, res, registered_bot_id).await;

    if res.ok {
        // Success response - bot acknowledged the request
        if is_known_run {
            info!(
                run_id = %run_id,
                "Bot acknowledged request, expecting streaming events"
            );
        } else {
            // This could be a response to chat.abort or another request that was already cleaned up
            debug!(
                run_id = %run_id,
                ok = res.ok,
                "Received success ResponseFrame for request"
            );
        }

        // Reconcile any accepted run id before processing streaming events.
        handle_accepted_run_id_response(state, run_id, res, registered_bot_id).await;
        handle_state_machine_response(state, run_id, res).await;
    } else {
        // Error response - request was rejected
        let active = state.bot_run_context.find_active_run(run_id).await.map_err(|error| {
            warn!(request_id = %run_id, "chat rejection scope lookup failed; retaining run");
            BotWsDispatchError::ServiceError(error)
        })?;
        if let Some(active) = active.filter(|active| !active.scope.group_id.is_empty()) {
            if registered_bot_id.as_deref() != Some(active.scope.bot_id.as_str())
                || active.transport_owner != bcs_service_api::BotRunTransportOwner::WebSocket {
                return Err(BotWsDispatchError::InvalidFrameFormat("chat rejection owner mismatch".into()));
            }
            let state_machine = state.collaboration_runtime.lookup_delivery_correlation(run_id).await
                .map_err(|error| BotWsDispatchError::ServiceError(ServiceError::InternalError(
                    format!("chat rejection correlation lookup failed: {error}"))))?.is_some();
            if !state_machine {
            let text = res.error.as_ref().map(|error| error.message.as_str()).unwrap_or("");
            state.message_flow.handle_bot_event(BotEventCommand {
                bot_id: active.scope.bot_id,
                run_id: active.canonical_run_id.clone(),
                group_id: active.scope.group_id.clone(),
                bcs_session_id: Some(active.scope.session_id.clone()),
                event_type: "chat.event".into(),
                state: AppChatEventState::Error,
                event_payload: serde_json::json!({
                    "run_id": active.canonical_run_id, "bcs_group_id": active.scope.group_id,
                    "session_id": active.scope.session_id, "state": "error", "errorMessage": text,
                }),
            }).await?;
            return Ok(());
            }
        }
        debug!(run_id = %run_id, "chat rejection has no ordinary active group scope; using direct response path");
        let error = res
            .error
            .as_ref()
            .map(|e| format!("{}: {}", e.code, e.message))
            .unwrap_or_else(|| "Unknown error".to_string());

        if is_known_run {
            // Forward error to client and clean up
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                run_id = %run_id,
                error = %error,
                "Bot rejected request, cleaning up run channel"
            );

            // Build error event frame to send to client
            let error_event = EventFrame::new(
                "chat",
                Some(serde_json::json!({
                    "run_id": run_id,
                    "bcs_group_id": "",
                    "state": "error",
                    "error": {
                        "code": res.error.as_ref().map(|e| e.code.clone()).unwrap_or_default(),
                        "message": res.error.as_ref().map(|e| e.message.clone()).unwrap_or_default(),
                    }
                })),
                Some(0),
            );

            // Send error to client
            let event_json = serde_json::to_string(&BcsFrame::Event(error_event))?;
            state.run_channels.send_event(run_id, event_json).await;

            // Clean up the run channel
            state.run_channels.unregister(run_id).await;
        } else {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                run_id = %run_id,
                error = %error,
                "Received error ResponseFrame for unknown run"
            );
        }
    }

    Ok(())
}

async fn log_bot_response_frame(
    state: &Arc<BotDispatchState>,
    run_id: &str,
    res: &ResponseFrame,
    registered_bot_id: &Option<String>,
) {
    let run_context = state.bot_run_context.get_context(run_id).await;
    let correlation = match state
        .collaboration_runtime
        .lookup_delivery_correlation(run_id)
        .await
    {
        Ok(correlation) => correlation,
        Err(error) => {
            debug!(
                run_id = %run_id,
                error = %error,
                "message_log: failed to lookup state-machine delivery correlation"
            );
            None
        }
    };
    if run_context.is_none() && correlation.is_none() {
        return;
    }

    let state_machine_run = match correlation.as_ref() {
        Some(correlation) => match state
            .collaboration_runtime
            .get_state_machine_run(&correlation.state_machine_run_id)
            .await
        {
            Ok(run) => run,
            Err(error) => {
                debug!(
                    run_id = %run_id,
                    state_machine_run_id = %correlation.state_machine_run_id,
                    error = %error,
                    "message_log: failed to load state-machine run for response log"
                );
                None
            }
        },
        None => None,
    };

    let group_id = state_machine_run
        .as_ref()
        .map(|view| view.run.group_id.clone())
        .or_else(|| run_context.as_ref().map(|ctx| ctx.group_id.clone()))
        .unwrap_or_default();
    let session_id = state_machine_run
        .as_ref()
        .map(|view| view.run.session_id.clone())
        .or_else(|| {
            run_context
                .as_ref()
                .and_then(|ctx| ctx.bcs_session_id.clone())
        })
        .or_else(|| run_context.as_ref().map(|ctx| ctx.group_id.clone()))
        .unwrap_or_else(|| group_id.clone());
    let bot_id = correlation
        .as_ref()
        .map(|corr| corr.assignee_bot_id.clone())
        .or_else(|| run_context.as_ref().map(|ctx| ctx.bot_id.clone()))
        .or_else(|| registered_bot_id.clone())
        .unwrap_or_default();
    let accepted_run_id = response_payload_run_id(res).unwrap_or("");
    let mode = if correlation.is_some() {
        MessageLogMode::StateMachine
    } else {
        MessageLogMode::FreeChat
    };
    let event_type = if res.ok {
        MessageLogEventType::BotAccept
    } else {
        MessageLogEventType::BotReject
    };
    let status = if res.ok {
        MessageLogStatus::Accepted
    } else {
        MessageLogStatus::Rejected
    };
    let error_code = res
        .error
        .as_ref()
        .map(|error| error.code.as_str())
        .unwrap_or("");
    let error_message = res
        .error
        .as_ref()
        .map(|error| error.message.as_str())
        .unwrap_or("");
    let state_machine_run_id = correlation
        .as_ref()
        .map(|corr| corr.state_machine_run_id.as_str())
        .unwrap_or("");
    let node_id = correlation
        .as_ref()
        .map(|corr| corr.node_id.as_str())
        .unwrap_or("");
    let attempt = correlation.as_ref().map(|corr| corr.attempt).unwrap_or(0);
    let delivery_request_id = correlation
        .as_ref()
        .map(|corr| corr.delivery_request_id.as_str())
        .unwrap_or("");

    if res.ok {
        info!(
            target: MSG_LOG_TARGET,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = event_type.as_str(),
            status = status.as_str(),
            mode = mode.as_str(),
            session_id = %session_id,
            group_id = %group_id,
            run_id = %run_id,
            bot_id = %bot_id,
            accepted_run_id = %accepted_run_id,
            state_machine_run_id = %state_machine_run_id,
            node_id = %node_id,
            attempt = attempt,
            delivery_request_id = %delivery_request_id,
            error_code = %error_code,
            error = %error_message,
            "bot_accept"
        );
    } else {
        warn!(
            target: MSG_LOG_TARGET,
            request_id = %bcs_observability::CurrentRequestId,
            schema_version = MESSAGE_LOG_SCHEMA_VERSION,
            event_type = event_type.as_str(),
            status = status.as_str(),
            mode = mode.as_str(),
            session_id = %session_id,
            group_id = %group_id,
            run_id = %run_id,
            bot_id = %bot_id,
            accepted_run_id = %accepted_run_id,
            state_machine_run_id = %state_machine_run_id,
            node_id = %node_id,
            attempt = attempt,
            delivery_request_id = %delivery_request_id,
            error_code = %error_code,
            error = %error_message,
            "bot_reject"
        );
    }
}

fn response_payload_run_id(res: &ResponseFrame) -> Option<&str> {
    res.payload
        .as_ref()
        .and_then(|payload| payload.get("run_id"))
        .and_then(|value| value.as_str())
        .filter(|payload_run_id| *payload_run_id != res.id)
}

/// Reconcile a bot-accepted run id with the request run id.
async fn handle_accepted_run_id_response(
    state: &Arc<BotDispatchState>,
    run_id: &str,
    res: &ResponseFrame,
    registered_bot_id: &Option<String>,
) {
    // A managed send has a per-attempt request id distinct from its canonical
    // run id. Resolve it before projecting the engine's accepted run alias.
    if let Some(bot_id) = registered_bot_id.as_deref() {
        let downstream = res.payload.as_ref().and_then(|p| p.get("run_id")).and_then(|v| v.as_str());
        if let Err(error) = state.message_flow.record_delivery_acceptance(run_id, bot_id, downstream).await {
            warn!(%error, "failed to persist Bot delivery ACK; alias projection withheld");
            return;
        }
    }
    let active = match state.bot_run_context.find_active_run(run_id).await {
        Ok(active) => active,
        Err(error) => {
            warn!(%error, "failed to resolve accepted request alias");
            return;
        }
    };
    if active.as_ref().is_some_and(|a| registered_bot_id.as_deref() != Some(a.scope.bot_id.as_str())) {
        warn!("accepted request alias does not belong to responding Bot");
        return;
    }
    let run_id = active.as_ref().map(|a| a.canonical_run_id.as_str()).unwrap_or(run_id);
    if let Some(sub_run_id) = res
        .payload
        .as_ref()
        .and_then(|p| p.get("run_id"))
        .and_then(|v| v.as_str())
    {
        if sub_run_id != run_id {
            if let Err(error) = state
                .bot_run_context
                .bind_downstream_run_id(run_id, sub_run_id)
                .await
            {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    canonical_run_id = %run_id,
                    downstream_run_id = %sub_run_id,
                    error = %error,
                    "failed to bind downstream run id for chat.abort routing"
                );
            }
            let channel_source_message_rebound = match state
                .message_flow
                .rebind_channel_source_message(run_id, sub_run_id)
                .await
            {
                Ok(rebound) => rebound,
                Err(error) => {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        source_run_id = %run_id,
                        accepted_run_id = %sub_run_id,
                        error = %error,
                        "failed to rebind channel source message to accepted run id"
                    );
                    false
                }
            };
            let task_alias_registration = match registered_bot_id.as_deref() {
                Some(bot_id) => match state
                    .message_flow
                    .register_task_run_alias(run_id, sub_run_id, bot_id)
                    .await
                {
                    Ok(registration) => registration,
                    Err(error) => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            task_id = %run_id,
                            sub_bot_run_id = %sub_run_id,
                            bot_id = %bot_id,
                            error = %error,
                            "master_slave: failed to register sub bot run_id alias"
                        );
                        TaskRunAliasRegistration::Rejected
                    }
                },
                None => TaskRunAliasRegistration::Rejected,
            };
            let run_channel_alias_registered = match task_alias_registration {
                TaskRunAliasRegistration::Registered | TaskRunAliasRegistration::NotTask => {
                    state
                        .run_channels
                        .register_alias(sub_run_id.to_string(), run_id.to_string())
                        .await
                }
                TaskRunAliasRegistration::Rejected => false,
            };
            let direct_run_context_alias_registered = run_channel_alias_registered
                && alias_direct_run_context(
                    state,
                    run_id,
                    sub_run_id,
                    registered_bot_id.as_deref(),
                )
                .await;
            let trace_context_linked = run_channel_alias_registered
                && state.run_channels.trace_parent(sub_run_id).await.is_some();
            info!(
                task_id = %run_id,
                sub_bot_run_id = %sub_run_id,
                task_alias_registration = ?task_alias_registration,
                channel_source_message_rebound = channel_source_message_rebound,
                run_channel_alias_registered = run_channel_alias_registered,
                direct_run_context_alias_registered = direct_run_context_alias_registered,
                trace_context_linked = trace_context_linked,
                "master_slave: registered sub bot run_id alias"
            );
            log_master_slave_bot_accept(
                state,
                run_id,
                sub_run_id,
                registered_bot_id.as_deref().unwrap_or(""),
                &task_alias_registration,
                run_channel_alias_registered,
            )
            .await;
        }
    }
}

async fn alias_direct_run_context(
    state: &BotDispatchState,
    source_run_id: &str,
    alias_run_id: &str,
    registered_bot_id: Option<&str>,
) -> bool {
    let Some(mut context) = state.bot_run_context.get_context(source_run_id).await else {
        return false;
    };
    if !context.group_id.is_empty() || registered_bot_id != Some(context.bot_id.as_str()) {
        return false;
    }

    context.run_id = alias_run_id.to_string();
    state.bot_run_context.put_context(context).await;
    true
}

async fn log_master_slave_bot_accept(
    state: &Arc<BotDispatchState>,
    task_id: &str,
    accepted_run_id: &str,
    bot_id: &str,
    task_alias_registration: &TaskRunAliasRegistration,
    run_channel_alias_registered: bool,
) {
    let session_id = state
        .run_channels
        .session_for_run(task_id)
        .await
        .unwrap_or_default();
    let group_id = session_id
        .split_once(':')
        .map(|(group_id, _)| group_id.to_string())
        .unwrap_or_else(|| session_id.clone());
    info!(
        target: MSG_LOG_TARGET,
        schema_version = MESSAGE_LOG_SCHEMA_VERSION,
        event_type = MessageLogEventType::BotAccept.as_str(),
        status = MessageLogStatus::Accepted.as_str(),
        mode = MessageLogMode::ManagerWorker.as_str(),
        session_id = %session_id,
        group_id = %group_id,
        task_id = %task_id,
        run_id = %task_id,
        bot_id = %bot_id,
        accepted_run_id = %accepted_run_id,
        task_alias_registration = ?task_alias_registration,
        run_channel_alias_registered = run_channel_alias_registered,
        "bot_accept"
    );
}

async fn handle_state_machine_response(
    state: &Arc<BotDispatchState>,
    run_id: &str,
    res: &ResponseFrame,
) {
    let correlation = match state
        .collaboration_runtime
        .lookup_delivery_correlation(run_id)
        .await
    {
        Ok(Some(correlation)) => correlation,
        Ok(None) => return,
        Err(error) => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                run_id = %run_id,
                error = %error,
                "state_machine: failed to lookup delivery correlation for response"
            );
            return;
        }
    };
    let Some(bot_delivery_run_id) = res
        .payload
        .as_ref()
        .and_then(|p| p.get("run_id"))
        .and_then(|v| v.as_str())
        .filter(|bot_run_id| *bot_run_id != run_id)
        .map(str::to_string)
    else {
        return;
    };
    if let Err(error) = state
        .collaboration_runtime
        .register_delivery_alias(
            &correlation.delivery_request_id,
            bot_delivery_run_id.clone(),
        )
        .await
    {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %bot_delivery_run_id,
            error = %error,
            "state_machine: failed to register bot delivery run alias"
        );
    } else {
        info!(
            delivery_request_id = %correlation.delivery_request_id,
            bot_delivery_run_id = %bot_delivery_run_id,
            "state_machine: registered bot delivery run alias"
        );
    }
}
