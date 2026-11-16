use std::collections::HashMap;
use std::future::Future;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use async_trait::async_trait;
use bcs_protocol::{
    BcsFrame, BotStatus, ChatEventState as WireChatEventState, EventFrame, RequestFrame,
    ResponseFrame,
};
use bcs_service_api::{
    BotDeliveryTarget, BotDynamicStatus, BotEventCommand, BotEventOutcome, BotRunContext,
    BotRunContextPort, BotRuntimeConnectCommand, BotRuntimeConnectOutcome,
    BotRuntimeConnectionService, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand,
    BotRuntimeStatusOutcome, BotUseCaseError, ChatAbortCommand,
    ChatAbortOutcome, ChatEventState, GroupCallbackCommand, GroupCallbackOutcome,
    CancelStateMachineRunCommand, CollaborationDefinition, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    DmActorSpec, Group, GroupCoreService, GroupMessage, GroupStatus, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, MessageFlowService, Participant, ParticipantMode,
    ParticipantRole, ProviderRunTransport, RedactedToken, ServiceError, ServiceResult, SessionHistoryResult, StartStateMachineRunCommand, StartStateMachineRunOutcome,
    StateMachineDeliveryCorrelation, StateMachineRunView,
    SystemMessageEvent, SystemMessageService, TaskCompleteCommand, TaskCompleteOutcome,
    TaskDispatchCommand, TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
};
use bcs_session::NoopSessionManagementService;
use bcs_ws::bot::{
    BotConnectionRegistry, BotDispatchOutcome, BotDispatchState, BotWsDispatchError, dispatch_frame,
};
use bcs_ws::shared::RunChannelManager;
use opentelemetry::trace::{SpanContext, SpanId, TraceFlags, TraceId, TraceState};
use tokio::sync::{Mutex, mpsc};
use ::tracing::instrument::WithSubscriber;
use tracing_subscriber::prelude::*;

#[path = "support/task_rpc_scope.rs"]
mod task_rpc_scope;

#[path = "frame_compat/connection.rs"]
mod connection;
#[path = "frame_compat/diagnostics.rs"]
mod diagnostics;
#[path = "frame_compat/v3_events.rs"]
mod v3_events;
#[path = "frame_compat/interactions.rs"]
mod interactions;
#[path = "frame_compat/task_dispatch.rs"]
mod task_dispatch;
#[path = "frame_compat/coordination_echo.rs"]
mod coordination_echo;
#[path = "frame_compat/chat_events.rs"]
mod chat_events;
// A theme module named `tracing` shadows the extern crate in this test root and in every
// `use super::*;` theme - reference the crate as `::tracing` (see diagnostics.rs).
#[path = "frame_compat/tracing.rs"]
mod tracing;
#[path = "frame_compat/run_aliases.rs"]
mod run_aliases;

#[derive(Default)]
struct RecordingMessageFlow {
    bot_events: Mutex<Vec<BotEventCommand>>,
    fail_bot_event_once: std::sync::atomic::AtomicBool,
    task_dispatches: Mutex<Vec<TaskDispatchCommand>>,
    task_messages: Mutex<Vec<TaskMessageCommand>>,
    task_completes: Mutex<Vec<TaskCompleteCommand>>,
    task_aliases: Mutex<Vec<(String, String, String)>>,
    task_dispatch_error: Mutex<Option<ServiceError>>,
}

#[derive(Default)]
struct RecordingBotRuntime {
    statuses: Mutex<HashMap<String, BotDynamicStatus>>,
    connect_count: AtomicUsize,
    delivery_target: Mutex<Option<BotDeliveryTarget>>,
    reject_status_update: std::sync::atomic::AtomicBool,
    fail_disconnect: std::sync::atomic::AtomicBool,
    trust_native_mcp: std::sync::atomic::AtomicBool,
}

#[derive(Default)]
struct RecordingCollaborationRuntime {
    correlation: Mutex<Option<StateMachineDeliveryCorrelation>>,
    aliases: Mutex<Vec<(String, String)>>,
}

#[derive(Default)]
struct RecordingBotRunContext {
    contexts: Mutex<HashMap<String, BotRunContext>>,
    active: Mutex<Option<bcs_service_api::ActiveBotRunContext>>,
    fail_lookup: std::sync::atomic::AtomicBool,
}

#[async_trait]
impl BotRunContextPort for RecordingBotRunContext {
    async fn find_active_run(&self, _: &str) -> ServiceResult<Option<bcs_service_api::ActiveBotRunContext>> {
        if self.fail_lookup.load(Ordering::SeqCst) { return Err(ServiceError::InternalError("lookup failed".into())); }
        Ok(self.active.lock().await.clone())
    }
    async fn put_context(&self, context: BotRunContext) {
        self.contexts
            .lock()
            .await
            .insert(context.run_id.clone(), context);
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.contexts.lock().await.get(run_id).cloned()
    }

    async fn try_begin_terminal(&self, _run_id: &str) -> bool {
        false
    }

    async fn mark_terminal(&self, _run_id: &str) -> bool {
        false
    }

    async fn release_terminal(&self, _run_id: &str) {}

    async fn begin_provider_transport(&self, _run_id: &str, _deadline_ms: u64) -> bool {
        false
    }

    async fn bind_provider_transport(
        &self,
        _run_id: &str,
        _transport: ProviderRunTransport,
    ) -> bool {
        false
    }

    async fn get_provider_transport(&self, _run_id: &str) -> Option<ProviderRunTransport> {
        None
    }

    async fn mark_provider_transport_terminal(&self, _run_id: &str) {}

    async fn clear_provider_transport(&self, _run_id: &str) {}
}

#[derive(Default)]
struct RecordingGroupCoreService {
    groups: Mutex<HashMap<String, Group>>,
}

#[derive(Default)]
struct RecordingSystemMessageService {
    notifications: Mutex<Vec<RecordingSystemNotification>>,
}

struct RecordingSystemNotification {
    group_id: String,
    event: SystemMessageEvent,
    session_id: String,
    participants: Vec<Participant>,
}

impl RecordingGroupCoreService {
    async fn insert(&self, group: Group) {
        self.groups.lock().await.insert(group.id.clone(), group);
    }
}

#[async_trait]
impl GroupCoreService for RecordingGroupCoreService {
    async fn upsert(&self, group: Group) -> ServiceResult<()> {
        self.groups.lock().await.insert(group.id.clone(), group);
        Ok(())
    }

    async fn get(&self, id: &str) -> Option<Group> {
        self.groups.lock().await.get(id).cloned()
    }

    async fn add_message(&self, id: &str, _message: GroupMessage) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn add_participant(&self, id: &str, _participant: Participant) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn remove_participant(&self, group_id: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(group_id.to_string()))
    }

    async fn update_participant_mode(
        &self,
        group_id: &str,
        _actor_id: &str,
        _mode: ParticipantMode,
    ) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(group_id.to_string()))
    }

    async fn update_label(&self, id: &str, _label: Option<String>) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_status(&self, id: &str, _status: GroupStatus) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn terminate(&self, id: &str, _caller_bot_id: &str) -> ServiceResult<Group> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>> {
        Ok(self.groups.lock().await.remove(id))
    }

    async fn list(&self) -> Vec<Group> {
        self.groups.lock().await.values().cloned().collect()
    }

    async fn list_paginated(&self, _offset: u64, _limit: u64) -> Vec<Group> {
        self.list().await
    }

    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group> {
        self.groups
            .lock()
            .await
            .values()
            .filter(|group| group.get_participant(bot_uuid).is_some())
            .cloned()
            .collect()
    }

    async fn count(&self) -> u64 {
        self.groups.lock().await.len() as u64
    }

    async fn count_by_participant(&self, bot_uuid: &str) -> u64 {
        self.find_by_participant(bot_uuid).await.len() as u64
    }

    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        _offset: u64,
        _limit: u64,
    ) -> Vec<Group> {
        self.find_by_participant(bot_uuid).await
    }

    async fn message_count(&self, _id: &str) -> ServiceResult<usize> {
        Ok(0)
    }

    async fn increment_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn reset_message_count(&self, _id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn create_or_reuse_actor_dm_group(
        &self,
        id: &str,
        _actor_a: DmActorSpec,
        _actor_b: DmActorSpec,
        _legacy_driver_bot: &str,
        _originator_actor_id: &str,
        _label: Option<String>,
        _context: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }
}

struct RecordingGroupDispatchContext {
    group: Arc<RecordingGroupCoreService>,
}

#[async_trait]
impl bcs_service_api::GroupDispatchContextPort for RecordingGroupDispatchContext {
    async fn participants(&self, group_id: &str) -> Option<Vec<Participant>> {
        self.group.get(group_id).await.map(|group| group.participants)
    }
}

#[derive(Default)]
struct RecordingInteractionService {
    requested: Mutex<Vec<bcs_service_api::ProviderInteractionRequestedCommand>>,
    resolved: Mutex<Vec<bcs_service_api::ProviderInteractionResolvedCommand>>,
    invalidated: Mutex<Vec<(String, String)>>,
    fail_requested_once: std::sync::atomic::AtomicBool,
}

#[async_trait]
impl bcs_service_api::InteractionService for RecordingInteractionService {
    async fn on_provider_requested(
        &self,
        command: bcs_service_api::ProviderInteractionRequestedCommand,
    ) -> ServiceResult<bcs_service_api::InteractionRequestedOutcome> {
        if self.fail_requested_once.swap(false, Ordering::Relaxed) {
            return Err(ServiceError::InvalidOperation {
                message: "scripted interaction failure".to_string(),
                request_id: Some(command.bcs_run_id),
            });
        }
        self.requested.lock().await.push(command);
        Ok(bcs_service_api::InteractionRequestedOutcome::Stored)
    }

    async fn on_provider_resolved(
        &self,
        command: bcs_service_api::ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()> {
        self.resolved.lock().await.push(command);
        Ok(())
    }

    async fn resolve(
        &self,
        _command: bcs_service_api::ResolveInteractionCommand,
    ) -> Result<bcs_service_api::ResolveInteractionResult, bcs_service_api::InteractionServiceError> {
        Err(bcs_service_api::InteractionServiceError::NotFound)
    }

    async fn list_pending(
        &self,
        _bcs_session_id: &str,
    ) -> ServiceResult<Vec<bcs_service_api::InteractionFrontendEvent>> {
        Ok(Vec::new())
    }

    async fn invalidate_run(
        &self,
        bcs_run_id: &str,
        reason: &str,
        _invalidated_at_ms: u64,
    ) -> ServiceResult<usize> {
        self.invalidated
            .lock()
            .await
            .push((bcs_run_id.to_string(), reason.to_string()));
        Ok(0)
    }

    async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
        Ok(0)
    }
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessageService {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.notifications.lock().await.push(RecordingSystemNotification {
            group_id: group_id.to_string(),
            event,
            session_id: session_id.to_string(),
            participants: session_participants.to_vec(),
        });
        Ok(1)
    }
}

#[async_trait]
impl CollaborationRuntimeService for RecordingCollaborationRuntime {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        unimplemented!("not used by ws compat tests")
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        unimplemented!("not used by ws compat tests")
    }

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(self
            .correlation
            .lock()
            .await
            .clone()
            .filter(|correlation| correlation.delivery_request_id == run_id))
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        self.aliases
            .lock()
            .await
            .push((delivery_request_id.to_string(), bot_delivery_run_id));
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: true,
            view: None,
        })
    }

    async fn upsert_definition(
        &self,
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        unimplemented!("not used by ws compat tests")
    }
}

#[async_trait]
impl BotRuntimeConnectionService for RecordingBotRuntime {
    async fn connect_streaming(
        &self,
        command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        self.connect_count.fetch_add(1, Ordering::Relaxed);
        let negotiated_client_kind = match command.client_kind {
            Some(kind)
                if kind.eq_ignore_ascii_case("native_mcp")
                    && !self.trust_native_mcp.load(Ordering::Relaxed) =>
            {
                None
            }
            other => other,
        };
        let bot_uuid = command
            .bot_id
            .unwrap_or_else(|| "generated-bot".to_string());
        Ok(BotRuntimeConnectOutcome {
            is_new: true,
            bot_uuid,
            token: command.token.unwrap_or_else(|| "test-token".to_string()),
            negotiated_client_kind,
        })
    }

    async fn update_runtime_status(
        &self,
        command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        if self.reject_status_update.load(Ordering::Relaxed) {
            return Ok(BotRuntimeStatusOutcome {
                updated: false,
                bot_uuid: command.bot_id,
                status: command.status,
            });
        }
        self.statuses
            .lock()
            .await
            .insert(command.bot_id.clone(), command.status.clone());
        Ok(BotRuntimeStatusOutcome {
            updated: true,
            bot_uuid: command.bot_id,
            status: command.status,
        })
    }

    async fn disconnect_streaming(
        &self,
        command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        if self.fail_disconnect.load(Ordering::Relaxed) {
            return Err(ServiceError::InternalError("disconnect persistence unavailable".into()).into());
        }
        self.statuses.lock().await.remove(&command.bot_id);
        Ok(())
    }

    async fn resolve_delivery_target(
        &self,
        bot_id: &str,
    ) -> ServiceResult<BotDeliveryTarget> {
        if let Some(target) = self.delivery_target.lock().await.clone() {
            return Ok(target);
        }
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}

#[async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        unreachable!("web send is not used by bot ws compat tests")
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        if self.fail_bot_event_once.swap(false, Ordering::Relaxed) {
            return Err(ServiceError::InternalError(
                "injected bot event failure".to_string(),
            ));
        }
        self.bot_events.lock().await.push(cmd);
        Ok(BotEventOutcome {
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            unregistered_run_ids: vec![],
            mentions: vec![],
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
        })
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        unreachable!("group callback is not used by bot ws compat tests")
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        unreachable!("chat abort is not used by bot ws compat tests")
    }

    async fn register_task_run_alias(
        &self,
        task_id: &str,
        run_id: &str,
        bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        self.task_aliases.lock().await.push((
            task_id.to_string(),
            run_id.to_string(),
            bot_id.to_string(),
        ));
        Ok(if task_id == "task-1" && bot_id == "bot-worker" {
            TaskRunAliasRegistration::Registered
        } else if task_id == "task-1" {
            TaskRunAliasRegistration::Rejected
        } else {
            TaskRunAliasRegistration::NotTask
        })
    }

    async fn handle_task_dispatch(
        &self,
        cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        self.task_dispatches.lock().await.push(cmd);
        if let Some(err) = self.task_dispatch_error.lock().await.take() {
            return Err(err);
        }
        Ok(TaskDispatchOutcome {
            task_id: "task-1".to_string(),
            status: "dispatched".to_string(),
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            real_run_id: None,
})
    }

    async fn handle_task_message(
        &self,
        cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        self.task_messages.lock().await.push(cmd);
        Ok(TaskMessageOutcome {
            status: "sent".to_string(),
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
        })
    }

    async fn handle_task_complete(
        &self,
        cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        self.task_completes.lock().await.push(cmd);
        Ok(TaskCompleteOutcome {
            status: "completed".to_string(),
            blocked: false,
            pending: Vec::new(),
            callback_requested: false,
            completed_session: None,
            frontend_deliveries: vec![],
        })
    }
}

struct TestState {
    bot_runtime: Arc<RecordingBotRuntime>,
    message_flow: Arc<RecordingMessageFlow>,
    collaboration_runtime: Arc<RecordingCollaborationRuntime>,
    group: Arc<RecordingGroupCoreService>,
    system_message: Arc<RecordingSystemMessageService>,
    bot_run_context: Arc<RecordingBotRunContext>,
    interactions: Arc<RecordingInteractionService>,
    dispatch_state: Arc<BotDispatchState>,
}

fn new_state() -> TestState {
    let bot_runtime = Arc::new(RecordingBotRuntime::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
    let group = Arc::new(RecordingGroupCoreService::default());
    let system_message = Arc::new(RecordingSystemMessageService::default());
    let bot_run_context = Arc::new(RecordingBotRunContext::default());
    let interactions = Arc::new(RecordingInteractionService::default());
    let dispatch_state = Arc::new(BotDispatchState {
        expected_actor_id: None,
        bot_runtime: bot_runtime.clone(),
        message_flow: message_flow.clone(),
        collaboration_runtime: collaboration_runtime.clone(),
        bot_run_context: bot_run_context.clone(),
        bot_connections: Arc::new(BotConnectionRegistry::new()),
        run_channels: Arc::new(RunChannelManager::new()),
        task_callback: None,
        session_management: Arc::new(NoopSessionManagementService),
        group_dispatch: Arc::new(RecordingGroupDispatchContext {
            group: group.clone(),
        }),
        system_message: Some(system_message.clone()),
        coordination_processed: Arc::new(Mutex::new(HashMap::new())),
        agent_credential_backfill: None,
        interactions: interactions.clone(),
    });

    TestState {
        bot_runtime,
        message_flow,
        collaboration_runtime,
        group,
        system_message,
        bot_run_context,
        interactions,
        dispatch_state,
    }
}

fn manager_worker_group() -> Group {
    Group::new(
        "group-1",
        "bot-manager",
        vec![
            Participant::bot("bot-manager", ParticipantRole::Manager),
            Participant::bot("bot-worker", ParticipantRole::Worker),
        ],
    )
}

fn coordination_echo(tool: &str, arguments: serde_json::Value) -> String {
    serde_json::json!({
        "__bcs_coordination__": true,
        "v": 1,
        "tool": tool,
        "arguments": arguments,
        "status": "received"
    })
    .to_string()
}

fn coordination_echo_frame(
    tool_name: &str,
    phase: &str,
    tool_call_id: &str,
    echo: &str,
    is_error: bool,
) -> BcsFrame {
    BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1:abcdef12",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": tool_name,
                "phase": phase,
                "toolCallId": tool_call_id,
                "isError": is_error,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": echo
                        }
                    ]
                }
            }
        })),
        Some(1),
    ))
}

async fn recv_response(rx: &mut mpsc::Receiver<String>) -> ResponseFrame {
    let raw = rx.recv().await.expect("expected ws response");
    match serde_json::from_str::<BcsFrame>(&raw).unwrap() {
        BcsFrame::Response(res) => res,
        other => panic!("expected response frame, got {other:?}"),
    }
}

async fn connect_v3_interaction_bot(
    state: &TestState,
    tx: &mpsc::Sender<String>,
    rx: &mut mpsc::Receiver<String>,
    registered_bot_id: &mut Option<String>,
    bot_id: &str,
) {
    let connect = BcsFrame::Request(RequestFrame::new(
        format!("connect-{bot_id}"),
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": bot_id,
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        tx,
        registered_bot_id,
    )
    .await
    .unwrap();
    recv_response(rx).await;
}

async fn capture_otel_spans<F, T>(future: F) -> (T, Vec<opentelemetry_sdk::trace::SpanData>)
where
    F: Future<Output = T>,
{
    let exporter = opentelemetry_sdk::trace::InMemorySpanExporterBuilder::new().build();
    let provider = opentelemetry_sdk::trace::SdkTracerProvider::builder()
        .with_simple_exporter(exporter.clone())
        .build();
    let tracer = opentelemetry::trace::TracerProvider::tracer(&provider, "ws-response-contract");
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new("bcn_otel=info"))
        .with(tracing_opentelemetry::layer().with_tracer(tracer));

    let output = future.with_subscriber(subscriber).await;
    provider.force_flush().unwrap();
    (output, exporter.get_finished_spans().unwrap())
}

fn assert_gen_ai_output_message(
    span: &opentelemetry_sdk::trace::SpanData,
    expected_content: &str,
    expected_finish_reason: &str,
) {
    let Some(value) = span
        .attributes
        .iter()
        .find_map(|attribute| match &attribute.value {
            opentelemetry::Value::String(value)
                if attribute.key.as_str() == "gen_ai.output.messages" =>
            {
                Some(value.as_str())
            }
            _ => None,
        })
    else {
        panic!("expected gen_ai.output.messages string attribute");
    };
    let Ok(messages): Result<serde_json::Value, _> = serde_json::from_str(value) else {
        panic!("expected schema-compliant output messages JSON");
    };
    assert_eq!(messages.as_array().map(Vec::len), Some(1));
    assert_eq!(messages[0]["role"], "assistant");
    assert_eq!(messages[0]["parts"].as_array().map(Vec::len), Some(1));
    assert_eq!(messages[0]["parts"][0]["type"], "text");
    assert_eq!(messages[0]["parts"][0]["content"], expected_content);
    assert_eq!(messages[0]["finish_reason"], expected_finish_reason);
}
