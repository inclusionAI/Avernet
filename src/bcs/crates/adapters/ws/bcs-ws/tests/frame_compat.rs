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
    BotDeliveryTarget, BotDynamicStatus, BotEventCommand, BotEventOutcome, BotRuntimeConnectCommand,
    BotRuntimeConnectOutcome, BotRuntimeConnectionService, BotRuntimeDisconnectCommand,
    BotRuntimeStatusCommand, BotRuntimeStatusOutcome, BotUseCaseError, ChatAbortCommand,
    ChatAbortOutcome, ChatEventState, GroupCallbackCommand, GroupCallbackOutcome,
    CancelStateMachineRunCommand, CollaborationDefinition, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    DmActorSpec, Group, GroupCoreService, GroupMessage, GroupStatus, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, MessageFlowService, Participant, ParticipantMode,
    ParticipantRole, RedactedToken, ServiceError, ServiceResult, ServiceSpec,
    SessionHistoryResult, StartStateMachineRunCommand, StartStateMachineRunOutcome,
    StateMachineDeliveryCorrelation, StateMachineRunView, Workspace,
    SystemMessageEvent, SystemMessageService, TaskCompleteCommand, TaskCompleteOutcome,
    TaskDispatchCommand, TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
};
use bcs_session::NoopSessionManagementService;
use bcs_test_support::NoopBotRunContextPort;
use bcs_ws::bot::{BotConnectionRegistry, BotDispatchState, dispatch_frame};
use bcs_ws::shared::RunChannelManager;
use opentelemetry::trace::{SpanContext, SpanId, TraceFlags, TraceId, TraceState};
use tokio::sync::{Mutex, mpsc};
use tracing::instrument::WithSubscriber;
use tracing_subscriber::prelude::*;

#[derive(Default)]
struct RecordingMessageFlow {
    bot_events: Mutex<Vec<BotEventCommand>>,
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
}

#[derive(Default)]
struct RecordingCollaborationRuntime {
    correlation: Mutex<Option<StateMachineDeliveryCorrelation>>,
    aliases: Mutex<Vec<(String, String)>>,
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

    async fn update_workspace(&self, id: &str, _workspace: Workspace) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_label(&self, id: &str, _label: Option<String>) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_status(&self, id: &str, _status: GroupStatus) -> ServiceResult<()> {
        Err(ServiceError::GroupNotFound(id.to_string()))
    }

    async fn update_service_spec(
        &self,
        id: &str,
        _service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()> {
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
        let bot_uuid = command
            .bot_id
            .unwrap_or_else(|| "generated-bot".to_string());
        Ok(BotRuntimeConnectOutcome {
            is_new: true,
            bot_uuid,
            token: command.token.unwrap_or_else(|| "test-token".to_string()),
        })
    }

    async fn update_runtime_status(
        &self,
        command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
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

#[tokio::test]
async fn bot_connect_rejects_provider_delivery_before_streaming_registration() {
    let state = new_state();
    *state.bot_runtime.delivery_target.lock().await = Some(BotDeliveryTarget::HttpProvider {
        bot_id: "bot-provider".to_string(),
        provider_id: "provider-1".to_string(),
        provider_bot_ref: "ref-1".to_string(),
        webhook_url: "https://provider.example/webhook".to_string(),
        bcs_to_provider_token: RedactedToken::new("secret"),
        protocol_version: "2025-05-01".to_string(),
    });
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-provider",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-provider",
            "token": "existing-token",
            "protocol_version": 1
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let rejected = recv_response(&mut rx).await;
    assert!(!rejected.ok);
    assert_eq!(
        rejected.error.as_ref().map(|err| err.code.as_str()),
        Some("bot_delivery_is_provider")
    );
    assert_eq!(registered_bot_id, None);
    assert_eq!(state.bot_runtime.connect_count.load(Ordering::Relaxed), 0);
    assert!(
        !state
            .dispatch_state
            .bot_connections
            .is_connected("bot-provider")
            .await
    );
}

#[async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        unreachable!("web send is not used by bot ws compat tests")
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
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
    dispatch_state: Arc<BotDispatchState>,
}

fn new_state() -> TestState {
    let bot_runtime = Arc::new(RecordingBotRuntime::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
    let group = Arc::new(RecordingGroupCoreService::default());
    let system_message = Arc::new(RecordingSystemMessageService::default());
    let dispatch_state = Arc::new(BotDispatchState {
        bot_runtime: bot_runtime.clone(),
        message_flow: message_flow.clone(),
        collaboration_runtime: collaboration_runtime.clone(),
        bot_run_context: Arc::new(NoopBotRunContextPort),
        bot_connections: Arc::new(BotConnectionRegistry::new()),
        run_channels: Arc::new(RunChannelManager::new()),
        task_callback: None,
        session_management: Arc::new(NoopSessionManagementService),
        group_dispatch: Arc::new(RecordingGroupDispatchContext {
            group: group.clone(),
        }),
        callback_dispatch: Arc::new(bcs_test_support::NoopSessionCallbackDispatchPort),
        system_message: Some(system_message.clone()),
        coordination_processed: Arc::new(Mutex::new(HashMap::new())),
        agent_credential_backfill: None,
    });

    TestState {
        bot_runtime,
        message_flow,
        collaboration_runtime,
        group,
        system_message,
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

#[tokio::test]
async fn bot_connect_and_status_frames_are_compatible() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "bot.connect",
        Some(serde_json::json!({"bot_id": "bot-compat:staff", "protocol_version": 1})),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(registered_bot_id.as_deref(), Some("bot-compat:staff"));
    assert!(
        state
            .dispatch_state
            .bot_connections
            .is_connected("bot-compat:staff")
            .await
    );

    let status = BcsFrame::Request(RequestFrame::new(
        "status-1",
        "bot.status",
        Some(serde_json::json!({
            "status": BotStatus::Busy,
            "dynamic_summary": "working",
            "load": 0.7
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&status).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let updated = recv_response(&mut rx).await;
    assert!(updated.ok);
    let statuses = state.bot_runtime.statuses.lock().await;
    let status = statuses.get("bot-compat:staff").unwrap();
    assert_eq!(status.status, "busy");
    assert_eq!(status.dynamic_summary.as_deref(), Some("working"));
}

#[tokio::test]
async fn task_dispatch_unwraps_legacy_session_group_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-driver".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"dispatch-1","method":"task.dispatch","params":{"group_id":"group-1:abcdef12","target_bot":"bot-worker","message":"do work"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(res.ok);

    let commands = state.message_flow.task_dispatches.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(commands[0].payload["message"], "do work");
}

#[tokio::test]
async fn task_dispatch_maps_muted_target_error_code() {
    let state = new_state();
    *state.message_flow.task_dispatch_error.lock().await = Some(ServiceError::InvalidOperation {
        message: "target bot is muted".to_string(),
        request_id: None,
    });
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-driver".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"dispatch-1","method":"task.dispatch","params":{"group_id":"group-1","target_bot":"bot-worker","message":"do work"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(!res.ok);
    let err = res.error.expect("muted target should return an error");
    assert_eq!(err.code, "target_bot_muted");
    assert_eq!(err.message, "target bot is muted");
}

#[tokio::test]
async fn task_message_unwraps_legacy_session_group_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"message-1","method":"task.message","params":{"group_id":"group-1:abcdef12","message":"blocked on missing schema"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(res.ok);

    let commands = state.message_flow.task_messages.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].worker_bot_id, "bot-worker");
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(commands[0].payload["message"], "blocked on missing schema");
}

#[tokio::test]
async fn task_complete_unwraps_legacy_session_group_id() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-driver".to_string());

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"req","id":"complete-1","method":"task.complete","params":{"group_id":"group-1:abcdef12","summary":"done","status":"completed"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let res = recv_response(&mut rx).await;
    assert!(res.ok);

    let commands = state.message_flow.task_completes.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].task_id, "group-1");
    assert_eq!(commands[0].payload["group_id"], "group-1");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_dispatches_once_and_dedups() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-1", &echo, false);
    let text = serde_json::to_string(&frame).unwrap();

    dispatch_frame(
        &state.dispatch_state,
        &text,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    dispatch_frame(
        &state.dispatch_state,
        &text,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_dispatches.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].driver_bot_id, "bot-manager");
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].target_bot_id, "bot-worker");
    assert_eq!(commands[0].target_bot_name, None);
    assert_eq!(commands[0].payload["message"], "do worker task");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
    let bot_events = state.message_flow.bot_events.lock().await;
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].event_type, "agent");
    assert_eq!(bot_events[0].event_payload["stream"], "tool");
    assert_eq!(bot_events[0].event_payload["data"]["phase"], "result");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_forwards_response_mode() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task",
            "response_mode": "after-last-tool-call"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-response-mode", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_dispatches.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].payload["response_mode"], "after-last-tool-call");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_error_notifies_manager() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    *state.message_flow.task_dispatch_error.lock().await = Some(ServiceError::InvalidOperation {
        message: "target bot is muted".to_string(),
        request_id: None,
    });
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-error-notify", &echo, false);
    let text = serde_json::to_string(&frame).unwrap();

    dispatch_frame(
        &state.dispatch_state,
        &text,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let notifications = state.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    assert_eq!(notifications[0].participants.len(), 2);
    match &notifications[0].event {
        SystemMessageEvent::GenericNotification {
            group_id,
            message,
            receivers,
        } => {
            assert_eq!(group_id, "group-1");
            assert!(message.contains("[协同提醒]"));
            assert!(message.contains("target bot is muted"));
            assert_eq!(receivers.len(), 1);
            assert_eq!(receivers[0].bot_uuid, "bot-manager");
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_assign_task_whitespace_message_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "   "
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-blank-assign", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_tool_start_phase_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "start", "fc-start", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_non_exec_tool_name_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("read_file", "result", "fc-read", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_cannot_assign_task() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-worker-assign", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_send_task_message_notifies_manager_and_keeps_result_event() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": "manager progress"
        }),
    );
    let frame = coordination_echo_frame("shell", "result", "fc-manager-message", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_messages.lock().await.is_empty());
    let notifications = state.system_message.notifications.lock().await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:abcdef12");
    match &notifications[0].event {
        SystemMessageEvent::GenericNotification {
            group_id,
            message,
            receivers,
        } => {
            assert_eq!(group_id, "group-1");
            assert!(message.contains("[协同提醒]"));
            assert!(message.contains("only worker bot can send task messages"));
            assert_eq!(receivers.len(), 1);
            assert_eq!(receivers[0].bot_uuid, "bot-manager");
        }
        other => panic!("expected GenericNotification, got {other:?}"),
    }
    let bot_events = state.message_flow.bot_events.lock().await;
    assert_eq!(bot_events.len(), 1);
    assert_eq!(bot_events[0].event_type, "agent");
    assert_eq!(bot_events[0].event_payload["stream"], "tool");
    assert_eq!(bot_events[0].event_payload["data"]["phase"], "result");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_send_task_message_dispatches_with_session() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": "worker progress"
        }),
    );
    let frame = coordination_echo_frame("shell", "result", "fc-message", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_messages.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].worker_bot_id, "bot-worker");
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].payload["message"], "worker progress");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_send_task_message_empty_message_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": ""
        }),
    );
    let frame = coordination_echo_frame("shell", "result", "fc-blank-message", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_messages.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_worker_send_task_message_without_real_session_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());
    let echo = coordination_echo(
        "bcs_send_task_message",
        serde_json::json!({
            "message": "worker progress"
        }),
    );
    let frame = BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "shell",
                "phase": "result",
                "toolCallId": "fc-message-no-session",
                "isError": false,
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
    ));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_messages.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_task_complete_dispatches_via_echo() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_task_complete",
        serde_json::json!({
            "summary": "all done"
        }),
    );
    let frame = coordination_echo_frame("mcporter", "result", "fc-complete", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let commands = state.message_flow.task_completes.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].task_id, "group-1");
    assert_eq!(commands[0].bot_id, "bot-manager");
    assert!(commands[0].via_echo);
    assert_eq!(commands[0].payload["group_id"], "group-1");
    assert_eq!(commands[0].payload["summary"], "all done");
    assert_eq!(commands[0].payload["status"], "completed");
    assert_eq!(commands[0].payload["bcs_session_id"], "group-1:abcdef12");
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_manager_task_complete_whitespace_summary_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_task_complete",
        serde_json::json!({
            "summary": "   "
        }),
    );
    let frame = coordination_echo_frame("mcporter", "result", "fc-blank-complete", &echo, false);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_completes.lock().await.is_empty());
}

#[tokio::test]
#[ignore = "coordination echo handling moved from ws dispatcher to bcs-message-flow"]
async fn coordination_echo_error_result_does_not_dispatch() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let frame = coordination_echo_frame("exec", "result", "fc-error", &echo, true);

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&frame).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
}

#[tokio::test]
async fn chat_event_with_tool_like_coordination_payload_is_not_coordination_echo() {
    let state = new_state();
    state.group.insert(manager_worker_group()).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());
    let echo = coordination_echo(
        "bcs_assign_task",
        serde_json::json!({
            "target_bot": "bot-worker",
            "message": "do worker task"
        }),
    );
    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-chat-spoof",
            "bcs_group_id": "group-1:abcdef12",
            "state": WireChatEventState::Final,
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "exec",
                "phase": "result",
                "toolCallId": "fc-chat-spoof",
                "isError": false,
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
    ));

    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert!(state.message_flow.task_dispatches.lock().await.is_empty());
    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, "bot-manager");
    assert_eq!(events[0].run_id, "run-chat-spoof");
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].state, ChatEventState::Final);
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}

#[tokio::test]
async fn bot_chat_event_frame_is_forwarded_to_message_flow() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Final
        })),
        Some(1),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, "bot-compat:staff");
    assert_eq!(events[0].run_id, "run-1");
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].state, ChatEventState::Final);
    assert_eq!(
        events[0].bcs_session_id.as_deref(),
        Some("group-1:00000000")
    );
}

#[tokio::test]
async fn traced_chat_event_creates_bot_response_child_span_after_message_flow_accepts() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let trace_parent = SpanContext::new(
        TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap(),
        SpanId::from_hex("b7ad6b7169203331").unwrap(),
        TraceFlags::SAMPLED,
        false,
        TraceState::default(),
    );
    state
        .dispatch_state
        .run_channels
        .register_with_trace_parent(
            "run-traced".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("http-chat-async".to_string()),
            None,
            Some(trace_parent),
        )
        .await;
    assert!(
        state
            .dispatch_state
            .run_channels
            .trace_parent("run-traced")
            .await
            .is_some()
    );
    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-traced",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Delta,
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "ws-response-content" }],
                "timestamp": 123
            }
        })),
        Some(1),
    ));
    let text = serde_json::to_string(&event).unwrap();

    let (result, spans) = capture_otel_spans(async move {
        dispatch_frame(
            &state.dispatch_state,
            &text,
            &tx,
            &mut registered_bot_id,
        )
        .await
    })
    .await;

    result.unwrap();
    let span = spans
        .iter()
        .find(|span| span.name == "bcn.bot.response")
        .unwrap_or_else(|| panic!("expected bot response span, got {spans:#?}"));
    assert_eq!(span.span_context.trace_id().to_string(), "0af7651916cd43dd8448eb211c80319c");
    assert_eq!(span.parent_span_id.to_string(), "b7ad6b7169203331");
    assert!(span.events.events.is_empty());
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == "bcn.content.untrusted"
            && attr.value == opentelemetry::Value::Bool(false)
    }));
    assert!(span.attributes.iter().all(|attr| {
        attr.key.as_str() != "gen_ai.output.messages"
    }));
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == "bcn.bot.response.chunk"
            && matches!(&attr.value, opentelemetry::Value::String(value) if value.as_str() == "ws-response-content")
    }));
}

#[tokio::test]
async fn traced_plugin_run_alias_creates_bot_response_child_span() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let trace_parent = SpanContext::new(
        TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap(),
        SpanId::from_hex("b7ad6b7169203331").unwrap(),
        TraceFlags::SAMPLED,
        false,
        TraceState::default(),
    );
    state
        .dispatch_state
        .run_channels
        .register_with_trace_parent(
            "gateway-run".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("http-chat-async".to_string()),
            None,
            Some(trace_parent),
        )
        .await;

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "plugin-run",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Final,
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "plugin-ws-response" }],
                "timestamp": 123
            }
        })),
        Some(1),
    ));
    let event_text = serde_json::to_string(&event).unwrap();

    let (result, spans) = capture_otel_spans(async move {
        dispatch_frame(
            &state.dispatch_state,
            r#"{"type":"res","id":"gateway-run","ok":true,"payload":{"run_id":"plugin-run"}}"#,
            &tx,
            &mut registered_bot_id,
        )
        .await?;
        dispatch_frame(
            &state.dispatch_state,
            &event_text,
            &tx,
            &mut registered_bot_id,
        )
        .await
    })
    .await;

    result.unwrap();
    let span = spans
        .iter()
        .find(|span| span.name == "bcn.bot.response")
        .unwrap_or_else(|| panic!("expected aliased bot response span, got {spans:#?}"));
    assert_eq!(span.span_context.trace_id().to_string(), "0af7651916cd43dd8448eb211c80319c");
    assert_eq!(span.parent_span_id.to_string(), "b7ad6b7169203331");
    assert!(span.events.events.is_empty());
    assert_gen_ai_output_message(span, "plugin-ws-response", "stop");
}

#[tokio::test]
async fn chat_event_without_trace_mapping_does_not_create_response_span() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-untraced",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Delta,
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "untraced-ws-response" }],
                "timestamp": 123
            }
        })),
        Some(1),
    ));
    let text = serde_json::to_string(&event).unwrap();

    let (result, spans) = capture_otel_spans(async move {
        dispatch_frame(
            &state.dispatch_state,
            &text,
            &tx,
            &mut registered_bot_id,
        )
        .await
    })
    .await;

    result.unwrap();
    assert!(spans.is_empty());
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

#[tokio::test]
async fn bot_response_registers_state_machine_delivery_alias() {
    let state = new_state();
    *state.collaboration_runtime.correlation.lock().await = Some(StateMachineDeliveryCorrelation {
        state_machine_run_id: "sm-run-1".to_string(),
        node_id: "review".to_string(),
        attempt: 1,
        assignee_bot_id: "bot-compat:staff".to_string(),
        delivery_request_id: "delivery-1".to_string(),
        bot_delivery_run_id: None,
    });
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let response = BcsFrame::Response(ResponseFrame::ok(
        "delivery-1",
        serde_json::json!({"run_id": "bot-run-actual"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert_eq!(
        *state.collaboration_runtime.aliases.lock().await,
        vec![("delivery-1".to_string(), "bot-run-actual".to_string())]
    );
}

#[tokio::test]
async fn task_response_ack_from_target_worker_registers_sub_run_alias() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-worker".to_string());

    state
        .dispatch_state
        .run_channels
        .register(
            "task-1".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("workbench-ws".to_string()),
            Some("human_1".to_string()),
        )
        .await;

    let response = BcsFrame::Response(ResponseFrame::ok(
        "task-1",
        serde_json::json!({"run_id": "worker-run-1"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert_eq!(
        *state.message_flow.task_aliases.lock().await,
        vec![(
            "task-1".to_string(),
            "worker-run-1".to_string(),
            "bot-worker".to_string()
        )]
    );
    assert_eq!(
        state
            .dispatch_state
            .run_channels
            .session_for_run("worker-run-1")
            .await
            .as_deref(),
        Some("group-1:abcdef12")
    );
}

#[tokio::test]
async fn task_response_ack_from_manager_does_not_register_sub_run_alias() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-manager".to_string());

    state
        .dispatch_state
        .run_channels
        .register(
            "task-1".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("workbench-ws".to_string()),
            Some("human_1".to_string()),
        )
        .await;

    let response = BcsFrame::Response(ResponseFrame::ok(
        "task-1",
        serde_json::json!({"run_id": "manager-run-1"}),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&response).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    assert_eq!(
        *state.message_flow.task_aliases.lock().await,
        vec![(
            "task-1".to_string(),
            "manager-run-1".to_string(),
            "bot-manager".to_string()
        )]
    );
    assert_eq!(
        state
            .dispatch_state
            .run_channels
            .session_for_run("manager-run-1")
            .await,
        None
    );
}

#[tokio::test]
async fn bot_chat_event_frame_unwraps_v2_session_id_group_id() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-session-1",
            "bcs_group_id": "group-1:abcdef12",
            "state": WireChatEventState::Final
        })),
        Some(1),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}

#[tokio::test]
async fn bot_event_frame_restores_session_id_from_sub_run_mapping() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    state
        .dispatch_state
        .run_channels
        .register(
            "outer-run".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("workbench-ws".to_string()),
            Some("human_1".to_string()),
        )
        .await;

    dispatch_frame(
        &state.dispatch_state,
        r#"{"type":"res","id":"outer-run","ok":true,"payload":{"run_id":"sub-run"}}"#,
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let event = BcsFrame::Event(EventFrame::new(
        "agent",
        Some(serde_json::json!({
            "run_id": "sub-run",
            "bcs_group_id": "group-1",
            "stream": "assistant",
            "ts": 123,
            "data": {
                "delta": "hi"
            }
        })),
        Some(1),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].run_id, "sub-run");
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}

#[tokio::test]
async fn bot_chat_event_frame_uses_explicit_bcs_session_id() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-session-2",
            "bcs_group_id": "group-1",
            "bcs_session_id": "group-1:abcdef12",
            "state": WireChatEventState::Final
        })),
        Some(1),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let events = state.message_flow.bot_events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].group_id, "group-1");
    assert_eq!(events[0].bcs_session_id.as_deref(), Some("group-1:abcdef12"));
}
