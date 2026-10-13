//! Final-sender authorization and invalidation unbinding over the
//! REAL WS chain (bot-authority plan Task 7.1/7.2, spec §8.2):
//! real V1 connect → 服务授权 → registry + protected run
//! registration → the real typed event producer path
//! (FrontendDeliveryPort) → bounded queue → per-frame final
//! authorization → socket writer.
//!
//! Control is coordinated with barriers (the authorization gate);
//! no sleeps guess at ordering. The DECISION MATRIX itself lives in
//! `bcs-app-session/tests/delivery_authorization.rs`; this suite
//! pins the ADAPTER mechanics around it.

mod support;

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_domain::MessageViewScope;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::application::v1::{
    ApplicationError, DeliveryAuthorizationService, DeliveryDecision,
    GroupSessionConnectionBinding, GroupSessionConnectionError, ParticipantRole,
    SessionParticipant,
    GroupSessionConnectionService, IssueGroupSessionConnectionToken,
    IssuedGroupSessionConnectionToken, ProtectedDeliveryBinding, VerifyGroupSessionConnectionToken,
};
use bcs_domain::{ActorKind, MessageAudience, MessageVisibilityDomain, ParticipantMode};
use bcs_service_api::{
    BotEventCommand, BotEventOutcome, ChatAbortCommand, ChatAbortOutcome,
    FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
    FrontendDeliveryTarget, GroupCallbackCommand, GroupCallbackOutcome,
    MessageFlowService, ParticipantKind, ServiceResult, TaskDispatchCommand,
    TaskDispatchOutcome, TaskRunAliasRegistration, TaskCompleteCommand,
    TaskCompleteOutcome, WebSendCommand, WebSendOutcome,
    WorkbenchChatAuthorizationCommand, WorkbenchChatAbortAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchConnectOutcome, WorkbenchParticipantView,
    WorkbenchSessionService, WorkbenchUseCaseError,
};
use bcs_test_support::{NoopCollaborationRuntimeService, NoopInteractionService, NoopWsLifecycleInstrumentationHook};
use bcs_ws::shared::{
    DeliveryVisibility as SharedVisibility, OutboundFrame, PROTECTED_FRAME_QUEUE_SIZE,
    RunChannelManager, RunClientSink,
};
use bcs_ws::web::{
    dispatch_client_frame, group_session_websocket_router, WorkbenchConnectionAuth,
    WorkbenchConnectionRegistry, WebDispatchState, WebClientConnectionState,
};
use bcs_ws::web::WorkbenchFrontendDelivery;
use futures::{SinkExt, StreamExt};
use std::future::Future;
use serde_json::json;
use tokio::sync::{mpsc, watch, Mutex};
use tokio_tungstenite::tungstenite::Message;

use support::GateAuthorization;

const SESSION_ID: &str = "session-a";
const GROUP_ID: &str = "group-a";

// ---------------------------------------------------------------------------
// Real-chain harness
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
enum VerifyMode {
    Valid,
    #[allow(dead_code)]
    Invalid,
}

struct RecordingConnectionService {
    mode: VerifyMode,
    /// View selections received at connect (fresh V1 wire field).
    views: Mutex<Vec<String>>,
}

impl RecordingConnectionService {
    fn new(mode: VerifyMode) -> Self {
        Self {
            mode,
            views: Mutex::new(Vec::new()),
        }
    }
}

fn connect_binding() -> GroupSessionConnectionBinding {
    GroupSessionConnectionBinding {
        tenant: Some("tenant-a".into()),
        user_id: "user-a".into(),
        group_id: GROUP_ID.into(),
        session_id: SESSION_ID.into(),
    }
}

#[async_trait]
impl GroupSessionConnectionService for RecordingConnectionService {
    async fn issue_token(
        &self,
        _command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError> {
        panic!("issuance is not part of the WS connect suite")
    }

    async fn verify_token(
        &self,
        _command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError> {
        match self.mode {
            VerifyMode::Valid => Ok(connect_binding()),
            VerifyMode::Invalid => Err(GroupSessionConnectionError::InvalidConnectionToken),
        }
    }

    async fn authorize_connect(
        &self,
        command: bcs_service_api::application::v1::AuthorizeGroupSessionConnection,
    ) -> Result<bcs_service_api::application::v1::AuthorizedGroupSessionConnection, GroupSessionConnectionError>
    {
        match command.view {
            bcs_service_api::application::v1::ConnectionView::HumanSelf => {
                Ok(bcs_service_api::application::v1::AuthorizedGroupSessionConnection {
                    view: bcs_service_api::application::v1::ConnectionView::HumanSelf,
                    participants: Vec::new(),
                })
            }
            bcs_service_api::application::v1::ConnectionView::Bot { bot_id } => {
                self.views
                    .lock()
                    .await
                    .push(bot_id.clone());
                Ok(bcs_service_api::application::v1::AuthorizedGroupSessionConnection {
                    view: bcs_service_api::application::v1::ConnectionView::Bot {
                        bot_id: bot_id.clone(),
                    },
                    participants: vec![SessionParticipant {
                        actor_id: bot_id,
                        actor_kind: ActorKind::Bot,
                        name: None,
                        role: ParticipantRole::Worker,
                        tags: Vec::new(),
                        mode: ParticipantMode::Auto,
                        message_view_scope: MessageViewScope::Full,
                        joined_at: None,
                    }],
                })
            }
        }
    }
}

/// The message-flow double: chat.send produces REAL run ids the
/// dispatcher registers the protected sink for.
struct RunProducingFlow {
    runs: Vec<String>,
    received: Mutex<Vec<WebSendCommand>>,
}

impl RunProducingFlow {
    fn new(runs: Vec<String>) -> Self {
        Self {
            runs,
            received: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl MessageFlowService for RunProducingFlow {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        self.received.lock().await.push(cmd);
        Ok(WebSendOutcome {
            queue_admission: None,
            primary_run_id: self.runs[0].clone(),
            active_run_ids: self.runs.clone(),
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
        panic!("bot event routing is not part of this suite")
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        panic!("group callbacks are not part of this suite")
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        panic!("aborts are not part of this suite")
    }

    async fn resolve_chat_abort_scope(
        &self,
        _group_id: &str,
        _legacy_run_id: &str,
    ) -> ServiceResult<Option<bcs_service_api::ChatAbortScope>> {
        panic!("abort scopes are not part of this suite")
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        panic!("task aliases are not part of this suite")
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        panic!("task dispatch is not part of this suite")
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        panic!("task completion is not part of this suite")
    }
}

/// Allows the Workbench chat authorization legs.
struct AllowWorkbenchSessions;

#[async_trait]
impl WorkbenchSessionService for AllowWorkbenchSessions {
    async fn connect(
        &self,
        _command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        Ok(WorkbenchConnectOutcome {
            group_id: GROUP_ID.into(),
            participants: vec![WorkbenchParticipantView {
                bot_uuid: "human_user-a".into(),
                role: "observer".to_string(),
                kind: ParticipantKind::Bot,
                mode: Some(ParticipantMode::Present),
                message_view_scope: MessageViewScope::Participant,
            }],
        })
    }

    async fn authorize_chat_send(
        &self,
        _command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        Ok(())
    }

    async fn authorize_chat_abort(
        &self,
        _command: WorkbenchChatAbortAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        Ok(())
    }
}

fn connect_frame(request_id: &str, view_actor_id: Option<&str>) -> String {
    let mut params = json!({
        "group_id": GROUP_ID,
        "session_id": SESSION_ID,
    });
    if let Some(view) = view_actor_id {
        params["view_actor_id"] = json!(view);
    }
    serde_json::to_string(&BcsFrame::Request(RequestFrame::new(
        request_id,
        "connect",
        Some(params),
    )))
    .expect("serialize connect")
}

fn send_frame(request_id: &str) -> String {
    serde_json::to_string(&BcsFrame::Request(RequestFrame::new(
        request_id,
        "chat.send",
        Some(json!({
            "group_id": GROUP_ID,
            "session_id": SESSION_ID,
            "message": "hello",
        })),
    )))
    .expect("serialize chat.send")
}

struct ConnectedSocket {
    addr: std::net::SocketAddr,
    #[allow(dead_code)]
    gate: Arc<dyn DeliveryAuthorizationService>,
    run_channels: Arc<RunChannelManager>,
    connections: Arc<WorkbenchConnectionRegistry>,
    delivery: Arc<WorkbenchFrontendDelivery>,
    flow: Arc<RunProducingFlow>,
    server: tokio::task::JoinHandle<()>,
}

/// Start ONE REAL axum V1 server per test and connect a REAL
/// session-bound socket through `handle_client_connection`
/// (connect → registry → chat.send → protected run registration all
/// run genuinely).
async fn start_connected(
    gate: Arc<GateAuthorization>,
    runs: Vec<String>,
    view_actor_id: Option<&str>,
) -> (
    ConnectedSocket,
    tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
) {
    start_connected_with_auth(gate, runs, view_actor_id).await
}

async fn start_connected_with_auth(
    delivery_authorization: Arc<dyn DeliveryAuthorizationService>,
    runs: Vec<String>,
    view_actor_id: Option<&str>,
) -> (
    ConnectedSocket,
    tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
) {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let flow = Arc::new(RunProducingFlow::new(runs));
    let connection_service = Arc::new(RecordingConnectionService::new(VerifyMode::Valid));
    let dispatch = Arc::new(WebDispatchState {
        message_flow: flow.clone(),
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: Arc::new(AllowWorkbenchSessions),
        interactions: Arc::new(NoopInteractionService),
        group_session_connections: Some(connection_service.clone()),
        frontend_connections: connections.clone(),
        run_channels: run_channels.clone(),
        delivery_authorization,
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind test server");
    let addr = listener.local_addr().expect("server address");
    let router = group_session_websocket_router(
        connection_service,
        dispatch.clone(),
        Arc::new(NoopWsLifecycleInstrumentationHook),
    );
    let server = tokio::spawn(async move {
        axum::serve(listener, router).await.expect("serve test app");
    });
    let url = format!("ws://{addr}/openapi/v1/collaboration/messages/ws?token=opaque");
    let (socket, response) = tokio_tungstenite::connect_async(url)
        .await
        .expect("upgrade");
    assert_eq!(response.status(), axum::http::StatusCode::SWITCHING_PROTOCOLS);

    let mut socket = socket;
    socket
        .send(Message::Text(connect_frame("connect-1", view_actor_id).into()))
        .await
        .expect("send connect");
    let response = socket
        .next()
        .await
        .expect("connect response")
        .expect("valid frame");
    let Message::Text(text) = response else {
        panic!("expected text connect response")
    };
    let frame: BcsFrame = serde_json::from_str(&text).expect("BCS response frame");
    assert!(
        matches!(frame, BcsFrame::Response(ref r) if r.ok),
        "connect must succeed: {text}"
    );

    let delivery = Arc::new(WorkbenchFrontendDelivery::new(
        connections.clone(),
        run_channels.clone(),
    ));
    (
        ConnectedSocket {
            addr,
            gate: dispatch.delivery_authorization.clone(),
            run_channels,
            connections,
            delivery,
            flow,
            server,
        },
        socket,
    )
}

async fn run_chat_send(
    socket: &mut tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
) {
    socket
        .send(Message::Text(send_frame("send-1").into()))
        .await
        .expect("send chat.send");
    let response = socket
        .next()
        .await
        .expect("chat.send response")
        .expect("valid frame");
    let Message::Text(text) = response else {
        panic!("expected text response")
    };
    let frame: BcsFrame = serde_json::from_str(&text).expect("BCS response frame");
    assert!(
        matches!(frame, BcsFrame::Response(ref r) if r.ok),
        "chat.send must succeed: {text}"
    );
}

/// The REAL typed producer entry (what bcs-message-flow reaches
/// through `FrontendDeliveryPort` for run deliveries).
/// `expected_delivered == 0` is itself a real assertion: an
/// unbound run accepts nothing at all.
async fn producer_enqueue_run_frame(
    delivery: &WorkbenchFrontendDelivery,
    run_id: &str,
    domain: MessageVisibilityDomain,
    audience: Option<MessageAudience>,
    payload: &str,
    expected_delivered: usize,
) {
    use bcs_service_api::{
        FrontendDeliveryCommand, FrontendDeliveryKind, FrontendDeliveryPort,
        FrontendDeliveryTarget,
    };
    let delivered = delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Run {
                run_id: run_id.to_string(),
            },
            event_json: payload.to_string(),
            delivery_kind: FrontendDeliveryKind::RunEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: domain,
            audience,
        })
        .await
        .expect("producer publish");
    assert_eq!(
        delivered.delivered, expected_delivered,
        "the producer path reached the expected sinks"
    );
}

async fn next_text(
    socket: &mut tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
    what: &str,
) -> String {
    let message = tokio::time::timeout(Duration::from_secs(5), socket.next())
        .await
        .unwrap_or_else(|_| panic!("timed out waiting for {what}"))
        .expect("socket open")
        .expect("valid frame");
    match message {
        Message::Text(text) => text.to_string(),
        Message::Close(_) => panic!("socket closed while waiting for {what}"),
        other => panic!("expected text for {what}, got {other:?}"),
    }
}

fn workbench_binding(
    view_actor_id: &str,
    connection_instance_id: u64,
) -> ProtectedDeliveryBinding {
    ProtectedDeliveryBinding {
        tenant: None,
        user_id: "user-a".into(),
        group_id: GROUP_ID.into(),
        session_id: Some(SESSION_ID.into()),
        view_actor_id: view_actor_id.into(),
        connection_instance_id,
    }
}

// ---------------------------------------------------------------------------
// The real chain: connect → protected registration → producer → writer
// ---------------------------------------------------------------------------

/// The REAL `view_actor_id` wire field selects the view; the token
/// never pre-selects one.
#[tokio::test]
async fn human_self_default_connect_registers_protected_run_sinks() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) = start_connected(gate, vec!["run-1".into()], None).await;

    run_chat_send(&mut socket).await;

    // The run is registered as a PROTECTED Workbench sink carrying
    // the connection's binding (real user, resource, view, THE
    // connection instance).
    let RunClientSink::Workbench { binding, .. } =
        server.run_channels.sink_of("run-1").await.expect("registered sink")
    else {
        panic!("chat.send must register a protected Workbench sink");
    };
    assert_eq!(binding.user_id, "user-a");
    assert_eq!(binding.tenant.as_deref(), Some("tenant-a"));
    assert_eq!(binding.group_id, GROUP_ID);
    assert_eq!(binding.session_id.as_deref(), Some(SESSION_ID));
    assert_eq!(binding.view_actor_id, "human_user-a");
    socket.send(Message::Close(None)).await.ok();
    server.server.abort();
}

/// 显式 view_actor_id selects the Bot view; another Human is
/// rejected on the SAME wire field (no second view field).
#[tokio::test]
async fn explicit_bot_view_connects_and_other_humans_are_rejected() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) =
        start_connected(gate, vec!["run-1".into()], Some("bot-worker")).await;

    // The connection is registered for the BOT view: per-frame
    // authorizations consult the CURRENT edge for user-a → bot-worker.
    let views = server.flow.received.lock().await;
    assert!(views.is_empty(), "no send yet");
    drop(views);
    run_chat_send(&mut socket).await;
    let RunClientSink::Workbench { binding, .. } =
        server.run_channels.sink_of("run-1").await.expect("sink")
    else {
        panic!("workbench sink expected");
    };
    assert_eq!(binding.view_actor_id, "bot-worker");

    // The other-Human view is rejected on the V1 connect itself.
    let reject_socket_url = format!(
        "ws://{}/openapi/v1/collaboration/messages/ws?token=opaque",
        server.addr
    );
    let (mut other, _resp) = tokio_tungstenite::connect_async(reject_socket_url)
        .await
        .expect("upgrade");
    other
        .send(Message::Text(connect_frame("connect-2", Some("human_other")).into()))
        .await
        .expect("connect");
    let text = next_text(&mut other, "connect rejection").await;
    assert!(
        text.contains("forbidden_view_actor"),
        "another Human actor must be rejected: {text}"
    );
    server.server.abort();
}

/// Revocation that lands while the frame sits between enqueue and
/// the final check invalidates the WHOLE binding: queue drained,
/// run + subscriptions removed, existing close protocol, and ZERO
/// further authorization reads (解绑后查询0).
#[tokio::test]
async fn revocation_between_enqueue_and_send_invalidates() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) = start_connected(gate.clone(), vec!["run-1".into()], None).await;
    run_chat_send(&mut socket).await;

    // Barriers: the writer parks INSIDE authorize_delivery.
    gate.close_with(DeliveryDecision::InvalidateBinding);
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::StateMachine,
        Some(MessageAudience::FullOnly),
        r#"{"hidden":true}"#,
        1,
    )
    .await;
    gate.wait_until_consulted(1).await;
    // The revocation decision is already scripted; let the check
    // complete now.
    gate.open();

    let close = next_text(&mut socket, "close notification").await;
    assert!(
        close.contains(r#""reason":"view_scope_changed""#),
        "the existing close protocol notifies the client: {close}"
    );
    // The binding is gone BEFORE any payload reached the socket...
    let dangling = no_payload_after(Duration::from_millis(150), &mut socket).await;
    assert!(
        dangling,
        "no protected payload may leak after invalidation"
    );
    assert!(
        !server.run_channels.is_registered("run-1").await,
        "the invalidated run is unregistered"
    );
    assert_eq!(
        server.connections.connection_count(SESSION_ID).await,
        0,
        "the subscription is removed"
    );
    // ...and 解绑后查询0: the producer can no longer reach any
    // sink — no further authorization consultation happens.
    let consulted = gate.consulted().len();
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::Chat,
        None,
        "late",
        0,
    )
    .await;
    assert!(
        no_payload_after(Duration::from_millis(150), &mut socket).await,
        "unbound sinks deliver nothing"
    );
    assert_eq!(
        gate.consulted().len(),
        consulted,
        "no authorization queries after unbinding"
    );
    server.server.abort();
}

/// SkipMessage drops the one invisible frame ONLY: the connection,
/// its other frames and its subscriptions stay intact (单帧不可见不
/// 关连接).
#[tokio::test]
async fn skip_message_drops_one_frame_and_keeps_the_binding() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) = start_connected(gate.clone(), vec!["run-1".into()], None).await;
    run_chat_send(&mut socket).await;

    gate.push(DeliveryDecision::SkipMessage);
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::StateMachine,
        Some(MessageAudience::FullOnly),
        r#"{"invisible":true}"#,
        1,
    )
    .await;
    gate.wait_until_consulted(1).await;

    assert!(
        tokio::time::timeout(Duration::from_millis(150), socket.next()).await.is_err(),
        "the skipped frame never reaches the socket"
    );
    assert!(
        server.run_channels.is_registered("run-1").await,
        "a scope-hide must not unbind the run"
    );

    // The very next frame delivers normally.
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::Chat,
        None,
        r#"{"visible":true}"#,
        1,
    )
    .await;
    let delivered = next_text(&mut socket, "public frame").await;
    assert!(delivered.contains(r#""visible":true"#));
    assert!(server.connections.connection_count(SESSION_ID).await >= 1);
    server.server.abort();
}

/// 检查通过至 send 之间撤权的帧允许在途: frame 1's decision was
/// made BEFORE the scripted revocation, so it IS delivered while
/// frame 2 (checked after) revokes the binding.
#[tokio::test]
async fn frame_checked_before_revocation_is_allowed_in_flight() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) = start_connected(gate.clone(), vec!["run-1".into()], None).await;
    run_chat_send(&mut socket).await;

    // Frame 1 parks inside its check; the revocation is scripted
    // while it is in flight; its own decision (Deliver) was taken
    // BEFORE the revocation, so it completes.
    gate.close_with(DeliveryDecision::Deliver);
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::Chat,
        None,
        r#"{"flight":1}"#,
        1,
    )
    .await;
    gate.wait_until_consulted(1).await;
    gate.close_with(DeliveryDecision::InvalidateBinding);
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::Chat,
        None,
        r#"{"flight":2}"#,
        1,
    )
    .await;
    gate.open();

    let first = next_text(&mut socket, "in-flight frame").await;
    assert!(first.contains(r#""flight":1"#));
    let close = next_text(&mut socket, "close notification").await;
    assert!(close.contains(r#""reason":"view_scope_changed""#));
    server.server.abort();
}

/// ConnectionControl frames NEVER pass a delivery authorization:
/// the public socket carries only connection-control messages.
#[tokio::test]
async fn connection_control_frames_flow_without_authorization() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::InvalidateBinding);
    let (server, mut socket) =
        start_connected(gate.clone(), vec!["run-1".into()], None).await;
    run_chat_send(&mut socket).await;

    let RunClientSink::Workbench { tx, .. } =
        server.run_channels.sink_of("run-1").await.expect("sink")
    else {
        panic!("workbench sink expected");
    };
    tx.send(OutboundFrame::ConnectionControl {
        payload: json!({"type": "event", "event": "close"}).to_string(),
    })
    .await
    .expect("control frame");

    let text = next_text(&mut socket, "connection control frame").await;
    assert!(text.contains(r#""event":"close""#));
    // A protected frame behind the same closed gate would have
    // invalidated the connection; the control frame did NOT
    // consult the authorization at all (the gate records every
    // consult).
    assert_eq!(
        gate.consulted().len(),
        0,
        "connection control frames pass no delivery authorization"
    );
    server.server.abort();
}

/// The internal HTTP Run String port is untouched: register_internal
/// keeps the raw-String consumer, and a Workbench registration does
/// NOT inherit it.
#[tokio::test]
async fn internal_http_string_port_behavior_unchanged() {
    let manager = RunChannelManager::new();
    let (internal_tx, mut internal_rx) = mpsc::channel(8);
    manager
        .register_internal(
            "internal-run".into(),
            "session-internal".into(),
            internal_tx,
            Some("http-chat-async".into()),
            None,
        )
        .await;

    // Internal sink: raw String port, unfiltered visibility
    // semantics unchanged.
    assert!(
        manager
            .send_visible_event(
                "internal-run",
                "raw-event".into(),
                MessageVisibilityDomain::StateMachine,
                Some(&MessageAudience::FullOnly),
            )
            .await,
        "the internal port accepts events unchanged"
    );
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(1), internal_rx.recv())
            .await
            .expect("internal consumer receives")
            .as_deref(),
        Some("raw-event")
    );

    // The Workbench protected sink cannot be reached with a raw
    // String plaintext bypass: a workbench registration receives
    // Typed protected frames only.
    let (frame_tx, mut frame_rx) = mpsc::channel(8);
    manager
        .register_workbench(
            "wb-run".into(),
            "session-internal".into(),
            frame_tx,
            workbench_binding("human_user-a", 1),
            Some("workbench-ws".into()),
            None,
            None,
        )
        .await;
    assert!(matches!(
        manager.sink_of("wb-run").await,
        Some(RunClientSink::Workbench { .. })
    ));
    assert!(matches!(
        manager.sink_of("internal-run").await,
        Some(RunClientSink::Internal { .. })
    ));
    assert!(
        manager.send_event("wb-run", "bypass".into()).await,
        "the enqueue succeeds into the protected queue"
    );
    match tokio::time::timeout(Duration::from_secs(1), frame_rx.recv())
        .await
        .expect("protected queue receives")
    {
        Some(OutboundFrame::Protected { payload, visibility, .. }) => {
            // The enqueue carries the Trusted producer metadata; no
            // raw String channel is exposed to the public writer.
            assert_eq!(payload, "bypass");
            assert_eq!(visibility.domain, MessageVisibilityDomain::Chat);
        }
        other => panic!("workbench sink must carry protected frames, got {other:?}"),
    }
    let _ = SharedVisibility {
        domain: MessageVisibilityDomain::Chat,
        audience: None,
    };
}

/// alias / session fallback 保留 sink 类型与连接实例: a run
/// alias and the session fallback resolve to the SAME
/// registered sink (and its binding), never to another Human's
/// sink under the same run/session string.
#[tokio::test]
async fn alias_and_session_fallback_preserve_the_bound_sink() {
    let manager = RunChannelManager::new();
    let (tx_a, mut rx_a) = mpsc::channel(8);
    let (tx_b, mut rx_b) = mpsc::channel(8);
    let binding_a = workbench_binding("human_user-a", 101);
    let binding_b = ProtectedDeliveryBinding {
        view_actor_id: "human_user-b".into(),
        user_id: "user-b".into(),
        ..workbench_binding("human_user-b", 202)
    };
    manager
        .register_workbench(
            "outer-run".into(),
            "shared-session".into(),
            tx_a,
            binding_a.clone(),
            Some("workbench-ws".into()),
            None,
            None,
        )
        .await;
    manager
        .register_workbench(
            "other-run".into(),
            "unrelated".into(),
            tx_b,
            binding_b.clone(),
            Some("workbench-ws".into()),
            None,
            None,
        )
        .await;
    assert!(
        manager
            .register_alias("derived-run".into(), "outer-run".into())
            .await
    );

    // The alias delivers to A's queue with A's binding.
    assert!(
        manager
            .send_visible_event(
                "derived-run",
                "via-alias".into(),
                MessageVisibilityDomain::Chat,
                None,
            )
            .await
    );
    match tokio::time::timeout(Duration::from_secs(1), rx_a.recv()).await {
        Ok(Some(OutboundFrame::Protected { binding, payload, .. })) => {
            assert_eq!(payload, "via-alias");
            assert_eq!(binding, binding_a);
        }
        other => panic!("alias must reuse the bound sink: {other:?}"),
    }
    assert!(
        tokio::time::timeout(Duration::from_millis(100), rx_b.recv())
            .await
            .is_err(),
        "B's sink must not receive A's run"
    );

    // The session fallback resolves the LAST run registered for
    // that session — the OTHER connection's own sink with ITS
    // binding, never a silent re-binding of the requester.
    assert!(
        manager
            .send_visible_event_by_session(
                "shared-session",
                "via-fallback".into(),
                MessageVisibilityDomain::Chat,
                None,
            )
            .await
    );
    match tokio::time::timeout(Duration::from_secs(1), rx_a.recv()).await {
        Ok(Some(OutboundFrame::Protected { binding, payload, .. })) => {
            assert_eq!(payload, "via-fallback");
            assert_eq!(binding, binding_a, "fallback keeps the original connection instance");
        }
        other => panic!("fallback must keep the sink: {other:?}"),
    }
    assert!(
        tokio::time::timeout(Duration::from_millis(100), rx_b.recv())
            .await
            .is_err(),
        "an unrelated connection never receives the fallback frame"
    );
}

/// A replaced connection instance cannot inherit the prior
/// instance's frames: re-registering a run rebinds BOTH the sink
/// and the connection instance.
#[tokio::test]
async fn connection_instance_replacement_rebinds_the_sink() {
    let manager = RunChannelManager::new();
    let (tx_old, mut rx_old) = mpsc::channel(8);
    let (tx_new, mut rx_new) = mpsc::channel(8);
    manager
        .register_workbench(
            "dup-run".into(),
            "session-x".into(),
            tx_old,
            workbench_binding("human_user-a", 1),
            None,
            None,
            None,
        )
        .await;
    let new_binding = ProtectedDeliveryBinding {
        view_actor_id: "human_user-b".into(),
        ..workbench_binding("human_user-b", 2)
    };
    manager
        .register_workbench(
            "dup-run".into(),
            "session-x".into(),
            tx_new,
            new_binding.clone(),
            None,
            None,
            None,
        )
        .await;

    assert!(
        manager
            .send_visible_event("dup-run", "fresh".into(), MessageVisibilityDomain::Chat, None)
            .await
    );
    match tokio::time::timeout(Duration::from_secs(1), rx_new.recv()).await {
        Ok(Some(OutboundFrame::Protected { binding, payload, .. })) => {
            assert_eq!(payload, "fresh");
            assert_eq!(binding, new_binding);
        }
        other => panic!("the replacement sink receives: {other:?}"),
    }
    match tokio::time::timeout(Duration::from_millis(100), rx_old.recv())
        .await
        .ok()
        .flatten()
    {
        Some(frame) => panic!("stale queue received {frame:?}"),
        None => {}
    }
}

/// A slow (full) bounded queue BACKPRESSURES the producer instead of
/// closing it or dropping protected frames.
#[tokio::test]
async fn slow_queue_is_backpressured_not_closed() {
    let manager = Arc::new(RunChannelManager::new());
    let (tx, mut rx) = mpsc::channel(1);
    manager
        .register_workbench(
            "slow-run".into(),
            "session-slow".into(),
            tx,
            workbench_binding("human_user-a", 3),
            None,
            None,
            None,
        )
        .await;

    // Four frames into a capacity-1 queue: the producer awaits.
    let manager_for_producer = manager.clone();
    let producer = tokio::spawn(async move {
        for index in 0..4 {
            let sent = manager_for_producer
                .send_visible_event(
                    "slow-run",
                    format!("frame-{index}"),
                    MessageVisibilityDomain::Chat,
                    None,
                )
                .await;
            assert!(sent);
        }
    });
    for index in 0..4 {
        match tokio::time::timeout(Duration::from_secs(2), rx.recv()).await {
            Ok(Some(OutboundFrame::Protected { payload, .. })) => {
                assert_eq!(payload, format!("frame-{index}"));
            }
            other => panic!("slow queue must keep every frame: {other:?}"),
        }
    }
    producer.await.expect("producer completes");
}

/// Deterministic no-leak wait: the connection closed or no
/// protected text arrived within the window.
async fn no_payload_after(
    window: Duration,
    socket: &mut tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
) -> bool {
    match tokio::time::timeout(window, socket.next()).await {
        Err(_) => true,
        Ok(None) => true,
        Ok(Some(Ok(Message::Close(_)))) => true,
        Ok(Some(Ok(Message::Text(_)))) => false,
        Ok(Some(_)) => true,
    }
}



/// 没 binding/runtregistration refused: a public writer can
/// never host an unbound run — the registration refuses
/// instead of falling back to a public or Internal sender.
#[tokio::test]
async fn send_without_a_protected_queue_refuses_the_run_registration() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let flow = Arc::new(RunProducingFlow::new(vec!["run-1".to_string()]));
    let dispatch = Arc::new(WebDispatchState {
        message_flow: flow.clone(),
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: Arc::new(AllowWorkbenchSessions),
        interactions: Arc::new(NoopInteractionService),
        group_session_connections: None,
        frontend_connections: connections.clone(),
        run_channels: run_channels.clone(),
        delivery_authorization: gate.clone(),
    });

    // A connection WITHOUT an open protected queue
    // (the final writer only opens it in handle_client_connection).
    let mut connection_state = WebClientConnectionState::default();
    let (request_tx, _rx) = mpsc::channel(8);
    let chat_send = send_frame("send-1");
    bcs_ws::web::dispatch_client_frame(
        &dispatch,
        &chat_send,
        &request_tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_user-a".to_string()),
        },
    )
    .await
    .expect("chat.send succeeds");

    assert!(
        !run_channels.is_registered("run-1").await,
        "a run must NOT register without a protected delivery binding"
    );
    assert!(
        matches!(
            run_channels.sink_of("run-1").await,
            None
        ),
        "no unbounded public/ Internal fallback exists"
    );
}

// ---------------------------------------------------------------------------
// Critical#1 fix: the FANOUT (registry broadcast) path is a
// protected-data writer — every (connection, frame) pair is
// re-authorized under the same contract
// ---------------------------------------------------------------------------

/// Fanout that arrives with a revoked binding invalidates the
/// WHOLE connection: existing close protocol, subscription
/// removed, and after unbinding ZERO further authorization
/// queries (no manual frame injection — the real
/// FrontendDeliveryPort path is used).
#[tokio::test]
async fn fanout_revocation_between_enqueue_and_send_invalidates() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) = start_connected(gate.clone(), vec!["run-1".into()], None).await;

    // Barriers: park the final writer inside the fanout check.
    gate.close_with(DeliveryDecision::InvalidateBinding);
    let delivered = server
        .delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.into(),
            },
            event_json: r#"{"type":"event","event":"chat"}"#.into(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::Public),
        })
        .await
        .expect("publish");
    assert_eq!(
        delivered.delivered, 1,
        "the subscriber's protected queue accepted the fanout frame"
    );
    gate.wait_until_consulted(1).await;
    gate.open();

    let close = next_text(&mut socket, "close notification").await;
    assert!(
        close.contains(r#""reason":"view_scope_changed""#),
        "the existing close protocol notifies the client: {close}"
    );
    assert_eq!(
        server.connections.connection_count(SESSION_ID).await,
        0,
        "the subscription is removed"
    );
    // 解绑后查询0: subsequent fanout reaches no subscriber and
    // consults nothing.
    let consulted = gate.consulted().len();
    let delivered = server
        .delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.into(),
            },
            event_json: "late".into(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::Public),
        })
        .await
        .expect("publish");
    assert_eq!(
        delivered.delivered, 0,
        "an unbound connection receives no fanout"
    );
    assert_eq!(
        gate.consulted().len(),
        consulted,
        "no authorization queries after unbinding"
    );
    server.server.abort();
}

/// 单帧不可见不关连接: a scope-hidden fanout frame is dropped
/// for the current view only; Public / Directed-to-self and the
/// very next frame still deliver.
#[tokio::test]
async fn fanout_scope_tightening_skips_without_unbinding() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) = start_connected(gate.clone(), vec!["run-1".into()], None).await;

    gate.push(DeliveryDecision::SkipMessage);
    server
        .delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.into(),
            },
            event_json: r#"{"hidden":true}"#.into(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::FullOnly),
        })
        .await
        .expect("publish");
    gate.wait_until_consulted(1).await;

    assert!(
        tokio::time::timeout(Duration::from_millis(150), socket.next())
            .await
            .is_err(),
        "the hidden fanout frame never reaches the socket"
    );
    assert_eq!(
        server.connections.connection_count(SESSION_ID).await,
        1,
        "one invisible fanout frame must not unbind"
    );

    // Public fanout delivers and the connection stays.
    server
        .delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.into(),
            },
            event_json: r#"{"visible":true}"#.into(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::Public),
        })
        .await
        .expect("publish");
    let delivered_frame = next_text(&mut socket, "public fanout").await;
    assert!(delivered_frame.contains(r#""visible":true"#));
    assert_eq!(server.connections.connection_count(SESSION_ID).await, 1);
    server.server.abort();
}

/// A Bot-view connection's fanout is AUTHORIZED per frame too
/// (the unfiltered human_view=None hole is closed by
/// construction): the consulted binding IS the Bot view.
#[tokio::test]
async fn bot_view_fanout_is_authorized() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) =
        start_connected(gate.clone(), vec!["run-1".into()], Some("bot-worker")).await;

    server
        .delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.into(),
            },
            event_json: r#"{"fullonly":true}"#.into(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::FullOnly),
        })
        .await
        .expect("publish");
    gate.wait_until_consulted(1).await;

    let consulted = gate.consulted();
    assert_eq!(
        consulted.len(),
        1,
        "Bot-view fanout is NOT a free pass: it is re-authorized"
    );
    assert_eq!(consulted[0].view_actor_id, "bot-worker");
    assert_eq!(consulted[0].user_id, "user-a");

    // The authorized Bot-view frame flows to the socket (Full
    // scope sees FullOnly).
    let delivered_frame = next_text(&mut socket, "bot-view fanout frame").await;
    assert!(delivered_frame.contains(r#""fullonly":true"#));

    // And a revoked edge invalidates on the NEXT frame.
    gate.close_with(DeliveryDecision::InvalidateBinding);
    server
        .delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.into(),
            },
            event_json: "second".into(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::Public),
        })
        .await
        .expect("publish");
    gate.open();
    let close = next_text(&mut socket, "close notification").await;
    assert!(close.contains(r#""reason":"view_scope_changed""#));
    server.server.abort();
}

// ---------------------------------------------------------------------------
// GPT-review batch B: writer-queue backpressure isolation
// ---------------------------------------------------------------------------

/// Per-connection authorization barrier: only the `parked_view`
/// blocks INSIDE the final check (its writer stops consuming
/// its protected queue); every other view is authorized
/// immediately. A watch gate like `GateAuthorization` — no
/// sleeps.
struct ParkedViewAuthorization {
    parked_view: &'static str,
    open_tx: watch::Sender<bool>,
    open_rx: watch::Receiver<bool>,
    consulted: Mutex<Vec<ProtectedDeliveryBinding>>,
}

impl ParkedViewAuthorization {
    fn new(parked_view: &'static str) -> Arc<Self> {
        let (open_tx, open_rx) = watch::channel(true);
        Arc::new(Self {
            parked_view,
            open_tx,
            open_rx,
            consulted: Mutex::new(Vec::new()),
        })
    }

    /// Park the parked view's NEXT final check.
    fn close(&self) {
        let _ = self.open_tx.send(false);
    }

    fn open(&self) {
        let _ = self.open_tx.send(true);
    }

    /// Barrier: the parked view's writer has DEQUEUED its frame
    /// and entered the final check.
    async fn wait_until_parked(&self) {
        let mut waited = 0;
        while !self
            .consulted
            .lock()
            .await
            .iter()
            .any(|binding| binding.view_actor_id == self.parked_view)
        {
            assert!(
                waited < 2000,
                "the parked view never reached its final check"
            );
            tokio::time::sleep(Duration::from_millis(2)).await;
            waited += 1;
        }
    }
}

#[async_trait]
impl DeliveryAuthorizationService for ParkedViewAuthorization {
    async fn authorize_delivery(
        &self,
        binding: &ProtectedDeliveryBinding,
        _message: &bcs_service_api::application::v1::DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError> {
        self.consulted
            .lock()
            .await
            .push(binding.clone());
        if binding.view_actor_id == self.parked_view {
            let mut open_rx = self.open_rx.clone();
            // Race-free barrier: wait while the gate is closed.
            while !*open_rx.borrow_and_update() {
                open_rx
                    .changed()
                    .await
                    .expect("parked-view gate stays alive");
            }
        }
        Ok(DeliveryDecision::Deliver)
    }
}

/// One session-target fanout through the REAL
/// `FrontendDeliveryPort`: the number of subscriber queues that
/// accepted the frame.
async fn publish_session_fanout(
    delivery: &WorkbenchFrontendDelivery,
    payload: &str,
) -> usize {
    delivery
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Session {
                session_id: SESSION_ID.to_string(),
            },
            event_json: payload.to_string(),
            delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
            run_fallback: None,
            exclude_conn_id: None,
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(MessageAudience::Public),
        })
        .await
        .expect("producer publish")
        .delivered
}

/// Bounded condition wait (the `wait_until_consulted` poll
/// pattern): 2ms polls, hard fail after the bound.
async fn wait_until_satisfied<F, Fut>(what: &str, mut condition: F)
where
    F: FnMut() -> Fut,
    Fut: Future<Output = bool>,
{
    let mut waited = 0usize;
    while !condition().await {
        assert!(waited < 5000, "condition never held: {what}");
        tokio::time::sleep(Duration::from_millis(2)).await;
        waited += 1;
    }
}

async fn wait_for_flow_commands(flow: &RunProducingFlow, expected: usize) {
    let mut waited = 0;
    while flow.received.lock().await.len() < expected {
        assert!(
            waited < 5000,
            "message flow never observed {expected} commands"
        );
        tokio::time::sleep(Duration::from_millis(2)).await;
        waited += 1;
    }
}

/// A writer-queue-saturated connection MUST NOT deadlock its own
/// revocation: the close/cancel path runs on the writer task
/// itself, so an awaited re-enqueue into the writer's OWN full
/// control queue would block the writer on its own consumption.
/// The close notification is therefore best-effort: when the
/// queue is provably full the notification is dropped, and the
/// unbind + socket close proceed structurally.
#[tokio::test]
async fn revocation_with_saturated_writer_queue_does_not_deadlock() {
    let gate = GateAuthorization::open_with_default(DeliveryDecision::Deliver);
    let (server, mut socket) =
        start_connected(gate.clone(), vec!["run-1".into()], None).await;
    run_chat_send(&mut socket).await;

    // Park the final writer INSIDE its protected-frame check.
    gate.close_with(DeliveryDecision::InvalidateBinding);
    producer_enqueue_run_frame(
        &server.delivery,
        "run-1",
        MessageVisibilityDomain::Chat,
        None,
        "poison",
        1,
    )
    .await;
    gate.wait_until_consulted(1).await;

    // Saturate the writer's control queue while it is parked:
    // each chat.send enqueues exactly ONE response the parked
    // writer cannot consume. Exactly PROTECTED_FRAME_QUEUE_SIZE
    // sequential responses fill the queue; the next one proves
    // saturation through the flow barrier (its dispatch ran —
    // recorded BEFORE its response enqueue — so the queue is full
    // at this exact point, no sleeps, no fixed waits).
    for index in 0..PROTECTED_FRAME_QUEUE_SIZE {
        socket
            .send(Message::Text(
                send_frame(&format!("saturate-{index}")).into(),
            ))
            .await
            .expect("saturating chat.send");
    }
    socket
        .send(Message::Text(send_frame("saturate-last").into()))
        .await
        .expect("final saturating chat.send");
    wait_for_flow_commands(&server.flow, PROTECTED_FRAME_QUEUE_SIZE + 2).await;

    // The revocation decision lands against the FULL queue.
    gate.open();

    // No deadlock: the unbinding completes in bounded time even
    // though the close notification could not be enqueued.
    wait_until_satisfied("run unregistered", || async {
        !server.run_channels.is_registered("run-1").await
    })
    .await;
    wait_until_satisfied("subscription removed", || async {
        server.connections.connection_count(SESSION_ID).await == 0
    })
    .await;

    // The socket still terminates, structurally: the queued
    // traffic drains, and the close notification — which could
    // not be enqueued — never appears on the socket.
    loop {
        match tokio::time::timeout(Duration::from_secs(10), socket.next()).await {
            Err(_) => {
                panic!("the revoked connection terminates (no writer-queue deadlock)")
            }
            Ok(None) => break,
            Ok(Some(Err(_))) => break,
            Ok(Some(Ok(Message::Close(_)))) => break,
            Ok(Some(Ok(Message::Text(text)))) => {
                assert!(
                    !text.contains(r#""reason":"view_scope_changed""#),
                    "the full queue could not take the close notification; it must not appear: {text}"
                );
            }
            Ok(Some(Ok(_))) => {}
        }
    }
    server.server.abort();
}

/// A saturated slow fanout subscriber must not block the OTHER
/// subscribers of the same session NOR the upstream caller: the
/// registry fanout never awaits a subscriber's queue. The slow
/// subscriber's saturated frame is dropped (per-frame
/// slow-consumer policy — exactly the legacy lane's behavior);
/// the healthy subscriber keeps full throughput, and the slow
/// subscriber keeps its binding (not closed).
#[tokio::test]
async fn saturated_fanout_subscriber_does_not_block_the_healthy_one() {
    // The default Human view's writer parks inside ITS per-frame
    // authorization; every other view authorizes immediately.
    let auth = ParkedViewAuthorization::new("human_user-a");
    let (server, mut socket_slow) =
        start_connected_with_auth(auth.clone(), vec![], None).await;

    // A second REAL socket subscribes to the SAME session.
    let other_url = format!(
        "ws://{}/openapi/v1/collaboration/messages/ws?token=opaque",
        server.addr
    );
    let (mut socket_fast, _response) =
        tokio_tungstenite::connect_async(other_url)
            .await
            .expect("second upgrade");
    socket_fast
        .send(Message::Text(
            connect_frame("connect-2", Some("bot-worker")).into(),
        ))
        .await
        .expect("connect second subscriber");
    let response = socket_fast
        .next()
        .await
        .expect("second connect response")
        .expect("valid frame");
    let Message::Text(text) = response else {
        panic!("expected text connect response")
    };
    let frame: BcsFrame = serde_json::from_str(&text).expect("BCS response frame");
    assert!(
        matches!(frame, BcsFrame::Response(ref r) if r.ok),
        "second connect must succeed: {text}"
    );
    assert_eq!(
        server.connections.connection_count(SESSION_ID).await,
        2,
        "two subscribers of the same session"
    );

    // Park the slow subscriber's writer inside its final check.
    // Its writer has TAKEN the frame out of its queue to park, so
    // the queue below refills from exactly zero.
    auth.close();
    publish_session_fanout(&server.delivery, r#"{"park":0}"#).await;
    auth.wait_until_parked().await;

    // Fill the parked subscriber's bounded queue exactly.
    for index in 0..PROTECTED_FRAME_QUEUE_SIZE {
        let delivered = publish_session_fanout(
            &server.delivery,
            &format!(r#"{{"burst":"{index}"}}"#),
        )
        .await;
        assert_eq!(
            delivered, 2,
            "both subscribers accept frames until saturation"
        );
    }

    // Drain the healthy subscriber fully (its queue is empty again
    // only when the writer pushed everything to the socket): the
    // slow subscriber's stall cost it NOTHING in throughput.
    let last_burst = format!(r#""burst":"{}""#, PROTECTED_FRAME_QUEUE_SIZE - 1);
    let mut saw_last = false;
    while !saw_last {
        let text = next_text(&mut socket_fast, "healthy fanout").await;
        saw_last = text.contains(&last_burst);
    }

    // The saturated fanout: the slow subscriber's queue is FULL
    // and its writer parked — the fanout must return WITHOUT
    // waiting on it (pre-fix it awaited the full queue forever).
    let delivered = tokio::time::timeout(
        Duration::from_secs(5),
        publish_session_fanout(&server.delivery, r#"{"burst":"final"}"#),
    )
    .await
    .expect("the fanout must NEVER hang on a saturated subscriber");
    assert_eq!(
        delivered, 1,
        "only the healthy subscriber's queue accepted the frame"
    );

    let final_text = next_text(&mut socket_fast, "healthy fanout").await;
    assert!(
        final_text.contains(r#""burst":"final""#),
        "the healthy subscriber receives the saturated fanout: {final_text}"
    );

    // The slow subscriber's ONE saturated frame was dropped, not
    // delayed or closed over: after release it receives its
    // queued frames and never the dropped one, and the binding
    // survives (frame-level slow-consumer policy).
    auth.open();
    let mut saw_last = false;
    while !saw_last {
        let text = next_text(&mut socket_slow, "parked subscriber resumes").await;
        assert!(
            !text.contains(r#""burst":"final""#),
            "the saturated fanout frame was dropped for the slow subscriber: {text}"
        );
        saw_last = text.contains(&last_burst);
    }
    assert_eq!(
        server.connections.connection_count(SESSION_ID).await,
        2,
        "a saturated slow subscriber is not closed"
    );
    assert!(
        no_payload_after(Duration::from_millis(150), &mut socket_slow).await,
        "the dropped fanout frame never leaks late"
    );
    server.server.abort();
}
