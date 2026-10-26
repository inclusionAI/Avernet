//! Bot EventFrame handling: legacy/V3 normalization, run-scope validation,
//! V3 sequence bookkeeping, default group event routing, and event logging.

use std::sync::Arc;

use bcs_protocol::stream::TASK_INTENT_ELIGIBLE_KEY;
use bcs_protocol::{
    AgentEventPayload, AgentStream, ChatEventPayload, ChatEventState, EventFrame,
};
use bcs_service_api::{BotEventCommand, BotRunContext, ChatEventState as AppChatEventState, ServiceError};
use opentelemetry::Context;
use opentelemetry::trace::TraceContextExt;
use serde_json::Value;
use tracing::{Instrument, info, info_span, warn};
use tracing_opentelemetry::OpenTelemetrySpanExt;

use super::trace::record_ws_bot_response_trace;
use super::unwrap_outbound_group_id;
use super::{BotDispatchState, BotWsDispatchError, Result};
use crate::bot::run_event_v3::NormalizedBotEvent;

fn legacy_default_session_id(group_id: &str) -> Option<String> {
    if group_id.is_empty() || group_id.contains(':') {
        None
    } else {
        Some(format!("{group_id}:00000000"))
    }
}


/// Resolve wire compatibility against the original accepted Run. An omitted
/// legacy Session is not a request to switch to a synthesized default Session.
fn registered_event_scope(
    context: BotRunContext,
    bot_id: &str,
    wire_group_id: &str,
    explicit_session_id: Option<&str>,
) -> Result<(String, Option<String>)> {
    let wire_scope = unwrap_outbound_group_id(wire_group_id, explicit_session_id);
    if context.group_id.is_empty() {
        // Direct A2A uses its caller-supplied session key in the legacy group
        // field; preserve this separate protocol contract.
        return Ok(if context.bot_id == bot_id {
            (String::new(), context.bcs_session_id.or(wire_scope.1))
        } else { wire_scope });
    }
    let session = context.bcs_session_id.unwrap_or_else(|| context.group_id.clone());
    let wire_group = unwrap_outbound_group_id(wire_group_id, None).0;
    if context.bot_id != bot_id || wire_group != context.group_id
        || wire_scope.0 != context.group_id
        || wire_scope.1.as_ref().is_some_and(|id| id != &session)
    {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "legacy event run/Bot/Group/Session identity mismatch".into(),
        ));
    }
    // Some(group_id) is the existing Group-only Run identity. Preserve it as
    // well as real Session ids so later fallback cannot rewrite accepted scope.
    Ok((context.group_id, Some(session)))
}

async fn resolve_bot_event_scope(
    state: &BotDispatchState,
    bot_id: &str,
    run_id: &str,
    wire_group_id: &str,
    explicit_session_id: Option<&str>,
) -> Result<(String, Option<String>)> {
    let Some(context) = state.bot_run_context.get_context(run_id).await else {
        return Ok(unwrap_outbound_group_id(wire_group_id, explicit_session_id));
    };
    registered_event_scope(context, bot_id, wire_group_id, explicit_session_id)
}

/// V3 帳本校验：外层 frame seq 与 payload seq 一致、run 存在、身份匹配。
/// 不含终态/过期检查——调用方需要在 agent/chat 专属的 terminal_fingerprint
/// 幂等重放检查之后才做终态/过期判断（否则一个合法的终态重放帧会被误判为
/// "run已终态"而报错，而不是被静默忽略）。也不含 reserve_run_event_seq
/// 占用——agent/chat 在流式管道内提交/回滚，Interaction 在
/// handle_interaction_event_v3 内对同一 per-run 账本做 reserve/commit/release
/// （对齐 HTTP SSE 的 SeqDedup 记账），两条路径各自管理占用时机。
/// pub(super) 可见性:bot 模块下的同级子模块 run_event_v3 需要调用它
/// (run_event_v3.rs 自身的 pub(super) 函数同属这一可见性约定)。
pub(in crate::bot) async fn validate_v3_run_scope(
    state: &BotDispatchState,
    bot_id: &str,
    run_id: &str,
    session_id: &str,
    seq: u64,
    outer_seq: Option<u64>,
) -> Result<BotRunContext> {
    if outer_seq.is_some_and(|outer| outer != seq) {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 frame seq does not match payload seq".into(),
        ));
    }
    let context = state
        .bot_run_context
        .get_context(run_id)
        .await
        .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event runId is unknown".into()))?;
    if context.bot_id != bot_id || context.bcs_session_id.as_deref() != Some(session_id) {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 event run/session identity mismatch".into(),
        ));
    }
    Ok(context)
}

/// Handle an EventFrame from a bot.
///
/// EventFrames are streaming events from bots during chat sessions.
/// Dispatches to the appropriate handler based on the group's service_mode.
pub(super) async fn handle_event_frame(
    state: &Arc<BotDispatchState>,
    event: &EventFrame,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    let bot_id = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            log_bot_event("unknown", event);
            warn!(request_id = %bcs_observability::CurrentRequestId, event = %event.event, "Received EventFrame from unregistered bot");
            return Ok(());
        }
    };

    log_bot_event(&bot_id, event);

    let connection = state
        .bot_connections
        .protocol(&bot_id)
        .await
        .unwrap_or(crate::bot::BotConnectionProtocol {
            protocol_version: 1,
            client_kind: None,
        });
    let is_v3 = connection.protocol_version >= 3;
    let (
        run_id,
        bcs_group_id,
        event_type,
        mut event_payload,
        event_state,
        is_final,
        v3_session_id,
        v3_seq,
    ) = if is_v3 {
        match crate::bot::run_event_v3::classify_v3_event(event)? {
            crate::bot::run_event_v3::V3RunEvent::Interaction(interaction) => {
                return crate::bot::run_event_v3::handle_interaction_event_v3(
                    state,
                    &bot_id,
                    interaction,
                    event.seq,
                )
                .await;
            }
            crate::bot::run_event_v3::V3RunEvent::Normalized(normalized) => normalized,
        }
    } else {
        let Some(normalized) = normalize_legacy_event(event) else {
            match event.event.as_str() {
                "agent" => warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    "Failed to parse agent event payload"
                ),
                "chat.event" => warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    "Failed to parse chat event payload"
                ),
                _ => warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    event = %event.event,
                    "Unknown event type"
                ),
            }
            return Ok(());
        };
        normalized
    };

    // This key is internal-only. Legacy clients cannot opt themselves into
    // task intent by smuggling it in their event payload.
    let task_intent_enabled = is_v3
        && matches!(connection.client_kind.as_deref(), Some("native_mcp" | "mcporter_mcp"));
    if !task_intent_enabled {
        if let Some(object) = event_payload.as_object_mut() {
            object.remove(TASK_INTENT_ELIGIBLE_KEY);
        }
    }

    let terminal_fingerprint = if is_v3
        && is_terminal_bot_event(&event_type, &event_state, is_final)
    {
        serde_json::to_string(&(&event_type, &event_payload)).ok()
    } else {
        None
    };
    let mut reserved_v3_seq = None;

    let (real_group_id, mut bcs_session_id) = if is_v3 {
        let session_id = v3_session_id
            .as_deref()
            .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event missing sessionId".into()))?;
        let seq = v3_seq
            .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event missing seq".into()))?;
        let context = validate_v3_run_scope(state, &bot_id, &run_id, session_id, seq, event.seq).await?;
        if let Some(fingerprint) = terminal_fingerprint.as_deref() {
            if state
                .bot_connections
                .is_terminal_replay(&bot_id, &run_id, seq, fingerprint)
                .await
            {
                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    seq,
                    "Ignoring idempotent V3 terminal replay"
                );
                return Ok(());
            }
        }
        if context.terminal || context.deadline_ms <= bcs_protocol::now_ms() {
            return Err(BotWsDispatchError::InvalidFrameFormat(
                "V3 event run is terminal or expired".into(),
            ));
        }
        if !state
            .bot_connections
            .reserve_run_event_seq(&bot_id, &run_id, seq)
            .await
        {
            return Err(BotWsDispatchError::InvalidFrameFormat(
                "V3 event seq is duplicate or regressed".into(),
            ));
        }
        reserved_v3_seq = Some(seq);
        (context.group_id, Some(session_id.to_string()))
    } else {
        let explicit_session_id = event_payload.get("bcs_session_id").and_then(|v| v.as_str());
        resolve_bot_event_scope(
            state,
            &bot_id,
            &run_id,
            &bcs_group_id,
            explicit_session_id,
        )
        .await?
    };
    if !is_v3 && bcs_session_id.is_none() {
        if let Some(mapped_session_id) = state.run_channels.session_for_run(&run_id).await {
            let restored_session_id = if mapped_session_id.contains(':') {
                Some(mapped_session_id)
            } else {
                legacy_default_session_id(&mapped_session_id)
            };
            if let Some(restored_session_id) = restored_session_id {
                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    bcs_group_id = %bcs_group_id,
                    bcs_session_id = %restored_session_id,
                    restore_source = "run_mapping",
                    "Bot event session restored"
                );
                bcs_session_id = Some(restored_session_id);
            }
        }
    }
    if !is_v3 && bcs_session_id.is_none() {
        if let Some(fallback_session_id) = legacy_default_session_id(&real_group_id) {
            info!(
                bot_id = %bot_id,
                run_id = %run_id,
                bcs_group_id = %bcs_group_id,
                bcs_session_id = %fallback_session_id,
                restore_source = "legacy_default",
                "Bot event session restored"
            );
            bcs_session_id = Some(fallback_session_id);
        }
    }

    let delivery_correlation = state
        .collaboration_runtime
        .lookup_delivery_correlation(&run_id)
        .await
        .map_err(|error| {
            BotWsDispatchError::ServiceError(ServiceError::InternalError(error.to_string()))
        });
    let delivery_correlation = match delivery_correlation {
        Ok(correlation) => correlation,
        Err(error) => {
            return finish_v3_sequence(
                state,
                &bot_id,
                &run_id,
                reserved_v3_seq,
                terminal_fingerprint,
                Err(error),
            )
            .await;
        }
    };
    if delivery_correlation.is_some() {
        let terminal = is_terminal_bot_event(&event_type, &event_state, is_final);
        info!(
            bot_id = %bot_id,
            run_id = %run_id,
            event_type = %event_type,
            response_span_created = false,
            response_span_skip_reason = "collaboration_runtime",
            "Bot response tracing skipped"
        );
        let result = state
            .collaboration_runtime
            .handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
                bot_id: bot_id.clone(),
                run_id: run_id.clone(),
                event_type: event_type.clone(),
                event_payload: event_payload.clone(),
                state: to_app_chat_event_state(&event_state),
                bcs_session_id: bcs_session_id.clone(),
            })
            .await
            .map_err(|error| {
                BotWsDispatchError::ServiceError(ServiceError::InternalError(error.to_string()))
            });
        if result.is_ok() && terminal {
            state.run_channels.unregister(&run_id).await;
        }
        return finish_v3_sequence(
            state,
            &bot_id,
            &run_id,
            reserved_v3_seq,
            terminal_fingerprint,
            result.map(|_| ()),
        )
        .await;
    }

    let trace_parent = if event_type == "chat.event" {
        state.run_channels.trace_parent(&run_id).await
    } else {
        None
    };
    let dispatch_result = if let Some(trace_parent) = trace_parent {
        info!(
            bot_id = %bot_id,
            run_id = %run_id,
            event_type = %event_type,
            parent_trace_id = %trace_parent.trace_id(),
            parent_span_id = %trace_parent.span_id(),
            response_span_created = true,
            "Creating traced Bot response span"
        );
        let span = info_span!(
            target: "bcn_otel",
            "bcn.bot.response",
            otel.kind = "consumer",
        );
        let _ = span.set_parent(Context::new().with_remote_span_context(trace_parent));
        async {
            handle_default_group_event(
                state,
                &bot_id,
                &run_id,
                &real_group_id,
                bcs_session_id.as_deref(),
                &event_type,
                &event_payload,
                &event_state,
                is_final,
            )
            .await?;
            record_ws_bot_response_trace(&bot_id, &run_id, &event_state, &event_payload);
            Ok::<(), BotWsDispatchError>(())
        }
        .instrument(span)
        .await
    } else {
        if event_type == "chat.event" {
            info!(
                bot_id = %bot_id,
                run_id = %run_id,
                event_type = %event_type,
                response_span_created = false,
                response_span_skip_reason = "missing_run_trace_context",
                "Bot response tracing skipped"
            );
        }
        handle_default_group_event(
            state,
            &bot_id,
            &run_id,
            &real_group_id,
            bcs_session_id.as_deref(),
            &event_type,
            &event_payload,
            &event_state,
            is_final,
        )
        .await
    };

    finish_v3_sequence(
        state,
        &bot_id,
        &run_id,
        reserved_v3_seq,
        terminal_fingerprint,
        dispatch_result,
    )
    .await
}

fn is_terminal_bot_event(
    event_type: &str,
    event_state: &ChatEventState,
    is_final: bool,
) -> bool {
    matches!(
        event_state,
        ChatEventState::Final | ChatEventState::Aborted | ChatEventState::Error
    ) || event_type == "agent" && is_final
}

async fn finish_v3_sequence(
    state: &BotDispatchState,
    bot_id: &str,
    run_id: &str,
    reserved_seq: Option<u64>,
    terminal_fingerprint: Option<String>,
    result: Result<()>,
) -> Result<()> {
    let Some(seq) = reserved_seq else {
        return result;
    };
    if result.is_ok() {
        if !state
            .bot_connections
            .commit_run_event_seq(bot_id, run_id, seq, terminal_fingerprint)
            .await
        {
            warn!(
                bot_id,
                run_id,
                seq,
                "V3 event succeeded after its connection sequence state was removed"
            );
        }
    } else {
        state
            .bot_connections
            .release_run_event_seq(bot_id, run_id, seq)
            .await;
    }
    result
}

// ---------------------------------------------------------------------------
// Event routing strategies by service_mode
// ---------------------------------------------------------------------------

/// Default group event routing: broadcast via OutboundDispatcher.
async fn handle_default_group_event(
    state: &Arc<BotDispatchState>,
    bot_id: &str,
    run_id: &str,
    bcs_group_id: &str,
    bcs_session_id: Option<&str>,
    event_type: &str,
    event_payload: &Value,
    event_state: &ChatEventState,
    is_final: bool,
) -> Result<()> {
    info!(
        bot_id = %bot_id,
        run_id = %run_id,
        bcs_group_id = %bcs_group_id,
        bcs_session_id = ?bcs_session_id,
        event_type = %event_type,
        event_state = ?event_state,
        is_final = is_final,
        ">>> dispatch_bot_event entry: bot reply received"
    );

    state
        .message_flow
        .handle_bot_event(BotEventCommand {
            bot_id: bot_id.to_string(),
            run_id: run_id.to_string(),
            group_id: bcs_group_id.to_string(),
            event_type: event_type.to_string(),
            event_payload: event_payload.clone(),
            state: to_app_chat_event_state(event_state),
            bcs_session_id: bcs_session_id.map(str::to_string),
        })
        .await?;

    if is_final {
        state.run_channels.unregister(run_id).await;
        info!(
            bot_id = %bot_id,
            run_id = %run_id,
            bcs_group_id = %bcs_group_id,
            event = %event_type,
            "Run completed, channel unregistered"
        );
    }

    Ok(())
}

fn to_app_chat_event_state(state: &ChatEventState) -> AppChatEventState {
    match state {
        ChatEventState::Delta => AppChatEventState::Delta,
        ChatEventState::Final => AppChatEventState::Final,
        ChatEventState::Aborted => AppChatEventState::Aborted,
        ChatEventState::Error => AppChatEventState::Error,
        ChatEventState::ToolCallStart => AppChatEventState::ToolCallStart,
        ChatEventState::ToolCallEnd => AppChatEventState::ToolCallEnd,
    }
}

/// Log bot event at INFO level with key payload information.
fn log_bot_event(bot_id: &str, event: &EventFrame) {
    match event.event.as_str() {
        "agent" => {
            // Try to parse agent event and extract key info
            if let Some((run_id, bcs_group_id, stream)) = parse_agent_event(&event.payload) {
                // Extract text content from data if available
                let text = event
                    .payload
                    .as_ref()
                    .and_then(|p| p.get("data"))
                    .and_then(|d| d.get("text"))
                    .or_else(|| {
                        event
                            .payload
                            .as_ref()
                            .and_then(|p| p.get("data"))
                            .and_then(|d| d.get("delta"))
                    })
                    .and_then(|t| t.as_str())
                    .unwrap_or("");

                // Truncate text for readability (use chars() to handle UTF-8 properly)
                let truncated_text = if text.chars().count() > 100 {
                    format!("{}...", text.chars().take(100).collect::<String>())
                } else {
                    text.to_string()
                };

                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    bcs_group_id = %bcs_group_id,
                    stream = ?stream,
                    text = %truncated_text,
                    "📥 [BCS] 收到 Bot 事件"
                );
            } else {
                info!(
                    bot_id = %bot_id,
                    event = %event.event,
                    payload = ?event.payload,
                    "📥 [BCS] 收到 Bot 事件 (解析失败)"
                );
            }
        }
        "chat.event" => {
            // Try to parse chat event and extract key info
            if let Some((run_id, bcs_group_id, state)) = parse_chat_event(&event.payload) {
                info!(
                    bot_id = %bot_id,
                    run_id = %run_id,
                    bcs_group_id = %bcs_group_id,
                    state = ?state,
                    "📥 [BCS] 收到 Bot 事件"
                );
            } else {
                info!(
                    bot_id = %bot_id,
                    event = %event.event,
                    payload = ?event.payload,
                    "📥 [BCS] 收到 Bot 事件 (解析失败)"
                );
            }
        }
        _ => {
            info!(
                bot_id = %bot_id,
                event = %event.event,
                payload = ?event.payload,
                "📥 [BCS] 收到 Bot 事件 (未知类型)"
            );
        }
    }
}

/// Parse an agent event payload to extract run_id, bcs_group_id, and stream type.
fn parse_agent_event(payload: &Option<Value>) -> Option<(String, String, AgentStream)> {
    let payload = payload.as_ref()?;
    let agent_event: AgentEventPayload = serde_json::from_value(payload.clone()).ok()?;
    Some((
        agent_event.run_id,
        agent_event.bcs_group_id,
        agent_event.stream,
    ))
}

/// Parse a chat event payload to extract run_id, bcs_group_id, and state.
fn parse_chat_event(payload: &Option<Value>) -> Option<(String, String, ChatEventState)> {
    let payload = payload.as_ref()?;
    let chat_event: ChatEventPayload = serde_json::from_value(payload.clone()).ok()?;
    Some((chat_event.run_id, chat_event.bcs_group_id, chat_event.state))
}

fn normalize_legacy_event(event: &EventFrame) -> Option<NormalizedBotEvent> {
    match event.event.as_str() {
        "agent" => {
            let (run_id, group_id, stream) = parse_agent_event(&event.payload)?;
            let is_final = matches!(stream, AgentStream::Error);
            Some((
                run_id,
                group_id,
                "agent".to_string(),
                event.payload.clone().unwrap_or(Value::Null),
                ChatEventState::Delta,
                is_final,
                None,
                None,
            ))
        }
        "chat.event" => {
            let (run_id, group_id, state) = parse_chat_event(&event.payload)?;
            let is_final = matches!(
                state,
                ChatEventState::Final | ChatEventState::Aborted | ChatEventState::Error
            );
            Some((
                run_id,
                group_id,
                "chat.event".to_string(),
                event.payload.clone().unwrap_or(Value::Null),
                state,
                is_final,
                None,
                None,
            ))
        }
        _ => None,
    }
}

#[cfg(test)]
#[path = "event_scope_tests.rs"]
mod scope_tests;
