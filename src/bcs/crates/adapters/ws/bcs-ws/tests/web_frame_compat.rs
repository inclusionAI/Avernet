use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    CollaborationDefinition, StateMachineDeliveryCorrelation, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_service_api::{
    BotEventCommand, BotEventOutcome, CancelStateMachineRunCommand, ChatAbortCommand,
    ChatAbortOutcome, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, GroupCallbackCommand,
    GroupCallbackOutcome, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, MessageFlowService,
    ParticipantKind, ParticipantMode, RespondHumanNodeOutcome, ServiceResult, SessionHistoryResult,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineRunView,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchConnectOutcome, WorkbenchParticipantView,
    WorkbenchSessionService, WorkbenchUseCaseError,
};
use bcs_test_support::NoopCollaborationRuntimeService;
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{
    WebClientConnectionState, WebDispatchOutcome, WebDispatchState, WorkbenchConnectionAuth,
    WorkbenchConnectionRegistry, dispatch_client_frame,
};
use tokio::sync::{Mutex, mpsc};

#[derive(Clone, Copy)]
enum SessionHumanInputBehavior {
    Consumed,
    Conflict,
}

struct RecordingCollaborationRuntime {
    behavior: SessionHumanInputBehavior,
    commands: Mutex<Vec<HandleSessionHumanInputCommand>>,
}

impl RecordingCollaborationRuntime {
    fn new(behavior: SessionHumanInputBehavior) -> Self {
        Self {
            behavior,
            commands: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl CollaborationRuntimeService for RecordingCollaborationRuntime {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        unreachable!("run start is not used by web ws compat tests")
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn handle_session_human_input(
        &self,
        cmd: HandleSessionHumanInputCommand,
    ) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> {
        self.commands.lock().await.push(cmd.clone());
        match self.behavior {
            SessionHumanInputBehavior::Consumed => Ok(HandleSessionHumanInputOutcome::Consumed {
                response: RespondHumanNodeOutcome {
                    node: StateMachineNodeRun {
                        run_id: "state-run-1".to_string(),
                        node_id: "human-review".to_string(),
                        status: StateMachineNodeStatus::Completed,
                        attempt: 0,
                        node_timeout_ms: Some(60_000),
                        timeout_deadline_ms: None,
                        max_attempts: 1,
                        assignee_bot_id: None,
                        outcome: Some("complete".to_string()),
                        responded_by: Some(cmd.caller_actor_id),
                        delivery_request_id: None,
                        bot_delivery_run_id: None,
                        artifact_text: Some(cmd.content),
                        error: None,
                        started_at: Some(1),
                        completed_at: Some(2),
                    },
                    run: StateMachineRun {
                        run_id: "state-run-1".to_string(),
                        definition_id: "definition-1".to_string(),
                        definition_version: 1,
                        group_id: cmd.group_id,
                        group_version: 1,
                        session_id: cmd.session_id.expect("session id"),
                        created_by: None,
                        status: StateMachineRunStatus::Running,
                        input: serde_json::Value::Null,
                        output: None,
                        error: None,
                        created_at: 1,
                        updated_at: 2,
                        completed_at: None,
                    },
                },
            }),
            SessionHumanInputBehavior::Conflict => Err(CollaborationRuntimeError::Conflict(
                "state machine is not waiting for Human input".to_string(),
            )),
        }
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
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: false,
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
        unreachable!("runtime configuration is not used by web ws compat tests")
    }
}

#[derive(Default)]
struct RecordingMessageFlow {
    web_sends: Mutex<Vec<WebSendCommand>>,
    aborts: Mutex<Vec<ChatAbortCommand>>,
}

#[derive(Default)]
struct RecordingWorkbenchSessions {
    connects: Mutex<Vec<WorkbenchConnectCommand>>,
    authorizations: Mutex<Vec<WorkbenchChatAuthorizationCommand>>,
    connect_error: Mutex<Option<WorkbenchUseCaseError>>,
}

#[async_trait]
impl WorkbenchSessionService for RecordingWorkbenchSessions {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        self.connects.lock().await.push(command.clone());
        if let Some(error) = self.connect_error.lock().await.take() {
            return Err(error);
        }
        Ok(WorkbenchConnectOutcome {
            group_id: command.group_id,
            participants: vec![WorkbenchParticipantView {
                bot_uuid: "human_100001".to_string(),
                role: "observer".to_string(),
                kind: ParticipantKind::Bot,
                mode: Some(ParticipantMode::Present),
            }],
        })
    }

    async fn authorize_chat_send(
        &self,
        command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        self.authorizations.lock().await.push(command);
        Ok(())
    }
}

#[async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        self.web_sends.lock().await.push(cmd);
        Ok(WebSendOutcome {
            primary_run_id: "run-web-1".to_string(),
            active_run_ids: vec!["run-web-1".to_string()],
            status: "accepted".to_string(),
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            mentions: vec![],
            hidden_mentions: vec![],
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
        })
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        unreachable!("bot event is not used by web ws compat tests")
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        unreachable!("group callback is not used by web ws compat tests")
    }

    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        self.aborts.lock().await.push(cmd);
        Ok(ChatAbortOutcome {
            aborted: true,
            aborted_run_ids: vec![],
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
        })
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Ok(TaskRunAliasRegistration::NotTask)
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        unreachable!("task dispatch is not used by web ws compat tests")
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        unreachable!("task complete is not used by web ws compat tests")
    }
}

struct TestState {
    workbench_sessions: Arc<RecordingWorkbenchSessions>,
    message_flow: Arc<RecordingMessageFlow>,
    dispatch_state: Arc<WebDispatchState>,
}

fn new_state() -> TestState {
    new_state_with_collaboration_runtime(Arc::new(NoopCollaborationRuntimeService))
}

fn new_state_with_collaboration_runtime(
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
) -> TestState {
    let workbench_sessions = Arc::new(RecordingWorkbenchSessions::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let frontend_connections = Arc::new(WorkbenchConnectionRegistry::new());
    let dispatch_state = Arc::new(WebDispatchState {
        message_flow: message_flow.clone(),
        collaboration_runtime,
        workbench_sessions: workbench_sessions.clone(),
        frontend_connections,
        run_channels: Arc::new(RunChannelManager::new()),
    });

    TestState {
        workbench_sessions,
        message_flow,
        dispatch_state,
    }
}

async fn recv_response(rx: &mut mpsc::Receiver<String>) -> ResponseFrame {
    let raw = rx.recv().await.expect("expected ws response");
    match serde_json::from_str::<BcsFrame>(&raw).unwrap() {
        BcsFrame::Response(res) => res,
        other => panic!("expected response frame, got {other:?}"),
    }
}

#[tokio::test]
async fn web_connect_frame_subscribes_frontend_registry() {
    let state = new_state();

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "connect",
        Some(serde_json::json!({"group_id": "group-web-1"})),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();
    assert_eq!(outcome, WebDispatchOutcome::ClientConnect { subscribed: true });

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .connection_count("group-web-1")
            .await,
        1
    );
    assert_eq!(connection_state.subscribed_sessions.len(), 1);
    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 1);
    assert_eq!(connects[0].group_id, "group-web-1");
    assert_eq!(connects[0].session_id, None);
    assert_eq!(connects[0].bound_actor_id.as_deref(), Some("human_100001"));
}

#[tokio::test]
async fn web_connect_with_session_id_subscribes_session_registry_key() {
    let state = new_state();

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "group-web-1:abcdef12",
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();
    assert_eq!(outcome, WebDispatchOutcome::ClientConnect { subscribed: true });

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .connection_count("group-web-1")
            .await,
        0
    );
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .connection_count("group-web-1:abcdef12")
            .await,
        1
    );
    assert_eq!(connection_state.subscribed_sessions.len(), 1);
    assert_eq!(
        connection_state.subscribed_sessions[0].0,
        "group-web-1:abcdef12"
    );

    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 1);
    assert_eq!(connects[0].group_id, "group-web-1");
    assert_eq!(
        connects[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
}

#[tokio::test]
async fn web_chat_send_frame_is_forwarded_to_message_flow_and_tracks_run() {
    let state = new_state();

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {:?}", sent);
    assert_eq!(sent.payload.unwrap()["runId"], "run-web-1");
    assert!(rx.try_recv().is_err());
    assert_eq!(connection_state.active_run_ids, vec!["run-web-1"]);
    assert!(
        state
            .dispatch_state
            .run_channels
            .is_registered("run-web-1")
            .await,
        "chat.send should register the run channel for bot events that do not echo bcs_session_id"
    );
    assert_eq!(
        state
            .dispatch_state
            .run_channels
            .get_session_runs("group-web-1:abcdef12")
            .await,
        vec!["run-web-1".to_string()]
    );

    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].group_id, "group-web-1");
    assert_eq!(calls[0].from_actor_id, "human_100001");
    assert_eq!(calls[0].session_id.as_deref(), Some("group-web-1:abcdef12"));
    assert_eq!(calls[0].message, "hello");
    let authorizations = state.workbench_sessions.authorizations.lock().await;
    assert_eq!(authorizations.len(), 1);
    assert_eq!(authorizations[0].group_id, "group-web-1");
    assert_eq!(authorizations[0].from_actor_id, "human_100001");
    assert_eq!(
        authorizations[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
    assert_eq!(
        authorizations[0].bound_actor_id.as_deref(),
        Some("human_100001")
    );
}

#[tokio::test]
async fn web_chat_send_is_consumed_by_the_single_pending_human_input_and_emits_empty_final() {
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::new(
        SessionHumanInputBehavior::Consumed,
    ));
    let state = new_state_with_collaboration_runtime(collaboration_runtime.clone());
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-human-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "untrusted-payload-actor",
            "sessionKey": "group-web-1:abcdef12",
            "message": "批准发布"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {sent:?}");
    assert_eq!(sent.payload.unwrap()["runId"], "state-run-1");

    let final_frame: serde_json::Value = serde_json::from_str(
        &rx.recv()
            .await
            .expect("HumanInput completion should emit a final chat event"),
    )
    .unwrap();
    assert_eq!(final_frame["type"], "event");
    assert_eq!(final_frame["event"], "chat");
    assert_eq!(final_frame["group_id"], "group-web-1");
    assert_eq!(final_frame["bot_uuid"], "bcs_state_machine");
    assert_eq!(final_frame["payload"]["run_id"], "state-run-1");
    assert_eq!(
        final_frame["payload"]["bcs_session_id"],
        "group-web-1:abcdef12"
    );
    assert_eq!(final_frame["payload"]["state"], "final");
    assert_eq!(final_frame["payload"]["message"]["role"], "assistant");
    assert_eq!(
        final_frame["payload"]["message"]["content"],
        serde_json::json!([])
    );
    assert!(rx.try_recv().is_err());
    assert!(state.message_flow.web_sends.lock().await.is_empty());
    let commands = collaboration_runtime.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id, "human_100001");
    assert_eq!(commands[0].content, "批准发布");
    assert_eq!(
        commands[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
}

#[tokio::test]
async fn web_chat_send_is_rejected_when_state_machine_has_no_pending_human_input() {
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::new(
        SessionHumanInputBehavior::Conflict,
    ));
    let state = new_state_with_collaboration_runtime(collaboration_runtime);
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-human-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sessionKey": "group-web-1:abcdef12",
            "message": "不应进入 Bot 群聊"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let rejected = recv_response(&mut rx).await;
    assert!(!rejected.ok);
    assert_eq!(
        rejected.error.as_ref().map(|error| error.code.as_str()),
        Some("conflict")
    );
    let response_error = rejected.error.as_ref().expect("conflict error");

    let error_frame: serde_json::Value = serde_json::from_str(
        &rx.recv()
            .await
            .expect("HumanInput rejection should emit a chat error event"),
    )
    .unwrap();
    assert_eq!(error_frame["type"], "event");
    assert_eq!(error_frame["event"], "chat");
    assert_eq!(error_frame["group_id"], "group-web-1");
    assert_eq!(error_frame["bot_uuid"], "bcs_state_machine");
    assert_eq!(
        error_frame["payload"]["bcs_session_id"],
        "group-web-1:abcdef12"
    );
    assert_eq!(error_frame["payload"]["state"], "error");
    assert_eq!(error_frame["payload"]["errorCode"], response_error.code);
    assert_eq!(
        error_frame["payload"]["errorMessage"],
        response_error.message
    );
    assert_eq!(
        error_frame["payload"]["message"]["content"][0]["text"],
        response_error.message
    );
    assert!(rx.try_recv().is_err());
    assert!(state.message_flow.web_sends.lock().await.is_empty());
}

#[tokio::test]
async fn web_chat_send_uses_session_subscription_key_for_sender_conn_id() {
    let state = new_state();

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "group-web-1:abcdef12",
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    let sender_conn_id = connection_state.subscribed_sessions[0].1;

    let send = BcsFrame::Request(RequestFrame::new(
        "send-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {:?}", sent);

    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].session_id.as_deref(), Some("group-web-1:abcdef12"));
    assert_eq!(calls[0].sender_conn_id, Some(sender_conn_id));
}

#[tokio::test]
async fn user_bound_chat_abort_preserves_existing_response() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-1",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "run_id": "run-web-1"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert!(response.ok);
    assert_eq!(response.payload.as_ref().expect("payload")["aborted"], true);
    assert_eq!(state.message_flow.aborts.lock().await.len(), 1);
}

#[tokio::test]
async fn user_bound_unknown_method_preserves_protocol_error_response() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let request = BcsFrame::Request(RequestFrame::new("unknown-1", "future.method", None));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&request).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
        },
    )
    .await
    .unwrap();

    let response = recv_response(&mut rx).await;
    assert!(!response.ok);
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("unknown_method")
    );
}

fn session_bound_auth() -> WorkbenchConnectionAuth {
    WorkbenchConnectionAuth::SessionBound {
        tenant: "tenant-a".to_string(),
        actor_id: "human_100001".to_string(),
        group_id: "group-web-1".to_string(),
        session_id: "session-bound-1".to_string(),
    }
}

async fn connect_session_bound(
    state: &TestState,
    tx: &mpsc::Sender<String>,
    rx: &mut mpsc::Receiver<String>,
    connection_state: &mut WebClientConnectionState,
) -> WebDispatchOutcome {
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-bound",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));
    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        tx,
        connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(recv_response(rx).await.ok);
    outcome
}

#[tokio::test]
async fn session_bound_requires_connect_before_business_frames() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-before-connect",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1",
            "message": "hello"
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Dispatched);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("connect_required")
    );
    assert!(state.message_flow.web_sends.lock().await.is_empty());
}

#[tokio::test]
async fn session_bound_scope_mismatch_closes_without_dynamic_authorization() {
    for params in [
        serde_json::json!({
            "group_id": "other-group",
            "session_id": "session-bound-1"
        }),
        serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "other-session"
        }),
    ] {
        let state = new_state();
        let (tx, mut rx) = mpsc::channel(8);
        let mut connection_state = WebClientConnectionState::default();
        let connect = BcsFrame::Request(RequestFrame::new(
            "connect-mismatch",
            "connect",
            Some(params),
        ));

        let outcome = dispatch_client_frame(
            &state.dispatch_state,
            &serde_json::to_string(&connect).unwrap(),
            &tx,
            &mut connection_state,
            &session_bound_auth(),
        )
        .await
        .unwrap();

        assert_eq!(outcome, WebDispatchOutcome::Close);
        let response = recv_response(&mut rx).await;
        assert_eq!(
            response.error.as_ref().map(|error| error.code.as_str()),
            Some("token_scope_mismatch")
        );
        assert!(state.workbench_sessions.connects.lock().await.is_empty());
    }
}

#[tokio::test]
async fn session_bound_connect_authorizes_once_with_immutable_binding() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();

    assert_eq!(
        connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await,
        WebDispatchOutcome::ClientConnect { subscribed: true }
    );
    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 1);
    assert_eq!(connects[0].bound_actor_id.as_deref(), Some("human_100001"));
    assert_eq!(connects[0].group_id, "group-web-1");
    assert_eq!(connects[0].session_id.as_deref(), Some("session-bound-1"));
    drop(connects);

    let second = BcsFrame::Request(RequestFrame::new(
        "connect-again",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));
    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&second).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Dispatched);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("already_connected")
    );
    assert_eq!(state.workbench_sessions.connects.lock().await.len(), 1);
}

#[tokio::test]
async fn session_bound_chat_send_cannot_escape_bound_scope() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let send = BcsFrame::Request(RequestFrame::new(
        "send-mismatch",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "other-session",
            "bot_uuid": "other-actor",
            "message": "escape"
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Close);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("token_scope_mismatch")
    );
    assert!(state.workbench_sessions.authorizations.lock().await.is_empty());
    assert!(state.message_flow.web_sends.lock().await.is_empty());
}

#[tokio::test]
async fn session_bound_chat_send_uses_bound_human_identity() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let send = BcsFrame::Request(RequestFrame::new(
        "send-bound",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1",
            "bot_uuid": "payload-controlled-actor",
            "message": "hello"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let authorizations = state.workbench_sessions.authorizations.lock().await;
    assert_eq!(authorizations.len(), 1);
    assert_eq!(authorizations[0].from_actor_id, "human_100001");
    drop(authorizations);
    let sends = state.message_flow.web_sends.lock().await;
    assert_eq!(sends.len(), 1);
    assert_eq!(sends[0].from_actor_id, "human_100001");
    assert_eq!(sends[0].session_id.as_deref(), Some("session-bound-1"));
}

#[tokio::test]
async fn session_bound_chat_abort_passes_only_the_bound_session() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    connect_session_bound(&state, &tx, &mut rx, &mut connection_state).await;
    let abort = BcsFrame::Request(RequestFrame::new(
        "abort-bound",
        "chat.abort",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "run_id": "run-bound"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&abort).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    let aborts = state.message_flow.aborts.lock().await;
    assert_eq!(aborts.len(), 1);
    assert_eq!(aborts[0].group_id, "group-web-1");
    assert_eq!(aborts[0].run_id.as_deref(), Some("run-bound"));
    assert_eq!(aborts[0].session_id.as_deref(), Some("session-bound-1"));
}

#[tokio::test]
async fn session_bound_revoked_access_closes_with_stable_error() {
    let state = new_state();
    *state.workbench_sessions.connect_error.lock().await =
        Some(WorkbenchUseCaseError::ForbiddenGroupAccess);
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-revoked",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));

    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();

    assert_eq!(outcome, WebDispatchOutcome::Close);
    let response = recv_response(&mut rx).await;
    assert_eq!(
        response.error.as_ref().map(|error| error.code.as_str()),
        Some("session_access_revoked")
    );
    assert_eq!(state.workbench_sessions.connects.lock().await.len(), 1);
}
