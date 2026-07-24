use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::{
    BotEventCommand, BotRunContext, BotRunContextPort, ChatEventState, CollaborationRuntimeError,
    CollaborationRuntimeService, CoordinationMode, HandleBotTerminalEventCommand,
    MessageFlowService, ProviderBotCoordinationCommand, ProviderBotCoordinationOutcome,
    ProviderBotCoreService, ProviderBotEventCommand, ProviderBotEventCredential,
    ProviderBotEventError, ProviderBotEventOutcome, ProviderBotEventService,
    ProviderCoordinationConfig, ProviderCoordinationEventKind, ProviderCoordinationIntent,
    RuntimeBotIdentity, ServiceError, TaskCompleteCommand, TaskDispatchCommand, TaskMessageCommand,
};
use serde_json::{Map, Value, json};
use tokio::sync::Mutex;
use tracing::{error, info, warn};

const COORDINATION_MAGIC_KEY: &str = "__bcs_coordination__";
const CONTRACT_VERSION: u64 = 1;
const TOOL_ASSIGN_TASK: &str = "bcs_assign_task";
const TOOL_SEND_TASK_MESSAGE: &str = "bcs_send_task_message";
const TOOL_TASK_COMPLETE: &str = "bcs_task_complete";
const COORDINATION_PROCESSED_TTL_MS: u64 = 10 * 60 * 1000;

type StateMachineTerminalKey = (String, String, i32);

struct StateMachineTerminalInflightGuard {
    inflight: Arc<StdMutex<HashSet<StateMachineTerminalKey>>>,
    key: StateMachineTerminalKey,
}

impl Drop for StateMachineTerminalInflightGuard {
    fn drop(&mut self) {
        self.inflight
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .remove(&self.key);
    }
}

#[derive(Debug, Clone)]
struct CoordinationCall {
    tool: String,
    arguments: Map<String, Value>,
}

impl CoordinationCall {
    fn from_stdout(stdout: &str) -> Option<Self> {
        for line in stdout.lines() {
            let line = line.trim();
            if !line.contains(COORDINATION_MAGIC_KEY) {
                continue;
            }
            if let Some(call) = Self::parse_candidate(line) {
                return Some(call);
            }
        }

        for (idx, _) in stdout.match_indices('{') {
            let candidate = &stdout[idx..];
            if !candidate.contains(COORDINATION_MAGIC_KEY) {
                continue;
            }
            let mut stream = serde_json::Deserializer::from_str(candidate).into_iter::<Value>();
            if let Some(Ok(value)) = stream.next() {
                if let Some(call) = Self::from_value(value) {
                    return Some(call);
                }
            }
        }
        None
    }

    fn parse_candidate(candidate: &str) -> Option<Self> {
        serde_json::from_str::<Value>(candidate)
            .ok()
            .and_then(Self::from_value)
    }

    fn from_value(value: Value) -> Option<Self> {
        let object = value.as_object()?;
        let magic = object
            .get(COORDINATION_MAGIC_KEY)
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let version = object.get("v").and_then(Value::as_u64).unwrap_or(0);
        if !magic || version != CONTRACT_VERSION {
            return None;
        }
        let tool = object
            .get("tool")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())?
            .to_string();
        let arguments = object
            .get("arguments")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        Some(Self { tool, arguments })
    }
}

#[derive(Clone)]
pub struct ProviderBotEvents {
    provider_bot_core: Arc<dyn ProviderBotCoreService>,
    bot_run_context: Arc<dyn BotRunContextPort>,
    message_flow: Arc<dyn MessageFlowService>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
    coordination_seen: Arc<Mutex<HashMap<String, u64>>>,
    state_machine_terminals_inflight: Arc<StdMutex<HashSet<StateMachineTerminalKey>>>,
}

impl ProviderBotEvents {
    pub fn new(
        provider_bot_core: Arc<dyn ProviderBotCoreService>,
        bot_run_context: Arc<dyn BotRunContextPort>,
        message_flow: Arc<dyn MessageFlowService>,
    ) -> Self {
        Self {
            provider_bot_core,
            bot_run_context,
            message_flow,
            collaboration_runtime: None,
            coordination_seen: Arc::new(Mutex::new(HashMap::new())),
            state_machine_terminals_inflight: Arc::new(StdMutex::new(HashSet::new())),
        }
    }

    pub fn with_collaboration_runtime(
        mut self,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        self.collaboration_runtime = Some(collaboration_runtime);
        self
    }

    async fn authenticate_credential(
        &self,
        provider_id: &str,
        credential: &ProviderBotEventCredential,
    ) -> Result<RuntimeBotIdentity, ProviderBotEventError> {
        match credential {
            ProviderBotEventCredential::StaticBearer(token) => self
                .provider_bot_core
                .authenticate_static_bearer_event(provider_id, token)
                .await
                .map_err(map_auth_error),
            ProviderBotEventCredential::AgentPass { agent_code } => self
                .provider_bot_core
                .authenticate_agentpass_event(provider_id, agent_code)
                .await
                .map_err(map_auth_error),
            ProviderBotEventCredential::ProviderAdmin {
                provider_admin_token,
                provider_bot_ref,
            } => self
                .provider_bot_core
                .authenticate_provider_admin_event(
                    provider_id,
                    provider_admin_token,
                    provider_bot_ref,
                )
                .await
                .map_err(map_auth_error),
        }
    }

    async fn authenticate_event(
        &self,
        command: &ProviderBotEventCommand,
    ) -> Result<RuntimeBotIdentity, ProviderBotEventError> {
        self.authenticate_credential(&command.provider_id, &command.credential)
            .await
    }

    async fn authenticate_coordination(
        &self,
        command: &ProviderBotCoordinationCommand,
    ) -> Result<RuntimeBotIdentity, ProviderBotEventError> {
        self.authenticate_credential(&command.provider_id, &command.credential)
            .await
    }

    async fn dispatch_coordination_call(
        &self,
        caller_bot_id: &str,
        context: &BotRunContext,
        call: &CoordinationCall,
    ) -> Result<(), ProviderBotEventError> {
        match call.tool.as_str() {
            TOOL_ASSIGN_TASK => {
                let target_bot_id =
                    coordination_argument_str(call, "target_bot").ok_or_else(|| {
                        ProviderBotEventError::InvalidRequest(
                            "bcs_assign_task requires target_bot".to_string(),
                        )
                    })?;
                let message = coordination_argument_str(call, "message").ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "bcs_assign_task requires message".to_string(),
                    )
                })?;
                let mut payload = json!({
                    "message": message,
                });
                if let Some(response_mode) = coordination_argument_str(call, "response_mode") {
                    payload["response_mode"] = serde_json::Value::String(response_mode.to_string());
                }
                if let Some(session_id) = context.bcs_session_id.as_deref() {
                    payload["bcs_session_id"] = serde_json::Value::String(session_id.to_string());
                }
                self.message_flow
                    .handle_task_dispatch(TaskDispatchCommand {
                        driver_bot_id: caller_bot_id.to_string(),
                        group_id: context.group_id.clone(),
                        target_bot_id: target_bot_id.to_string(),
                        target_bot_name: None,
                        payload,
                    })
                    .await
                    .map_err(map_service_error)?;
            }
            TOOL_SEND_TASK_MESSAGE => {
                let session_id = context.bcs_session_id.as_deref().ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "bcs_send_task_message requires bcs_session_id".to_string(),
                    )
                })?;
                let message = coordination_argument_str(call, "message").ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "bcs_send_task_message requires message".to_string(),
                    )
                })?;
                self.message_flow
                    .handle_task_message(TaskMessageCommand {
                        worker_bot_id: caller_bot_id.to_string(),
                        group_id: context.group_id.clone(),
                        payload: json!({
                            "message": message,
                            "bcs_session_id": session_id,
                        }),
                    })
                    .await
                    .map_err(map_service_error)?;
            }
            TOOL_TASK_COMPLETE => {
                let summary = coordination_argument_str(call, "summary").ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "bcs_task_complete requires summary".to_string(),
                    )
                })?;
                let mut payload = json!({
                    "group_id": context.group_id.clone(),
                    "summary": summary,
                    "status": "completed",
                });
                if let Some(session_id) = context.bcs_session_id.as_deref() {
                    payload["bcs_session_id"] = serde_json::Value::String(session_id.to_string());
                }
                let outcome = self
                    .message_flow
                    .handle_task_complete(TaskCompleteCommand {
                        task_id: context.group_id.clone(),
                        bot_id: caller_bot_id.to_string(),
                        via_echo: true,
                        payload,
                    })
                    .await
                    .map_err(map_service_error)?;
                if outcome.blocked {
                    return Err(ProviderBotEventError::InvalidRequest(format!(
                        "task completion blocked; pending={:?}",
                        outcome.pending
                    )));
                }
            }
            _ => {
                return Err(ProviderBotEventError::InvalidRequest(format!(
                    "unsupported coordination tool '{}'",
                    call.tool
                )));
            }
        }
        Ok(())
    }
}

#[async_trait]
impl ProviderBotEventService for ProviderBotEvents {
    async fn submit_event(
        &self,
        command: ProviderBotEventCommand,
    ) -> Result<ProviderBotEventOutcome, ProviderBotEventError> {
        if command.run_id.trim().is_empty() {
            return Err(ProviderBotEventError::InvalidRequest(
                "run_id is required".to_string(),
            ));
        }

        // Two intake modes (spec §11.2 / §11.3):
        //  - Legacy terminal-only (1.0): no `event`/`payload`; only chat
        //    final/error/aborted accepted, payload synthesized from message_text.
        //  - Callback streaming (2.0): `event`+`payload` present; accept §11.1.1
        //    completion events (chat final, agent/stream:tool result, thinking).
        //    Only chat terminal states close the run; non-terminal events flow
        //    through the pipeline without acquiring the terminal lock.
        let is_callback_streaming = command.event.is_some() && command.payload.is_some();
        let is_terminal = matches!(
            command.state,
            ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted
        );

        if !is_callback_streaming {
            // Legacy contract: reject non-terminal states outright.
            if !is_terminal {
                return Err(ProviderBotEventError::InvalidRequest(
                    "only final, error, and aborted states are supported".to_string(),
                ));
            }
        }

        // event_type + event_payload for handle_bot_event:
        //  - callback streaming: use the provider's event class + full payload
        //    (already in §3 schema; same as the SSE path produces).
        //  - legacy: synthesize the chat.event terminal payload from message_text.
        let payload_state = match &command.state {
            ChatEventState::Final => "final",
            ChatEventState::Error => "error",
            ChatEventState::Aborted => "aborted",
            ChatEventState::Delta => "delta",
            ChatEventState::ToolCallStart => "tool_call_start",
            ChatEventState::ToolCallEnd => "tool_call_end",
        };
        let (ingest_event_type, mut ingest_payload) = if is_callback_streaming {
            let event_type = match command.event.as_deref() {
                Some("agent") => "agent".to_string(),
                _ => "chat.event".to_string(),
            };
            (
                event_type,
                command.payload.clone().unwrap_or_else(|| {
                    json!({
                        "state": payload_state,
                        "message": { "content": [ { "type": "text", "text": command.message_text } ] },
                        "run_id": command.run_id,
                    })
                }),
            )
        } else {
            (
                "chat.event".to_string(),
                json!({
                    "state": payload_state,
                    "message": { "content": [ { "type": "text", "text": command.message_text } ] },
                    "run_id": command.run_id,
                }),
            )
        };
        if ingest_event_type == "chat.event" {
            normalize_chat_error_payload(&mut ingest_payload);
        }

        if let Some(collaboration_runtime) = self.collaboration_runtime.as_ref() {
            if let Some(correlation) = collaboration_runtime
                .lookup_delivery_correlation(&command.run_id)
                .await
                .map_err(map_collaboration_runtime_error)?
            {
                let identity = self.authenticate_event(&command).await?;
                if identity.bot_uuid != correlation.assignee_bot_id {
                    warn!(
                        provider_id = %command.provider_id,
                        run_id = %command.run_id,
                        provider_bot_id = %identity.bot_uuid,
                        run_bot_id = %correlation.assignee_bot_id,
                        "provider callback: state-machine runtime identity mismatch"
                    );
                    return Err(ProviderBotEventError::Forbidden(
                        "runtime identity does not match state-machine delivery".to_string(),
                    ));
                }

                if matches!(command.state, ChatEventState::Final)
                    && command.message_text.trim().is_empty()
                {
                    return Err(ProviderBotEventError::InvalidRequest(
                        "final state-machine bot event must include text".to_string(),
                    ));
                }

                let terminal_command = HandleBotTerminalEventCommand {
                    bot_id: identity.bot_uuid.clone(),
                    run_id: command.run_id.clone(),
                    event_type: "chat.event".to_string(),
                    event_payload: json!({
                        "state": payload_state,
                        "message": {
                            "content": [
                                { "type": "text", "text": command.message_text.clone() }
                            ]
                        },
                        "run_id": command.run_id.clone(),
                    }),
                    state: command.state.clone(),
                    bcs_session_id: None,
                };
                if matches!(command.state, ChatEventState::Final) {
                    let inflight_key = (
                        correlation.state_machine_run_id.clone(),
                        correlation.node_id.clone(),
                        correlation.attempt,
                    );
                    let accepted = self
                        .state_machine_terminals_inflight
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .insert(inflight_key.clone());
                    if !accepted {
                        info!(
                            provider_id = %command.provider_id,
                            run_id = %command.run_id,
                            state_machine_run_id = %correlation.state_machine_run_id,
                            node_id = %correlation.node_id,
                            attempt = correlation.attempt,
                            "provider callback: duplicate async state-machine final accepted"
                        );
                        return Ok(ProviderBotEventOutcome {
                            delivered_count: 1,
                            failed_count: 0,
                        });
                    }

                    let collaboration_runtime = collaboration_runtime.clone();
                    let inflight_guard = StateMachineTerminalInflightGuard {
                        inflight: self.state_machine_terminals_inflight.clone(),
                        key: inflight_key,
                    };
                    let provider_id = command.provider_id.clone();
                    let provider_run_id = command.run_id.clone();
                    let bot_id = identity.bot_uuid.clone();
                    let state_machine_run_id = correlation.state_machine_run_id.clone();
                    let node_id = correlation.node_id.clone();
                    let attempt = correlation.attempt;
                    info!(
                        provider_id = %provider_id,
                        run_id = %provider_run_id,
                        bot_id = %bot_id,
                        state_machine_run_id = %state_machine_run_id,
                        node_id = %node_id,
                        attempt = attempt,
                        "provider callback: state-machine final accepted for async processing"
                    );
                    tokio::spawn(async move {
                        let processing = tokio::spawn(async move {
                            let _inflight_guard = inflight_guard;
                            collaboration_runtime
                                .handle_bot_terminal_event(terminal_command)
                                .await
                        });
                        match processing.await {
                            Ok(Ok(outcome)) => info!(
                                provider_id = %provider_id,
                                run_id = %provider_run_id,
                                bot_id = %bot_id,
                                state_machine_run_id = %state_machine_run_id,
                                node_id = %node_id,
                                attempt = attempt,
                                consumed = %outcome.consumed,
                                "provider callback: async state-machine final processing completed"
                            ),
                            Ok(Err(processing_error)) => error!(
                                provider_id = %provider_id,
                                run_id = %provider_run_id,
                                bot_id = %bot_id,
                                state_machine_run_id = %state_machine_run_id,
                                node_id = %node_id,
                                attempt = attempt,
                                error = %processing_error,
                                "provider callback: async state-machine final processing failed"
                            ),
                            Err(join_error) => error!(
                                provider_id = %provider_id,
                                run_id = %provider_run_id,
                                bot_id = %bot_id,
                                state_machine_run_id = %state_machine_run_id,
                                node_id = %node_id,
                                attempt = attempt,
                                error = %join_error,
                                "provider callback: async state-machine final task failed"
                            ),
                        }
                    });
                    return Ok(ProviderBotEventOutcome {
                        delivered_count: 1,
                        failed_count: 0,
                    });
                }

                let outcome = collaboration_runtime
                    .handle_bot_terminal_event(terminal_command)
                    .await
                    .map_err(map_collaboration_runtime_error)?;
                info!(
                    provider_id = %command.provider_id,
                    run_id = %command.run_id,
                    bot_id = %identity.bot_uuid,
                    state_machine_run_id = %correlation.state_machine_run_id,
                    node_id = %correlation.node_id,
                    attempt = %correlation.attempt,
                    consumed = %outcome.consumed,
                    message_text = %command.message_text,
                    "provider callback: dispatched state-machine bot event"
                );
                return Ok(ProviderBotEventOutcome {
                    delivered_count: if outcome.consumed { 1 } else { 0 },
                    failed_count: if outcome.consumed { 0 } else { 1 },
                });
            }
        }

        let context = self
            .bot_run_context
            .get_context(&command.run_id)
            .await
            .ok_or_else(|| ProviderBotEventError::RunNotFound("run_not_found".to_string()))?;
        let context_group_id = context.group_id.clone();
        let context_bot_id = context.bot_id.clone();
        let context_bcs_session_id = context.bcs_session_id.clone();
        info!(
            provider_id = %command.provider_id,
            run_id = %command.run_id,
            group_id = %context_group_id,
            bcs_session_id = ?context_bcs_session_id,
            target_bot_id = %context_bot_id,
            state = ?command.state,
            message_text = %command.message_text,
            "provider callback: resolved run context"
        );
        let now = now_ms();
        if context.terminal || now > context.deadline_ms {
            return Err(ProviderBotEventError::RunTerminated(
                "run_terminated".to_string(),
            ));
        }

        let identity = self.authenticate_event(&command).await?;

        if identity.bot_uuid != context_bot_id {
            warn!(
                provider_id = %command.provider_id,
                run_id = %command.run_id,
                provider_bot_id = %identity.bot_uuid,
                run_bot_id = %context_bot_id,
                "provider callback: runtime identity mismatch"
            );
            return Err(ProviderBotEventError::Forbidden(
                "runtime identity does not match run".to_string(),
            ));
        }

        // Only terminal chat states acquire the terminal lock and close the
        // run. Non-terminal callback-streaming events (tool result, thinking,
        // chat delta) flow through the pipeline without terminating the run —
        // the run stays open until a chat final/error/aborted arrives (§11.1.1).
        if is_terminal
            && !self
                .bot_run_context
                .try_begin_terminal(&command.run_id)
                .await
        {
            return Err(ProviderBotEventError::RunTerminated(
                "run_terminated".to_string(),
            ));
        }

        let run_id = command.run_id.clone();
        let identity_bot_uuid = identity.bot_uuid.clone();
        let outcome_result = self
            .message_flow
            .handle_bot_event(BotEventCommand {
                bot_id: identity_bot_uuid.clone(),
                run_id: run_id.clone(),
                group_id: context_group_id.clone(),
                event_type: ingest_event_type.clone(),
                // Callback streaming: the provider's §3 payload (same shape the
                // SSE path produces). Legacy: synthesized chat.event terminal
                // payload, so A2A run parsers see it like WS `chat.event` frames.
                event_payload: ingest_payload.clone(),
                state: command.state.clone(),
                bcs_session_id: context_bcs_session_id.clone(),
            })
            .await;
        let outcome = match outcome_result {
            Ok(outcome) => outcome,
            Err(error) => {
                if is_terminal {
                    self.bot_run_context.release_terminal(&command.run_id).await;
                }
                return Err(map_service_error(error));
            }
        };

        if is_terminal {
            self.bot_run_context.mark_terminal(&command.run_id).await;
        }
        info!(
            provider_id = %command.provider_id,
            run_id = %command.run_id,
            bot_id = %identity_bot_uuid,
            group_id = %context_group_id,
            bcs_session_id = ?context_bcs_session_id,
            event_type = %ingest_event_type,
            terminal = %is_terminal,
            delivered_count = %outcome.delivered_count,
            failed_count = %outcome.failed_count,
            message_text = %command.message_text,
            "provider callback: dispatched bot event"
        );

        Ok(ProviderBotEventOutcome {
            delivered_count: outcome.delivered_count,
            failed_count: outcome.failed_count,
        })
    }

    async fn submit_coordination(
        &self,
        command: ProviderBotCoordinationCommand,
    ) -> Result<ProviderBotCoordinationOutcome, ProviderBotEventError> {
        if command.run_id.trim().is_empty() {
            return Err(ProviderBotEventError::InvalidRequest(
                "run_id is required".to_string(),
            ));
        }
        if command.tool_call_id.trim().is_empty() {
            return Err(ProviderBotEventError::InvalidRequest(
                "tool_call_id is required".to_string(),
            ));
        }

        let context = self
            .bot_run_context
            .get_context(&command.run_id)
            .await
            .ok_or_else(|| ProviderBotEventError::RunNotFound("run_not_found".to_string()))?;
        let now = now_ms();
        if context.terminal || now > context.deadline_ms {
            return Err(ProviderBotEventError::RunTerminated(
                "run_terminated".to_string(),
            ));
        }

        let identity = self.authenticate_coordination(&command).await?;
        if identity.bot_uuid != context.bot_id {
            warn!(
                provider_id = %command.provider_id,
                run_id = %command.run_id,
                provider_bot_id = %identity.bot_uuid,
                run_bot_id = %context.bot_id,
                "provider callback: coordination runtime identity mismatch"
            );
            return Err(ProviderBotEventError::Forbidden(
                "runtime identity does not match run".to_string(),
            ));
        }

        let coordination = self
            .provider_bot_core
            .get_provider_coordination_config(&command.provider_id)
            .await
            .map_err(map_service_error)?;
        let call = coordination_call_from_command(&coordination, &command)?;

        let dedup_key = format!("{}:{}", command.run_id.trim(), command.tool_call_id.trim());
        {
            let mut seen = self.coordination_seen.lock().await;
            seen.retain(|_, seen_at| now.saturating_sub(*seen_at) <= COORDINATION_PROCESSED_TTL_MS);
            if seen.contains_key(&dedup_key) {
                info!(
                    provider_id = %command.provider_id,
                    run_id = %command.run_id,
                    tool_call_id = %command.tool_call_id,
                    dedup_key = %dedup_key,
                    "provider callback: duplicate coordination event skipped"
                );
                return Ok(ProviderBotCoordinationOutcome {
                    processed: true,
                    duplicate: true,
                });
            }
            seen.insert(dedup_key.clone(), now);
        }

        self.dispatch_coordination_call(&identity.bot_uuid, &context, &call)
            .await?;

        info!(
            provider_id = %command.provider_id,
            run_id = %command.run_id,
            tool_call_id = %command.tool_call_id,
            bot_id = %identity.bot_uuid,
            group_id = %context.group_id,
            bcs_session_id = ?context.bcs_session_id,
            tool = %call.tool,
            "provider callback: coordination event processed"
        );

        Ok(ProviderBotCoordinationOutcome {
            processed: true,
            duplicate: false,
        })
    }
}

fn normalize_chat_error_payload(payload: &mut Value) {
    let Some(obj) = payload.as_object_mut() else {
        return;
    };
    if obj.get("state").and_then(Value::as_str) != Some("error") {
        return;
    }
    if payload_has_message_text(obj.get("message")) {
        return;
    }
    let error_message = obj
        .get("errorMessage")
        .or_else(|| obj.get("error_message"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(str::to_string);
    if let Some(error_message) = error_message {
        obj.insert(
            "message".to_string(),
            json!({
                "role": "assistant",
                "content": [{ "type": "text", "text": error_message }],
                "timestamp": now_ms(),
            }),
        );
    }
}

fn payload_has_message_text(message: Option<&Value>) -> bool {
    let Some(content) = message.and_then(|message| message.get("content")) else {
        return false;
    };
    if let Some(arr) = content.as_array() {
        return arr.iter().any(|block| {
            block
                .get("text")
                .and_then(Value::as_str)
                .is_some_and(|text| !text.is_empty())
        });
    }
    content.as_str().is_some_and(|text| !text.is_empty())
}

fn map_auth_error(error: ServiceError) -> ProviderBotEventError {
    match error {
        ServiceError::Unauthorized(message) => ProviderBotEventError::Unauthorized(message),
        ServiceError::Forbidden(message) => ProviderBotEventError::Forbidden(message),
        ServiceError::InvalidOperation { message, .. } => {
            ProviderBotEventError::InvalidRequest(message)
        }
        other => map_service_error(other),
    }
}

fn coordination_call_from_command(
    coordination: &ProviderCoordinationConfig,
    command: &ProviderBotCoordinationCommand,
) -> Result<CoordinationCall, ProviderBotEventError> {
    match coordination.mode {
        CoordinationMode::McporterMcp => {
            if command.kind != ProviderCoordinationEventKind::ToolResult {
                return Err(ProviderBotEventError::InvalidRequest(
                    "mcporter_mcp coordination requires tool_result callbacks".to_string(),
                ));
            }
            let result_text = command
                .result_text
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "tool_result callback requires result_text".to_string(),
                    )
                })?;
            CoordinationCall::from_stdout(result_text).ok_or_else(|| {
                ProviderBotEventError::InvalidRequest(
                    "tool_result did not contain a BCS coordination echo".to_string(),
                )
            })
        }
        CoordinationMode::NativeMcp => {
            if command.kind != ProviderCoordinationEventKind::CoordinationIntent {
                return Err(ProviderBotEventError::InvalidRequest(
                    "native_mcp coordination requires coordination_intent callbacks".to_string(),
                ));
            }
            let expected_server = coordination
                .mcp_server
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "native_mcp coordination is missing mcp_server config".to_string(),
                    )
                })?;
            let actual_server = command
                .mcp_server
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| {
                    ProviderBotEventError::InvalidRequest(
                        "native_mcp coordination requires mcp_server".to_string(),
                    )
                })?;
            if actual_server != expected_server {
                return Err(ProviderBotEventError::Forbidden(format!(
                    "mcp_server mismatch: expected '{}'",
                    expected_server
                )));
            }
            coordination_intent_to_call(command.intent.as_ref())
        }
        CoordinationMode::NativeTool => {
            if command.kind != ProviderCoordinationEventKind::CoordinationIntent {
                return Err(ProviderBotEventError::InvalidRequest(
                    "native_tool coordination requires coordination_intent callbacks".to_string(),
                ));
            }
            if command
                .mcp_server
                .as_deref()
                .map(str::trim)
                .is_some_and(|value| !value.is_empty())
            {
                return Err(ProviderBotEventError::InvalidRequest(
                    "native_tool coordination must not set mcp_server".to_string(),
                ));
            }
            coordination_intent_to_call(command.intent.as_ref())
        }
        CoordinationMode::Disabled => Err(ProviderBotEventError::InvalidRequest(
            "provider coordination is disabled".to_string(),
        )),
        CoordinationMode::LegacyUpstream => Err(ProviderBotEventError::InvalidRequest(
            "legacy_upstream is not a provider coordination mode".to_string(),
        )),
    }
}

fn coordination_intent_to_call(
    intent: Option<&ProviderCoordinationIntent>,
) -> Result<CoordinationCall, ProviderBotEventError> {
    let intent = intent.ok_or_else(|| {
        ProviderBotEventError::InvalidRequest(
            "coordination_intent callback requires intent".to_string(),
        )
    })?;
    if intent.v != CONTRACT_VERSION {
        return Err(ProviderBotEventError::InvalidRequest(format!(
            "unsupported coordination intent version {}",
            intent.v
        )));
    }
    if intent.tool.trim().is_empty() {
        return Err(ProviderBotEventError::InvalidRequest(
            "coordination intent tool is required".to_string(),
        ));
    }
    Ok(CoordinationCall {
        tool: intent.tool.trim().to_string(),
        arguments: intent.arguments.clone(),
    })
}

fn coordination_argument_str<'a>(call: &'a CoordinationCall, key: &str) -> Option<&'a str> {
    call.arguments
        .get(key)
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn map_service_error(error: ServiceError) -> ProviderBotEventError {
    match error {
        ServiceError::Unauthorized(message) => ProviderBotEventError::Unauthorized(message),
        ServiceError::Forbidden(message) => ProviderBotEventError::Forbidden(message),
        ServiceError::InvalidOperation { message, .. } => {
            ProviderBotEventError::InvalidRequest(message)
        }
        ServiceError::BotNotFound(bot_id) | ServiceError::BotNotRegistered(bot_id) => {
            ProviderBotEventError::BotNotFound(bot_id)
        }
        other => ProviderBotEventError::Internal(other.to_string()),
    }
}

fn map_collaboration_runtime_error(error: CollaborationRuntimeError) -> ProviderBotEventError {
    match error {
        CollaborationRuntimeError::RunNotFound(run_id) => {
            ProviderBotEventError::RunNotFound(run_id)
        }
        CollaborationRuntimeError::NodeNotFound { run_id, node_id } => {
            ProviderBotEventError::RunNotFound(format!("{run_id}/{node_id}"))
        }
        CollaborationRuntimeError::Unauthenticated => {
            ProviderBotEventError::Unauthorized("authentication is required".to_string())
        }
        CollaborationRuntimeError::Forbidden(message) => ProviderBotEventError::Forbidden(message),
        CollaborationRuntimeError::JudgeUnavailable(message) => {
            ProviderBotEventError::Internal(message)
        }
        CollaborationRuntimeError::InvalidRequest(message)
        | CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message) => {
            ProviderBotEventError::InvalidRequest(message)
        }
        CollaborationRuntimeError::Conflict(message) => {
            ProviderBotEventError::RunTerminated(message)
        }
        CollaborationRuntimeError::DefinitionNotFound(id, version) => {
            ProviderBotEventError::InvalidRequest(format!(
                "collaboration definition not found: {id}@{version}"
            ))
        }
        CollaborationRuntimeError::Internal(error) => map_service_error(error),
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
