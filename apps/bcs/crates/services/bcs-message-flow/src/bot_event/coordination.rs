use super::*;
use tracing::debug;

pub(super) struct CoordinationEchoDispatch {
    task_id: Option<String>,
    pub(super) bot_deliveries: Vec<BotDeliveryResult>,
    pub(super) frontend_deliveries: Vec<FrontendDeliveryResult>,
}

pub(super) async fn maybe_handle_coordination_echo(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    data: &Value,
    task_intent_eligible: bool,
) -> ServiceResult<Option<CoordinationEchoDispatch>> {
    if !task_intent_eligible || cmd.event_type != "agent" || cmd.group_id.is_empty() {
        return Ok(None);
    }
    if data.get("isError").and_then(Value::as_bool) != Some(false) {
        return Ok(None);
    }

    let Some(result_text) = tool_result_text(data) else {
        return Ok(None);
    };
    let Some(call) = CoordinationCall::from_stdout(&result_text) else {
        return Ok(None);
    };
    if !coordination_tool_name_allowed(flow, cmd, data, &call).await {
        warn!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool_name = ?data.get("name").and_then(|value| value.as_str()),
            "Ignoring coordination echo from unsupported tool name"
        );
        return Ok(None);
    }

    let tool_call_id = data
        .get("toolCallId")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let Some(tool_call_id) = tool_call_id else {
        warn!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool = %call.tool,
            "Ignoring coordination echo without toolCallId"
        );
        return Ok(None);
    };
    claim_and_dispatch(flow, cmd, call, tool_call_id.to_string()).await
}


/// Shared claim→dispatch core for every coordination intake channel (the
/// WS agent-event echo and the chat-final scan below): one place enforcing
/// the v2 claim ledger semantics — run-context liveness/match, claim-once,
/// post-claim run revalidation — before handing the resolved arguments to
/// the protocol dispatch. Behavior is byte-identical to the former inline
/// tail of `maybe_handle_coordination_echo`.
async fn claim_and_dispatch(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    call: CoordinationCall,
    tool_call_id: String,
) -> ServiceResult<Option<CoordinationEchoDispatch>> {
    // The original tail expects `tool_call_id` as a &str bound; reuse the
    // verbatim body with a shadowed &str so the extracted code stays
    // byte-level identical inside.
    let tool_call_id = &tool_call_id[..];
    if let Some(intent_id) = call.intent_id.as_deref() {
        let port = flow.coordination_intents.as_ref()
            .ok_or_else(|| ServiceError::InternalError("coordination_store_unavailable".into()))?;
        let context = match &flow.bot_run_context {
            Some(runs) => runs.get_context(&cmd.run_id).await,
            None => None,
        }.ok_or_else(|| ServiceError::Forbidden("coordination_run_not_found".into()))?;
        if context.terminal || context.deadline_ms <= now_ms() || context.bot_id != cmd.bot_id
            || context.group_id != cmd.group_id || context.bcs_session_id != cmd.bcs_session_id {
            return Err(ServiceError::Forbidden("coordination_run_mismatch_or_terminated".into()));
        }
        let identity = CoordinationContext {
            bot_id: context.bot_id, group_id: context.group_id, session_id: context.bcs_session_id,
            run_id: context.run_id, tool_call_id: tool_call_id.to_string(),
        };
        let lease = match port.resolve_and_claim(intent_id, &call.tool, &identity, context.deadline_ms).await? {
            CoordinationClaim::Acquired(lease) => lease,
            CoordinationClaim::Duplicate(Some(result)) if result.status == CoordinationStatus::Applied => return Ok(None),
            CoordinationClaim::Duplicate(Some(result)) if result.status == CoordinationStatus::Failed => return Ok(None),
            CoordinationClaim::Duplicate(_) => {
                if call.tool == TOOL_ASSIGN_TASK {
                    crate::task_rejection::notice(flow, &TaskDispatchCommand {
                        caller_delegation: None,
                        driver_bot_id:cmd.bot_id.clone(), group_id:cmd.group_id.clone(), target_bot_id:String::new(), target_bot_name:None,
                        payload:serde_json::json!({"assignment_intent_id":call.intent_id, "bcs_session_id":cmd.bcs_session_id}),
                    }, None).await?;
                }
                return Err(ServiceError::Conflict("coordination_previous_outcome_unknown_or_failed".into()));
            },
        };
        let active = match &flow.bot_run_context {
            Some(runs) => runs.get_context(&cmd.run_id).await,
            None => None,
        };
        if !active.is_some_and(|run| !run.terminal && run.deadline_ms > now_ms()
            && run.bot_id == identity.bot_id && run.group_id == identity.group_id
            && run.bcs_session_id == identity.session_id) {
            port.finish(intent_id, &identity, &lease.claim_token, &CoordinationResult {
                status: CoordinationStatus::Failed, task_id: None,
                error_code: Some("run_terminated_before_execution".into()),
            }).await?;
            return Err(ServiceError::Forbidden("coordination_run_terminated".into()));
        }
        let mut resolved = call.clone();
        resolved.arguments = lease.arguments;
        let dispatched = dispatch_coordination_call(flow, cmd, &resolved, Some(&tool_call_id)).await;
        let result = match &dispatched {
            Ok(Some(outcome)) => CoordinationResult { status: CoordinationStatus::Applied,
                task_id: outcome.task_id.clone(), error_code: None },
            Err(error) if crate::task_rejection::is_rejected(error) => CoordinationResult {
                status: CoordinationStatus::Failed, task_id: None, error_code: Some("task_dispatch_rejected".into()) },
            _ => CoordinationResult { status: CoordinationStatus::Unknown,
                task_id: None, error_code: Some("coordination_execution_not_confirmed".into()) },
        };
        port.finish(intent_id, &identity, &lease.claim_token, &result).await?;
        return match dispatched {
            Ok(None) => Err(ServiceError::InternalError("coordination_execution_not_confirmed".into())),
            other => other,
        };
    }
    let dedup_key = format!("{}:{}", cmd.run_id, tool_call_id);
    if !flow
        .message_tracker
        .mark_coordination_echo_seen(dedup_key.clone(), now_ms(), COORDINATION_PROCESSED_TTL_MS)
        .await
    {
        info!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool_call_id = %tool_call_id,
            dedup_key = %dedup_key,
            "Skipping duplicate coordination echo"
        );
        return Ok(None);
    }

    dispatch_coordination_call(flow, cmd, &call, Some(&tool_call_id)).await
}



/// Chat-final claim channel for provider manager bots, which have no
/// agent-event tool_call_end echo (their harness sends back only
/// `chat.event` finals). The sanctioned ecosystem contract already makes
/// managers echo the full raw MCP output in their group reply ("…回传完整
/// 原始输出…否则 BCS 无法识别 MCP 调用结果"), so the terminal final's text
/// carries the v2 stored-receipt frame; this scan turns that reply into
/// the claim trigger the deferred protocol always prescribed.
/// SCOPE — additive, caller-only:
///
/// - G1 gate: the claim only runs when the session has a live
///   owner-to-caller identity capture (the human spoke in this session),
///   so groups without one keep the exact pre-feature behavior (a stored
///   intent stays a silent orphan);
/// - group-strategy discipline stays in the task engine:
///   `ensure_task_dispatch_allowed` only admits manager_worker /
///   master_slave groups (existing gate);
/// - failures are best-effort and silent at debug level: the carrier is
///   LLM-mediated text — a missing/unknown/expired/mismatched frame is
///   noise, not a protocol violation. The WS echo path (which has
///   stronger provenance) keeps its group-treatment notify; this channel
///   never emits one.
pub(super) async fn maybe_handle_coordination_chat_final(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    final_text: &str,
) -> ServiceResult<Option<CoordinationEchoDispatch>> {
    // Hot-path gates first, cheapest to most expensive: only terminal
    // group chat finals with the coordination magic in the reply text.
    if cmd.group_id.is_empty()
        || cmd.bcs_session_id.as_deref().unwrap_or_default().is_empty()
        || !is_terminal_chat_event(cmd)
    {
        return Ok(None);
    }
    if !final_text.contains(bcs_protocol::ws::coordination::MAGIC_KEY) {
        return Ok(None);
    }
    // G1: only when the session holds the live owner identity capture —
    // absent state keeps the stored intent silent (the exact legacy shape).
    let Some(relay) = flow.caller_relay.as_ref() else {
        return Ok(None);
    };
    let now = now_ms() as i64;
    if !relay.ledger.has_live_capture(
        &bcs_config::resolve_env_str(),
        cmd.bcs_session_id.as_deref().unwrap_or_default(),
        now as i64,
    ) {
        debug!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            "chat-final coordination frame skipped: no live owner capture for the session"
        );
        return Ok(None);
    }
    let Some(call) = CoordinationCall::from_stdout(final_text) else {
        debug!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            "chat-final text carried the coordination magic but no parseable frame"
        );
        return Ok(None);
    };
    let Some(intent_id) = call.intent_id.as_deref().filter(|id| !id.is_empty()) else {
        // v1 frames carry inline arguments with no stored intent: the
        // chat-final carrier has no provenance for a direct dispatch, so
        // skip rather than guess.
        debug!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            "chat-final coordination frame skipped: v1 frame without intent_id"
        );
        return Ok(None);
    };
    // Tool-surface allowlist: the frame's self-declared tool must be one of
    // the coordination commands, and the bot's coordination surface must
    // permit the echo. Manager bots calling through mcporter are the
    // legacy surface, which tolerates (and historically relies on)
    // name-free carriers.
    if !coordination_tool_name_allowed(flow, cmd, &Value::Null, &call).await {
        debug!(
            bot_id = %cmd.bot_id,
            group_id = %cmd.group_id,
            run_id = %cmd.run_id,
            tool = %call.tool,
            "chat-final coordination frame skipped: tool surface not allowed for the bot"
        );
        return Ok(None);
    }
    // The chat text is a carrier WITHOUT a per-call tool identity — the
    // claim record uses the intent itself as the instance key. claim-once
    // on the intent (InsertOnly) is the real anti-replay bound; this
    // synthesized id exists for audit/dedup bookkeeping parity.
    let tool_call_id = format!("chat-final:{intent_id}");
    match claim_and_dispatch(flow, cmd, call, tool_call_id).await {
        Ok(dispatch) => Ok(dispatch),
        Err(error) => {
            // The carrier is LLM text: any claim-phase failure (unknown
            // intent, expired, run gap/mismatch) is expected noise, never
            // a group-side alarm. A post-claim dispatch failure still
            // surfaces through its own task-flow warnings.
            debug!(
                bot_id = %cmd.bot_id,
                group_id = %cmd.group_id,
                run_id = %cmd.run_id,
                error = %error,
                "chat-final coordination claim did not complete"
            );
            Ok(None)
        }
    }
}

fn is_terminal_chat_event(cmd: &BotEventCommand) -> bool {
    matches!(cmd.event_type.as_str(), "chat" | "chat.event")
        && (cmd.state == ChatEventState::Final || cmd.state == ChatEventState::Error)
}

#[allow(clippy::too_many_arguments)]
pub(super) async fn dispatch_coordination_call(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    call: &CoordinationCall,
    tool_call_id: Option<&str>,
) -> ServiceResult<Option<CoordinationEchoDispatch>> {
    match call.tool.as_str() {
        TOOL_ASSIGN_TASK => {
            let Some(target_bot_id) = coordination_argument_str(call, "target_bot") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_assign_task echo without target_bot"
                );
                return Ok(None);
            };
            let Some(message) = coordination_argument_str(call, "message") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_assign_task echo without message"
                );
                return Ok(None);
            };
            let mut payload = serde_json::json!({
                "message": message,
                "assignment_intent_id": call.intent_id,
            });
            if let Some(timeout) = call.arguments.get("timeout_s") { payload["timeout_s"] = timeout.clone(); }
            if let Some(response_mode) = coordination_argument_str(call, "response_mode") {
                payload["response_mode"] = Value::String(response_mode.to_string());
            }
            if let Some(session_id) = cmd.bcs_session_id.as_deref() {
                payload["bcs_session_id"] = Value::String(session_id.to_string());
            }
            // Identity inheritance consults the existing execution facts. No
            // child registration follows admission: the delivery projection
            // persists the non-secret parent/invocation association atomically.
            let delegation = crate::caller_delegation::prepare_assignment(
                flow, cmd, tool_call_id.unwrap_or_default(), target_bot_id,
            ).await?;
            match crate::task_flow::handle_task_dispatch(
                flow,
                TaskDispatchCommand {
                    driver_bot_id: cmd.bot_id.clone(),
                    group_id: cmd.group_id.clone(),
                    target_bot_id: target_bot_id.to_string(),
                    target_bot_name: None,
                    payload,
                    caller_delegation: delegation.clone(),
                },
            )
            .await
            {
                Ok(outcome) => {
                    Ok(Some(CoordinationEchoDispatch {
                        task_id: Some(outcome.task_id),
                        bot_deliveries: outcome.bot_deliveries,
                        frontend_deliveries: outcome.frontend_deliveries,
                    }))
                }
                Err(error) => {
                    warn!(
                        bot_id = %cmd.bot_id,
                        group_id = %cmd.group_id,
                        run_id = %cmd.run_id,
                        error = %error,
                        "Failed to dispatch bcs_assign_task coordination echo"
                    );
                    if call.v == 2 && !crate::task_rejection::is_rejected(&error) {
                        crate::task_rejection::notice(flow, &TaskDispatchCommand {
                            caller_delegation: None,
                            driver_bot_id:cmd.bot_id.clone(), group_id:cmd.group_id.clone(),
                            target_bot_id:target_bot_id.into(), target_bot_name:None,
                            payload:serde_json::json!({"assignment_intent_id":call.intent_id, "bcs_session_id":cmd.bcs_session_id}),
                        }, None).await?;
                    }
                    if call.v == 2 { Err(error) } else { Ok(None) }
                }
            }
        }
        TOOL_SEND_TASK_MESSAGE => {
            let Some(session_id) = cmd.bcs_session_id.as_deref() else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_send_task_message echo without bcs_session_id"
                );
                return Ok(None);
            };
            let Some(message) = coordination_argument_str(call, "message") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_send_task_message echo without message"
                );
                return Ok(None);
            };
            match crate::task_flow::handle_task_message(
                flow,
                TaskMessageCommand {
                    worker_bot_id: cmd.bot_id.clone(),
                    group_id: cmd.group_id.clone(),
                    payload: serde_json::json!({
                        "message": message,
                        "bcs_session_id": session_id,
                    }),
                },
            )
            .await
            {
                Ok(outcome) => Ok(Some(CoordinationEchoDispatch {
                    task_id: None,
                    bot_deliveries: outcome.bot_deliveries,
                    frontend_deliveries: outcome.frontend_deliveries,
                })),
                Err(error) => {
                    warn!(
                        bot_id = %cmd.bot_id,
                        group_id = %cmd.group_id,
                        run_id = %cmd.run_id,
                        error = %error,
                        "Failed to dispatch bcs_send_task_message coordination echo"
                    );
                    if call.v == 2 { Err(error) } else { Ok(None) }
                }
            }
        }
        TOOL_TASK_COMPLETE => {
            let Some(summary) = coordination_argument_str(call, "summary") else {
                warn!(
                    bot_id = %cmd.bot_id,
                    group_id = %cmd.group_id,
                    "Ignoring bcs_task_complete echo without summary"
                );
                return Ok(None);
            };
            let mut payload = serde_json::json!({
                "group_id": cmd.group_id.as_str(),
                "summary": summary,
                "status": "completed",
            });
            if let Some(session_id) = cmd.bcs_session_id.as_deref() {
                payload["bcs_session_id"] = Value::String(session_id.to_string());
            }
            match crate::task_flow::handle_task_complete(
                flow,
                TaskCompleteCommand {
                    task_id: cmd.group_id.clone(),
                    bot_id: cmd.bot_id.clone(),
                    via_echo: true,
                    payload,
                },
            )
            .await
            {
                Ok(outcome) => {
                    if outcome.blocked {
                        warn!(
                            bot_id = %cmd.bot_id,
                            group_id = %cmd.group_id,
                            run_id = %cmd.run_id,
                            pending = ?outcome.pending,
                            "Task completion coordination echo blocked by pending targets"
                        );
                        return Ok(None);
                    }
                    Ok(Some(CoordinationEchoDispatch {
                        task_id: None,
                        bot_deliveries: Vec::new(),
                        frontend_deliveries: outcome.frontend_deliveries,
                    }))
                }
                Err(error) => {
                    warn!(
                        bot_id = %cmd.bot_id,
                        group_id = %cmd.group_id,
                        run_id = %cmd.run_id,
                        error = %error,
                        "Failed to dispatch bcs_task_complete coordination echo"
                    );
                    if call.v == 2 { Err(error) } else { Ok(None) }
                }
            }
        }
        _ => {
            warn!(
                bot_id = %cmd.bot_id,
                group_id = %cmd.group_id,
                run_id = %cmd.run_id,
                tool = %call.tool,
                "Ignoring coordination echo for unsupported tool"
            );
            Ok(None)
        }
    }
}

pub(super) async fn coordination_tool_name_allowed(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
    data: &Value,
    call: &CoordinationCall,
) -> bool {
    let surface = coordination_surface_for_run(flow, cmd).await;
    if let Some(surface) = surface {
        return match surface.mode {
            CoordinationMode::NativeMcp => native_mcp_tool_name_allowed(&surface, data, call),
            CoordinationMode::McporterMcp | CoordinationMode::LegacyUpstream => {
                legacy_coordination_tool_name_allowed(data)
            }
            CoordinationMode::NativeTool | CoordinationMode::Disabled => false,
        };
    } else {
        return false;
    }
}

pub(super) async fn coordination_surface_for_run(
    flow: &BcsMessageFlow,
    cmd: &BotEventCommand,
) -> Option<CoordinationSurface> {
    if let Some(cached) = flow
        .message_tracker
        .coordination_surface(&cmd.run_id, &cmd.bot_id)
        .await
    {
        return cached;
    }

    let resolved = match flow
        .registry
        .resolve_coordination_surface(&cmd.bot_id)
        .await
    {
        Ok(surface) => Some(surface),
        Err(error) => {
            warn!(
                bot_id = %cmd.bot_id,
                group_id = %cmd.group_id,
                run_id = %cmd.run_id,
                error = %error,
                "Ignoring coordination echo because the coordination surface could not be resolved"
            );
            None
        }
    };
    flow.message_tracker
        .cache_coordination_surface(&cmd.run_id, &cmd.bot_id, resolved.clone())
        .await;
    resolved
}

pub(super) fn native_mcp_tool_name_allowed(
    surface: &CoordinationSurface,
    data: &Value,
    call: &CoordinationCall,
) -> bool {
    let Some(tool_name) = data
        .get("name")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return false;
    };
    // COSEC: Provider tool names are untrusted event input. Exact lookup binds
    // the result to the configured native MCP surface; aliases and prefixes
    // must not gain coordination side effects.
    let Some(canonical_tool) = surface.tool_name_mapping.get(tool_name) else {
        return false;
    };
    // COSEC: The signed/mapped source name and the echoed envelope must agree,
    // otherwise one mapped tool could smuggle another coordination operation.
    canonical_tool == &call.tool
}

pub(super) fn legacy_coordination_tool_name_allowed(data: &Value) -> bool {
    let Some(name) = data.get("name").and_then(|value| value.as_str()) else {
        // Claude Code command_output callbacks do not always carry the source
        // tool name; authenticated event intake plus task-flow role checks
        // still gate the side effect after the coordination magic is parsed.
        return true;
    };
    let name = name.trim();
    if name.is_empty() {
        return true;
    }
    matches!(
        name.to_ascii_lowercase().as_str(),
        "exec" | "bash" | "shell" | "mcporter"
    )
}

pub(super) fn tool_result_text(data: &Value) -> Option<String> {
    let result = data.get("result")?;
    if let Some(text) = result.as_str().filter(|value| !value.is_empty()) {
        return Some(text.to_string());
    }
    let mut text = String::new();
    let mut found_text = false;
    for block in result
        .get("content")
        .and_then(|value| value.as_array())
        .into_iter()
        .flatten()
    {
        if let Some(block_text) = block.get("text").and_then(|value| value.as_str()) {
            text.push_str(block_text);
            found_text = true;
        }
    }
    found_text.then_some(text)
}

pub(super) fn coordination_argument_str<'a>(call: &'a CoordinationCall, key: &str) -> Option<&'a str> {
    call.arguments
        .get(key)
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
}
