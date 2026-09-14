use bcs_service_api::port::CoordinationIntentPort;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::{
    BotEventCommand, BotRunContext, BotRunContextPort, ChatEventState, CollaborationRuntimeError,
    CollaborationRuntimeService, HandleBotTerminalEventCommand,
    MessageFlowService,
    ProviderBotCoreService, ProviderBotEventCommand, ProviderBotEventCredential,
    ProviderBotEventError, ProviderBotEventOutcome, ProviderBotEventService,
    ProviderEventIngestCommand, ProviderEventIngestService, ProviderEventSource,
    ProviderRunTransport, RuntimeBotIdentity, ServiceError, ServiceResult, TaskCompleteCommand,
    TaskDispatchCommand, TaskMessageCommand,
    DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
};
use bcs_protocol::stream::{
    ProviderTextEventState, ProviderTextResponseMode, apply_provider_event_text,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;
use tracing::{error, info, warn};
use tracing::instrument::WithSubscriber;

const STATE_MACHINE_VISIBLE_TEXT_RETENTION_MS: u64 = 24 * 60 * 60 * 1000;

type StateMachineTerminalKey = (String, String, i32);

struct StateMachineVisibleText {
    text: String,
    expires_at_ms: u64,
}

fn cleanup_expired_visible_text_entries(
    runs: &mut HashMap<String, StateMachineVisibleText>,
    now_ms: u64,
) -> usize {
    let before = runs.len();
    runs.retain(|_, entry| now_ms <= entry.expires_at_ms);
    before.saturating_sub(runs.len())
}

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

#[derive(Clone)]
pub struct ProviderBotEvents {
    provider_bot_core: Arc<dyn ProviderBotCoreService>,
    bot_run_context: Arc<dyn BotRunContextPort>,
    message_flow: Arc<dyn MessageFlowService>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
    /// Coordination intent port previously consumed by the removed
    /// HTTP coordination callback. The field and setter
    /// are retained so existing composition roots (`bcs-bootstrap`) keep
    /// compiling; the value is no longer read by this service after the HTTP
    /// callback path was deleted. Tracked for cleanup with bcs-bootstrap
    /// follow-up that drops the wiring.
    #[allow(dead_code)]
    pub(crate) coordination_intents: Option<Arc<dyn CoordinationIntentPort>>,
    state_machine_terminals_inflight: Arc<StdMutex<HashSet<StateMachineTerminalKey>>>,
    state_machine_visible_text: Arc<Mutex<HashMap<String, StateMachineVisibleText>>>,
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
            coordination_intents: None,
            state_machine_terminals_inflight: Arc::new(StdMutex::new(HashSet::new())),
            state_machine_visible_text: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn with_coordination_intents(mut self, port: Option<Arc<dyn CoordinationIntentPort>>) -> Self {
        self.coordination_intents = port;
        self
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

    async fn ingest_event(
        &self,
        command: ProviderEventIngestCommand,
    ) -> ServiceResult<bcs_service_api::BotEventOutcome> {
        let Some(runtime) = self.collaboration_runtime.as_ref() else {
            return self.message_flow.ingest_provider_event(command).await;
        };
        let Some(correlation) = runtime
            .lookup_delivery_correlation(&command.event.run_id)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?
        else {
            return self.message_flow.ingest_provider_event(command).await;
        };
        if correlation.assignee_bot_id != command.event.bot_id {
            return Err(ServiceError::Forbidden(
                "provider event bot does not match state-machine delivery".to_string(),
            ));
        }

        let mut event = command.event;
        let terminal = matches!(event.state,
            ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted);
        let expires_at_ms = self
            .bot_run_context
            .get_context(&event.run_id)
            .await
            .map(|context| context.deadline_ms)
            .unwrap_or_else(|| now_ms().saturating_add(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS))
            .saturating_add(STATE_MACHINE_VISIBLE_TEXT_RETENTION_MS);
        let visible_text = {
            let mut runs = self.state_machine_visible_text.lock().await;
            let accumulated = runs
                .entry(event.run_id.clone())
                .or_insert_with(|| StateMachineVisibleText {
                    text: String::new(),
                    expires_at_ms,
                });
            accumulated.expires_at_ms = expires_at_ms;
            apply_provider_event_text(
                &mut accumulated.text,
                &event.event_type,
                &event.event_payload,
                provider_text_event_state(&event.state),
                ProviderTextResponseMode::AfterLastToolCall,
            );
            let visible_text = accumulated.text.clone();
            if terminal { runs.remove(&event.run_id); }
            visible_text
        };
        if matches!(event.event_type.as_str(), "chat" | "chat.event")
            && matches!(event.state, ChatEventState::Delta | ChatEventState::Final)
            && !visible_text.is_empty()
        {
            inject_visible_text(&mut event.event_payload, &visible_text);
        }

        let runtime_command = HandleBotTerminalEventCommand {
            bot_id: event.bot_id.clone(),
            run_id: event.run_id.clone(),
            event_type: event.event_type.clone(),
            event_payload: event.event_payload.clone(),
            state: event.state.clone(),
            bcs_session_id: event.bcs_session_id.clone(),
        };
        if event.state == ChatEventState::Final {
            let inflight_key = (correlation.state_machine_run_id.clone(),
                correlation.node_id.clone(), correlation.attempt);
            if self.state_machine_terminals_inflight.lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .insert(inflight_key.clone())
            {
                let runtime = runtime.clone();
                let inflight_guard = StateMachineTerminalInflightGuard {
                    inflight: self.state_machine_terminals_inflight.clone(),
                    key: inflight_key,
                };
                tokio::spawn(bcs_observability::with_request_id(bcs_observability::current_request_id(), async move {
                    let _inflight_guard = inflight_guard;
                    if let Err(error) = runtime.handle_bot_terminal_event(runtime_command).await {
                        error!(request_id = %bcs_observability::CurrentRequestId, %error, "provider ingest: async state-machine final failed");
                    }
                }).with_current_subscriber());
            }
            return Ok(provider_state_machine_outcome());
        }

        let outcome = runtime.handle_bot_terminal_event(runtime_command).await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(if outcome.consumed { provider_state_machine_outcome() }
            else { provider_state_machine_miss_outcome() })
    }
}

fn inject_visible_text(payload: &mut Value, text: &str) {
    if let Some(object) = payload.as_object_mut() {
        object.insert("message".to_string(),
            json!({"content": [{"type": "text", "text": text}]}));
    }
}

fn provider_text_event_state(state: &ChatEventState) -> ProviderTextEventState {
    match state {
        ChatEventState::Delta => ProviderTextEventState::Delta,
        ChatEventState::Final => ProviderTextEventState::Final,
        ChatEventState::Aborted => ProviderTextEventState::Aborted,
        ChatEventState::Error => ProviderTextEventState::Error,
        ChatEventState::ToolCallStart => ProviderTextEventState::ToolCallStart,
        ChatEventState::ToolCallEnd => ProviderTextEventState::ToolCallEnd,
    }
}

fn provider_state_machine_outcome() -> bcs_service_api::BotEventOutcome {
    bcs_service_api::BotEventOutcome {
        bot_deliveries: Vec::new(), frontend_deliveries: Vec::new(),
        unregistered_run_ids: Vec::new(), mentions: Vec::new(),
        delivered_count: 1, failed_count: 0, delivery_results: Vec::new(),
    }
}

fn provider_state_machine_miss_outcome() -> bcs_service_api::BotEventOutcome {
    bcs_service_api::BotEventOutcome {
        failed_count: 1, delivered_count: 0, ..provider_state_machine_outcome()
    }
}

#[async_trait]
impl ProviderEventIngestService for ProviderBotEvents {
    async fn ingest_provider_event(
        &self,
        command: ProviderEventIngestCommand,
    ) -> ServiceResult<bcs_service_api::BotEventOutcome> {
        self.ingest_event(command).await
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

        // COSEC: bind every Provider 2.0 run to one negotiated event source so
        // a callback cannot inject duplicate or conflicting events into an SSE run.
        match self
            .bot_run_context
            .get_provider_transport(&command.run_id)
            .await
        {
            Some(ProviderRunTransport::Sse | ProviderRunTransport::Negotiating) => {
                return Err(ProviderBotEventError::TransportConflict(
                    "transport_conflict".to_string(),
                ));
            }
            Some(ProviderRunTransport::Terminal) => {
                return Err(ProviderBotEventError::RunTerminated(
                    "run_terminated".to_string(),
                ));
            }
            Some(ProviderRunTransport::Callback) | None => {}
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

        if let Some(runtime) = self.collaboration_runtime.as_ref()
            && runtime.lookup_delivery_correlation(&command.run_id).await
                .map_err(map_collaboration_runtime_error)?.is_some()
        {
            let identity = self.authenticate_event(&command).await?;
            let outcome = self.ingest_event(ProviderEventIngestCommand {
                source: ProviderEventSource::Callback,
                event: BotEventCommand {
                    bot_id: identity.bot_uuid,
                    run_id: command.run_id.clone(),
                    group_id: String::new(),
                    event_type: ingest_event_type,
                    event_payload: ingest_payload,
                    state: command.state.clone(),
                    bcs_session_id: None,
                },
            }).await.map_err(map_service_error)?;
            if is_terminal {
                self.bot_run_context.mark_terminal(&command.run_id).await;
                self.bot_run_context.mark_provider_transport_terminal(&command.run_id).await;
            }
            return Ok(ProviderBotEventOutcome {
                delivered_count: outcome.delivered_count,
                failed_count: outcome.failed_count,
            });
        }

        let has_registered_context = self
            .bot_run_context
            .get_context(&command.run_id)
            .await
            .is_some();
        if !has_registered_context {
            if let Some(collaboration_runtime) = self.collaboration_runtime.as_ref() {
            if let Some(correlation) = collaboration_runtime
                .lookup_delivery_correlation(&command.run_id)
                .await
                .map_err(map_collaboration_runtime_error)?
            {
                let identity = self.authenticate_event(&command).await?;
                if identity.bot_uuid != correlation.assignee_bot_id {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
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
                    tokio::spawn(bcs_observability::with_request_id(bcs_observability::current_request_id(), async move {
                        let processing = tokio::spawn(bcs_observability::in_current_context(async move {
                            let _inflight_guard = inflight_guard;
                            collaboration_runtime
                                .handle_bot_terminal_event(terminal_command)
                                .await
                        }));
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
                                request_id = %bcs_observability::CurrentRequestId,
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
                                request_id = %bcs_observability::CurrentRequestId,
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
                    }).with_current_subscriber());
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
        }

        let identity = self.authenticate_event(&command).await?;
        let managed_context = self.message_flow.resolve_managed_provider_run(&command.run_id, &command.provider_id, &identity.bot_uuid)
            .await.map_err(map_service_error)?;
        let managed = managed_context.is_some();
        let cached_context = self
            .bot_run_context
            .get_context(&command.run_id)
            .await;
        // A durable Unknown run can accept a trusted late terminal even after
        // cache expiry/restart. Do not resume an unnegotiated streaming source.
        if managed && cached_context.is_none() && !is_terminal {
            return Err(ProviderBotEventError::RunNotFound("run_not_found".into()));
        }
        let context = managed_context.or(cached_context)
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

        if identity.bot_uuid != context_bot_id {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
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
        if is_terminal && !managed
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
            .ingest_event(ProviderEventIngestCommand {
                source: ProviderEventSource::Callback,
                event: BotEventCommand {
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
                },
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
            self.bot_run_context
                .mark_provider_transport_terminal(&command.run_id)
                .await;
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

    async fn cleanup_expired(&self, now_ms: u64) -> usize {
        let mut runs = self.state_machine_visible_text.lock().await;
        cleanup_expired_visible_text_entries(&mut runs, now_ms)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_machine_visible_text_expires_one_day_after_run_deadline() {
        let deadline_ms = 1_000_u64;
        let expires_at_ms = deadline_ms.saturating_add(STATE_MACHINE_VISIBLE_TEXT_RETENTION_MS);
        let mut runs = HashMap::from([(
            "run-1".to_string(),
            StateMachineVisibleText {
                text: "partial".to_string(),
                expires_at_ms,
            },
        )]);

        assert_eq!(STATE_MACHINE_VISIBLE_TEXT_RETENTION_MS, 86_400_000);
        assert_eq!(cleanup_expired_visible_text_entries(&mut runs, expires_at_ms), 0);
        assert_eq!(runs.len(), 1);
        assert_eq!(
            cleanup_expired_visible_text_entries(&mut runs, expires_at_ms + 1),
            1
        );
        assert!(runs.is_empty());
        assert_eq!(cleanup_expired_visible_text_entries(&mut runs, u64::MAX), 0);
    }
}
