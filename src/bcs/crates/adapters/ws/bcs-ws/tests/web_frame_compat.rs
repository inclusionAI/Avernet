use std::sync::Arc;

use async_trait::async_trait;
use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_service_api::{
    BotEventCommand, BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, GroupCallbackCommand,
    GroupCallbackOutcome, MessageFlowService, ParticipantKind, ParticipantMode, ServiceResult,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand,
    WorkbenchConnectOutcome, WorkbenchParticipantView, WorkbenchSessionService,
    WorkbenchUseCaseError,
};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{
    WebClientConnectionState, WebDispatchOutcome, WebDispatchState, WorkbenchConnectionRegistry,
    dispatch_client_frame,
};
use tokio::sync::{Mutex, mpsc};

#[derive(Default)]
struct RecordingMessageFlow {
    web_sends: Mutex<Vec<WebSendCommand>>,
}

#[derive(Default)]
struct RecordingWorkbenchSessions {
    connects: Mutex<Vec<WorkbenchConnectCommand>>,
    authorizations: Mutex<Vec<WorkbenchChatAuthorizationCommand>>,
}

#[async_trait]
impl WorkbenchSessionService for RecordingWorkbenchSessions {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        self.connects.lock().await.push(command.clone());
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

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
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
    let workbench_sessions = Arc::new(RecordingWorkbenchSessions::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let frontend_connections = Arc::new(WorkbenchConnectionRegistry::new());
    let dispatch_state = Arc::new(WebDispatchState {
        message_flow: message_flow.clone(),
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
        Some("human_100001"),
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
        Some("human_100001"),
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
        Some("human_100001"),
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {:?}", sent);
    assert_eq!(sent.payload.unwrap()["runId"], "run-web-1");
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
        Some("human_100001"),
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
        Some("human_100001"),
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
