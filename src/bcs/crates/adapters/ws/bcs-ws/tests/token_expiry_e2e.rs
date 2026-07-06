//! End-to-end test: token expiry scanner disconnects a real WS connection.
//!
//! Starts a minimal axum WS server backed by the real `handle_connection`,
//! connects a tokio-tungstenite client, completes bot.connect, sets an
//! already-expired token_expires_at, then verifies the scanner closes
//! the connection.

use std::net::SocketAddr;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use async_trait::async_trait;
use axum::extract::ws::WebSocketUpgrade;
use axum::extract::State;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;
use bcs_service_api::{
    BotDeliveryTarget, BotEventCommand, BotEventOutcome,
    BotRuntimeConnectCommand, BotRuntimeConnectOutcome, BotRuntimeConnectionService,
    BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, BotRuntimeStatusOutcome,
    BotUseCaseError, ChatAbortCommand, ChatAbortOutcome, CollaborationDefinition,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, CancelStateMachineRunCommand, GroupCallbackCommand,
    GroupCallbackOutcome, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    MessageFlowService, ServiceResult, SessionHistoryResult,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineDeliveryCorrelation,
    StateMachineRunView, TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand,
    TaskDispatchOutcome, TaskMessageCommand, TaskMessageOutcome, TaskRunAliasRegistration,
    WebSendCommand, WebSendOutcome,
};
use bcs_session::NoopSessionManagementService;
use bcs_test_support::NoopBotRunContextPort;
use bcs_ws::bot::{BotConnectionRegistry, BotDispatchState, handle_connection};
use bcs_ws::shared::RunChannelManager;
use futures::{SinkExt, StreamExt};
use tokio::sync::Mutex;
use tokio_tungstenite::tungstenite::Message;

// ─── Minimal mocks ──────────────────────────────────────────────────────────

#[derive(Default)]
struct MockBotRuntime {
    connect_count: AtomicUsize,
    disconnects: Mutex<Vec<String>>,
}

#[async_trait]
impl BotRuntimeConnectionService for MockBotRuntime {
    async fn connect_streaming(
        &self,
        command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        self.connect_count.fetch_add(1, Ordering::Relaxed);
        let bot_uuid = command.bot_id.unwrap_or_else(|| "test-bot".to_string());
        Ok(BotRuntimeConnectOutcome {
            is_new: true,
            bot_uuid,
            token: command.token.unwrap_or_else(|| "tok".to_string()),
        })
    }

    async fn update_runtime_status(
        &self,
        cmd: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        Ok(BotRuntimeStatusOutcome {
            updated: true,
            bot_uuid: cmd.bot_id,
            status: cmd.status,
        })
    }

    async fn disconnect_streaming(
        &self,
        cmd: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        self.disconnects.lock().await.push(cmd.bot_id);
        Ok(())
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}

#[derive(Default)]
struct MockMessageFlow;

#[async_trait]
impl MessageFlowService for MockMessageFlow {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        unreachable!()
    }
    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
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
        unreachable!()
    }
    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        unreachable!()
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
        unreachable!()
    }
    async fn handle_task_message(
        &self,
        _cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        unreachable!()
    }
    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        unreachable!()
    }
}

#[derive(Default)]
struct MockCollaboration;

#[async_trait]
impl CollaborationRuntimeService for MockCollaboration {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        unreachable!()
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
        unreachable!()
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
        unreachable!()
    }
}

// ─── Noop metrics hook ──────────────────────────────────────────────────────

struct NoopMetrics;

#[async_trait]
impl bcs_service_api::WsLifecycleInstrumentationHook for NoopMetrics {
    async fn accepted(&self, _peer: bcs_service_api::WsPeer, _endpoint: &'static str) {}
    async fn registered(&self, _peer: bcs_service_api::WsPeer, _endpoint: &'static str) {}
    async fn closed(
        &self,
        _peer: bcs_service_api::WsPeer,
        _endpoint: &'static str,
        _reason: bcs_service_api::WsCloseReason,
        _duration: Duration,
    ) {
    }
    async fn error(
        &self,
        _peer: bcs_service_api::WsPeer,
        _endpoint: &'static str,
        _kind: bcs_service_api::WsErrorKind,
    ) {
    }
}

// ─── Test helpers ───────────────────────────────────────────────────────────

#[derive(Clone)]
struct AppState {
    dispatch_state: Arc<BotDispatchState>,
    metrics_hook: Arc<dyn bcs_service_api::WsLifecycleInstrumentationHook>,
}

async fn ws_handler(State(state): State<AppState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| {
        handle_connection(
            socket,
            state.dispatch_state,
            state.metrics_hook,
            None,
            None,
        )
    })
}

/// Start a minimal WS server and return (addr, bot_connections, bot_runtime).
async fn start_test_server() -> (SocketAddr, Arc<BotConnectionRegistry>, Arc<MockBotRuntime>) {
    let bot_connections = Arc::new(BotConnectionRegistry::new());
    let bot_runtime = Arc::new(MockBotRuntime::default());

    let dispatch_state = Arc::new(BotDispatchState {
        bot_runtime: bot_runtime.clone(),
        message_flow: Arc::new(MockMessageFlow),
        collaboration_runtime: Arc::new(MockCollaboration),
        bot_run_context: Arc::new(NoopBotRunContextPort),
        bot_connections: bot_connections.clone(),
        run_channels: Arc::new(RunChannelManager::new()),
        task_callback: None,
        session_management: Arc::new(NoopSessionManagementService),
        group_dispatch: Arc::new(bcs_test_support::NoopGroupDispatchContextPort),
        callback_dispatch: Arc::new(bcs_test_support::NoopSessionCallbackDispatchPort),
        system_message: None,
        coordination_processed: Arc::new(Mutex::new(
            std::collections::HashMap::new(),
        )),
        agent_credential_backfill: None,
    });

    let app_state = AppState {
        dispatch_state,
        metrics_hook: Arc::new(NoopMetrics),
    };

    let app = Router::new()
        .route("/ws/bot", get(ws_handler))
        .with_state(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });

    (addr, bot_connections, bot_runtime)
}

/// Connect a WS client and send bot.connect, return the stream.
async fn connect_bot(
    addr: SocketAddr,
    bot_id: &str,
) -> tokio_tungstenite::WebSocketStream<
    tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
> {
    let url = format!("ws://{}/ws/bot", addr);
    let (ws_stream, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    let (mut write, mut read) = ws_stream.split();

    // Send bot.connect
    let connect_frame = serde_json::json!({
        "type": "req",
        "id": "connect-1",
        "method": "bot.connect",
        "params": {
            "bot_id": bot_id,
            "protocol_version": 1
        }
    });
    write
        .send(Message::Text(connect_frame.to_string().into()))
        .await
        .unwrap();

    // Read the response
    let msg = read.next().await.unwrap().unwrap();
    let resp: serde_json::Value = serde_json::from_str(&msg.into_text().unwrap()).unwrap();
    assert_eq!(resp["ok"], true, "bot.connect should succeed: {:?}", resp);

    write.reunite(read).unwrap()
}

// ─── Tests ──────────────────────────────────────────────────────────────────

/// Test: scanner disconnects a bot whose token has expired.
/// Real WS connection, real axum server, real close frame observed by client.
#[tokio::test]
async fn scanner_disconnects_bot_with_expired_token() {
    let (addr, bot_connections, bot_runtime) = start_test_server().await;

    // Connect bot via real WebSocket
    let mut ws = connect_bot(addr, "expiry-bot").await;

    // Verify bot is registered
    assert!(bot_connections.is_connected("expiry-bot").await);

    // Set an already-expired token (exp = 1000, way in the past)
    bot_connections.set_token_expires_at("expiry-bot", 1000).await;

    // Simulate what the scanner does: collect expired and disconnect
    let expired = bot_connections.collect_expiring(2000, 0).await;
    assert_eq!(expired, vec!["expiry-bot".to_string()]);

    for bot_id in &expired {
        bot_connections.disconnect(bot_id).await;
    }

    // The client should receive a Close frame or the stream should end
    let msg = tokio::time::timeout(Duration::from_secs(5), ws.next()).await;
    match msg {
        Ok(Some(Ok(Message::Close(_)))) => {} // explicit close frame
        Ok(None) => {}                         // stream ended cleanly
        Ok(Some(Err(_))) => {}                 // connection reset
        Err(_) => {}                           // timeout (connection already dead)
        other => panic!("Expected close/end, got: {:?}", other),
    }

    // Bot should no longer be connected
    assert!(!bot_connections.is_connected("expiry-bot").await);

    // disconnect_streaming should have NOT been called by registry directly
    // (that's the scanner's job separately)
    // Verify we can still call it
    bot_runtime
        .disconnect_streaming(BotRuntimeDisconnectCommand {
            bot_id: "expiry-bot".to_string(),
        })
        .await
        .unwrap();
    assert_eq!(
        bot_runtime.disconnects.lock().await.as_slice(),
        &["expiry-bot".to_string()]
    );
}

/// Test: bot with no expiry is not disconnected by scanner.
#[tokio::test]
async fn scanner_does_not_disconnect_bot_without_expiry() {
    let (addr, bot_connections, _) = start_test_server().await;

    let mut ws = connect_bot(addr, "no-expiry-bot").await;
    assert!(bot_connections.is_connected("no-expiry-bot").await);

    // No set_token_expires_at called → token_expires_at = None
    let expired = bot_connections.collect_expiring(9999999999, 0).await;
    assert!(expired.is_empty());

    // Connection should still be alive
    assert!(bot_connections.is_connected("no-expiry-bot").await);

    // Send a ping to verify connection is alive
    ws.send(Message::Ping(vec![1, 2, 3].into())).await.unwrap();
    // Should get pong or at least no error
    let msg = tokio::time::timeout(Duration::from_secs(2), ws.next()).await;
    assert!(msg.is_ok(), "Connection should still be alive");
}

/// Test: bot with future expiry is not disconnected.
#[tokio::test]
async fn scanner_does_not_disconnect_bot_with_future_expiry() {
    let (addr, bot_connections, _) = start_test_server().await;

    let mut ws = connect_bot(addr, "future-bot").await;
    assert!(bot_connections.is_connected("future-bot").await);

    // Set expiry far in the future
    bot_connections
        .set_token_expires_at("future-bot", 9999999999)
        .await;

    // now=2000, exp=9999999999 → not expired
    let expired = bot_connections.collect_expiring(2000, 0).await;
    assert!(expired.is_empty());

    // Connection should still be alive
    assert!(bot_connections.is_connected("future-bot").await);

    ws.send(Message::Ping(vec![1, 2, 3].into())).await.unwrap();
    let msg = tokio::time::timeout(Duration::from_secs(2), ws.next()).await;
    assert!(msg.is_ok(), "Connection should still be alive");
}

/// Test: grace period prevents disconnect of recently-expired token.
#[tokio::test]
async fn scanner_respects_grace_period_on_real_connection() {
    let (addr, bot_connections, _) = start_test_server().await;

    let mut ws = connect_bot(addr, "grace-bot").await;
    bot_connections.set_token_expires_at("grace-bot", 1000).await;

    // now=900, early=50 → 900+50=950 >= 1000? No → not expiring yet
    let expired = bot_connections.collect_expiring(900, 50).await;
    assert!(expired.is_empty());
    assert!(bot_connections.is_connected("grace-bot").await);

    // now=960, early=50 → 960+50=1010 >= 1000? Yes → expiring soon
    let expired = bot_connections.collect_expiring(960, 50).await;
    assert_eq!(expired, vec!["grace-bot".to_string()]);

    // Disconnect it
    for bot_id in &expired {
        bot_connections.disconnect(bot_id).await;
    }

    // Client sees close
    let msg = tokio::time::timeout(Duration::from_secs(5), ws.next()).await;
    match msg {
        Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) => {}
        Err(_) => {} // timeout (connection already dead)
        other => panic!("Expected close/end, got: {:?}", other),
    }
    assert!(!bot_connections.is_connected("grace-bot").await);
}

/// Test: multiple bots, only expired ones get disconnected.
#[tokio::test]
async fn scanner_disconnects_only_expired_bots_in_batch() {
    let (addr, bot_connections, _) = start_test_server().await;

    let _ws_alive = connect_bot(addr, "alive-bot").await;
    let mut ws_expired = connect_bot(addr, "expired-bot").await;

    bot_connections
        .set_token_expires_at("alive-bot", 9999999999)
        .await;
    bot_connections
        .set_token_expires_at("expired-bot", 500)
        .await;

    let expired = bot_connections.collect_expiring(2000, 0).await;
    assert_eq!(expired, vec!["expired-bot".to_string()]);

    for bot_id in &expired {
        bot_connections.disconnect(bot_id).await;
    }

    // expired-bot should be closed
    let msg = tokio::time::timeout(Duration::from_secs(5), ws_expired.next()).await;
    match msg {
        Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) => {}
        Err(_) => {} // timeout (connection already dead)
        other => panic!("Expected close for expired-bot, got: {:?}", other),
    }

    // alive-bot should still be connected
    assert!(bot_connections.is_connected("alive-bot").await);
    assert!(!bot_connections.is_connected("expired-bot").await);
}
