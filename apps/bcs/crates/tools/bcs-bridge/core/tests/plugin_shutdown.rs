//! Shutdown must finish the WebSocket handshake before releasing the bridge owner.
use std::{sync::Arc, time::Duration};

use bcs_bridge_core::{config::ProviderConfig, engine::EngineRegistry, plugin, AppState};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::{net::{TcpListener, TcpStream}, task::JoinHandle, time::timeout};
use tokio_tungstenite::{accept_async, tungstenite::{Message, protocol::frame::coding::CloseCode}, WebSocketStream};
use tokio_util::sync::CancellationToken;

type Socket = WebSocketStream<TcpStream>;
const WAIT: Duration = Duration::from_secs(5);

struct Peer {
    _dir: tempfile::TempDir,
    listener: TcpListener,
    config: ProviderConfig,
}

impl Peer {
    async fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let path = dir.path().join("bridge.toml");
        std::fs::write(&path, format!(r#"
provider_id = "shutdown-test"
mode = "plugin"
state_path = {}
[plugin]
url = "ws://{}/ws/bot"
connect_timeout_ms = 30000
heartbeat_interval_ms = 60000
reconnect_interval_ms = 20
[[bot]]
provider_bot_ref = "worker"
bot_id = "bot-shutdown"
engine = "claude-code"
cwd = {}
"#, json!(dir.path().join("state.sqlite3")), listener.local_addr().unwrap(), json!(dir.path()))).unwrap();
        let config = ProviderConfig::load(&path).unwrap();
        Self { _dir: dir, listener, config }
    }

    fn start(&self) -> (CancellationToken, JoinHandle<anyhow::Result<()>>) {
        let state = Arc::new(AppState::new(self.config.clone(), &EngineRegistry::builtin()).unwrap());
        let stop = CancellationToken::new();
        let task = tokio::spawn(plugin::serve(state, stop.clone()));
        (stop, task)
    }

    async fn accept(&self) -> (Socket, Value) {
        let (stream, _) = timeout(WAIT, self.listener.accept()).await.unwrap().unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let connect = recv_json(&mut socket).await;
        assert_eq!(connect["method"], "bot.connect");
        assert_eq!(connect["params"]["bot_id"], "bot-shutdown");
        (socket, connect)
    }
}

async fn recv_json(socket: &mut Socket) -> Value {
    match timeout(WAIT, socket.next()).await.unwrap().unwrap().unwrap() {
        Message::Text(text) => serde_json::from_str(&text).unwrap(),
        frame => panic!("expected application frame, got {frame:?}"),
    }
}

async fn login(socket: &mut Socket, connect: &Value, version: u32) {
    socket.send(Message::Text(json!({
        "type":"res", "id":connect["id"], "ok":true,
        "payload":{"bot_uuid":"bot-shutdown", "token":"test-shutdown-token", "is_new":false,
            "protocol_version":version, "min_supported_version":1}
    }).to_string().into())).await.unwrap();
}

async fn online(socket: &mut Socket) {
    // The heartbeat is emitted only after the validated identity was committed.
    let heartbeat = recv_json(socket).await;
    assert_eq!(heartbeat["method"], "bot.status");
    socket.send(Message::Text(json!({"type":"res", "id":heartbeat["id"], "ok":true,
        "payload":{"updated":true}}).to_string().into())).await.unwrap();
}

async fn expect_close(socket: &mut Socket) {
    let frame = timeout(WAIT, socket.next()).await.expect("client did not send Close")
        .expect("transport dropped without Close").expect("unclean WebSocket shutdown");
    match frame {
        Message::Close(Some(close)) => assert_eq!(close.code, CloseCode::Normal),
        frame => panic!("expected normal Close, got {frame:?}"),
    }
}

async fn finish(task: JoinHandle<anyhow::Result<()>>) -> anyhow::Result<()> {
    timeout(WAIT, task).await.expect("shutdown did not finish").expect("bridge task panicked")
}

#[tokio::test]
async fn stop_waits_for_close_reply_then_same_identity_can_restart() {
    for version in [2, 3] {
        let peer = Peer::new().await;
        for round in 0..3 {
            let (stop, mut task) = peer.start();
            let (mut socket, connect) = peer.accept().await;
            if round > 0 { assert_eq!(connect["params"]["token"], "test-shutdown-token"); }
            login(&mut socket, &connect, version).await;
            online(&mut socket).await;
            stop.cancel();
            expect_close(&mut socket).await;
            assert!(timeout(Duration::from_millis(50), &mut task).await.is_err(),
                "bridge owner released before peer acknowledged Close");
            // Reading Close queues the peer's reply; flush it explicitly.
            socket.flush().await.unwrap();
            finish(task).await.unwrap();
        }
    }
}

#[tokio::test]
async fn stop_during_bot_connect_closes_the_established_websocket() {
    let peer = Peer::new().await;
    let (stop, task) = peer.start();
    let (mut socket, _) = peer.accept().await;
    // Keep the business handshake pending (30s timeout), then stop immediately.
    stop.cancel();
    expect_close(&mut socket).await;
    socket.flush().await.unwrap();
    finish(task).await.unwrap();
}

#[tokio::test]
async fn rejected_login_still_closes_before_returning_the_original_error() {
    let peer = Peer::new().await;
    let (_stop, task) = peer.start();
    let (mut socket, connect) = peer.accept().await;
    socket.send(Message::Text(json!({"type":"res", "id":connect["id"], "ok":false,
        "error":{"code":"invalid_token", "message":"synthetic rejection", "retryable":false}}
    ).to_string().into())).await.unwrap();
    expect_close(&mut socket).await;
    socket.flush().await.unwrap();
    assert!(finish(task).await.unwrap_err().to_string().contains("BCS rejected bot.connect"));
}

#[tokio::test]
async fn identity_commit_failure_closes_the_authenticated_connection() {
    let peer = Peer::new().await;
    let (_stop, task) = peer.start();
    let (mut socket, connect) = peer.accept().await;
    // Fail only the identity write, after store initialization and WS upgrade.
    let db = rusqlite::Connection::open(&peer.config.state_path).unwrap();
    db.execute_batch("CREATE TRIGGER fail_identity BEFORE INSERT ON connection_identities
        BEGIN SELECT RAISE(FAIL, 'synthetic identity write failure'); END;").unwrap();
    login(&mut socket, &connect, 3).await;
    expect_close(&mut socket).await;
    socket.flush().await.unwrap();
    assert!(finish(task).await.unwrap_err().to_string().contains("cannot persist plugin identity"));
}

#[tokio::test]
async fn unresponsive_close_peer_does_not_hold_shutdown_indefinitely() {
    let peer = Peer::new().await;
    let (stop, task) = peer.start();
    let (mut socket, connect) = peer.accept().await;
    login(&mut socket, &connect, 3).await;
    online(&mut socket).await;
    stop.cancel();
    expect_close(&mut socket).await;
    // Leave the queued reply unflushed and keep TCP open. This is shorter than
    // connect_timeout_ms and the CLI's 10s lifecycle deadline.
    timeout(Duration::from_secs(4), task).await.expect("Close wait must be bounded")
        .unwrap().unwrap();
}

#[tokio::test]
async fn bot_connect_timeout_closes_before_retry_and_stop_prevents_reconnect() {
    let mut peer = Peer::new().await;
    peer.config.plugin.as_mut().unwrap().connect_timeout_ms = 100;
    let (stop, task) = peer.start();
    let (mut socket, _) = peer.accept().await;
    expect_close(&mut socket).await;
    stop.cancel();
    socket.flush().await.unwrap();
    finish(task).await.unwrap();
    assert!(timeout(Duration::from_millis(100), peer.listener.accept()).await.is_err());
}

#[tokio::test]
async fn kicked_bot_closes_without_reconnecting() {
    let peer = Peer::new().await;
    let (_stop, task) = peer.start();
    let (mut socket, connect) = peer.accept().await;
    login(&mut socket, &connect, 3).await;
    online(&mut socket).await;
    socket.send(Message::Text(json!({"type":"event", "event":"bot.kicked",
        "payload":{"reason":"synthetic eviction"}}).to_string().into())).await.unwrap();
    expect_close(&mut socket).await;
    socket.flush().await.unwrap();
    finish(task).await.unwrap();
    assert!(timeout(Duration::from_millis(100), peer.listener.accept()).await.is_err());
}

#[tokio::test]
async fn peer_initiated_close_is_acknowledged_before_reconnect() {
    let peer = Peer::new().await;
    let (stop, task) = peer.start();
    let (mut socket, connect) = peer.accept().await;
    login(&mut socket, &connect, 3).await;
    online(&mut socket).await;
    socket.close(Some(tokio_tungstenite::tungstenite::protocol::CloseFrame {
        code: CloseCode::Away, reason: "peer restart".into(),
    })).await.unwrap();
    let reply = timeout(WAIT, socket.next()).await.unwrap().unwrap().unwrap();
    let Message::Close(Some(close)) = reply else { panic!("expected Close reply, got {reply:?}"); };
    assert_eq!(close.code, CloseCode::Away);
    assert_eq!(close.reason, "peer restart");
    drop(socket);
    let (mut socket, connect) = peer.accept().await;
    assert_eq!(connect["params"]["token"], "test-shutdown-token");
    login(&mut socket, &connect, 3).await;
    online(&mut socket).await;
    stop.cancel();
    expect_close(&mut socket).await;
    socket.flush().await.unwrap();
    finish(task).await.unwrap();
}
