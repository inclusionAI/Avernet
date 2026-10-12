use std::{path::Path, sync::{Arc, Weak}, time::Duration};

use bcs_bridge_core::{
    config::{BotConfig, ConnectionMode, ProviderConfig, PluginConfig},
    engine::EngineRegistry,
    plugin, AppState,
};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::{net::{TcpListener, TcpStream}, task::JoinHandle};
use tokio_tungstenite::{accept_async, tungstenite::Message, WebSocketStream};
use tokio_util::sync::CancellationToken;

type Socket = WebSocketStream<TcpStream>;
const IO_TIMEOUT: Duration = Duration::from_secs(5);

fn init_tracing() {
    let _ = tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("off")))
        .with_target(false)
        .try_init();
}

struct BridgePeer {
    dir: TempDir,
    listener: TcpListener,
    config: ProviderConfig,
    /// Protocol version this mock BCS negotiates (the bridge always requests 3).
    version: u32,
}

impl BridgePeer {
    async fn one_bot() -> Self {
        Self::with_version(3).await
    }

    async fn with_version(version: u32) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let config = config_for(&dir, listener.local_addr().unwrap(), &["worker-1"]);
        Self { dir, listener, config, version }
    }

    async fn start(&self) -> (Weak<AppState>, CancellationToken, JoinHandle<anyhow::Result<()>>) {
        let state = Arc::new(AppState::new(self.config.clone(), &EngineRegistry::builtin()).unwrap());
        self.start_state(state)
    }

    fn start_state(&self, state: Arc<AppState>) -> (Weak<AppState>, CancellationToken, JoinHandle<anyhow::Result<()>>) {
        let state_weak = Arc::downgrade(&state);
        let shutdown = CancellationToken::new();
        let task = tokio::spawn(plugin::serve(state, shutdown.clone()));
        (state_weak, shutdown, task)
    }

    async fn accept(&self, bot_id: &str, expected_token: Option<&str>) -> Socket {
        let (actual_bot_id, socket) = self.accept_any(expected_token).await;
        assert_eq!(actual_bot_id, bot_id);
        socket
    }

    async fn accept_any(&self, expected_token: Option<&str>) -> (String, Socket) {
        let (stream, _) = tokio::time::timeout(IO_TIMEOUT, self.listener.accept())
            .await.unwrap().unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let connect = recv_json(&mut socket).await;
        assert_eq!(connect["type"], "req");
        assert_eq!(connect["method"], "bot.connect");
        let bot_id = connect["params"]["bot_id"].as_str().expect("bot.connect bot_id").to_owned();
        // The bridge always requests V3; the peer negotiates its own version.
        assert_eq!(connect["params"]["protocol_version"], 3);
        match expected_token {
            Some(token) => assert_eq!(connect["params"]["token"], token),
            None => assert!(connect["params"].get("token").is_none()
                || connect["params"]["token"].is_null()),
        }
        send_json(&mut socket, json!({
            "type": "res", "id": connect["id"], "ok": true,
            "payload": {
                "bot_uuid": &bot_id, "token": "token1", "is_new": false,
                "protocol_version": self.version, "min_supported_version": 1
            }
        })).await;
        (bot_id, socket)
    }
}

#[tokio::test]
async fn reconnect_delivers_offline_final_without_restarting_engine() {
    let peer = BridgePeer::one_bot().await;
    let (_, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    let request = chat_send("request-1", "run-1", "group-a:session-a", "WAIT_OFFLINE original");
    send_json(&mut socket, request.clone()).await;

    let ack = recv_application(&mut socket, |frame| frame["id"] == "request-1").await;
    assert_eq!(ack["payload"]["run_id"], "run-1");
    let first = recv_application(&mut socket, is_delta).await;
    assert_eq!(first["event"], "chat");
    assert_eq!(first["payload"]["runId"], "run-1");
    assert_eq!(first["payload"]["sessionId"], "group-a:session-a");
    let first_seq = first["seq"].as_u64().expect("delta has outer sequence");

    socket.close(None).await.unwrap();
    let engine_dir = peer.dir.path().join("worker-1");
    wait_for_file(&engine_dir.join("initial-delta")).await;
    std::fs::write(engine_dir.join("release-engine"), b"release").unwrap();
    wait_for_file(&engine_dir.join("engine-complete")).await;

    let mut socket = peer.accept("bot-a", Some("token1")).await;
    let final_frame = recv_application(&mut socket, is_final).await;
    assert_eq!(final_frame["event"], "chat");
    assert_eq!(final_frame["payload"]["runId"], "run-1");
    assert_eq!(final_frame["payload"]["sessionId"], "group-a:session-a");
    assert!(final_frame["seq"].as_u64().unwrap() > first_seq);
    assert!(final_frame.to_string().contains("WAIT_OFFLINE original"));

    send_json(&mut socket, request).await;
    let replay_ack = recv_response_without_events(&mut socket, "request-1").await;
    assert_eq!(replay_ack["payload"]["run_id"], "run-1");
    send_json(&mut socket, json!({"type":"req", "id":"barrier-1", "method":"bot.ping"})).await;
    let barrier = recv_response_without_events(&mut socket, "barrier-1").await;
    assert_eq!(barrier["ok"], true);
    assert_eq!(engine_starts(peer.dir.path()).len(), 1,
        "an idempotent resend must neither replay delivered frames nor invoke the engine again");

    stop(shutdown, task).await;
}

#[tokio::test]
async fn inject_survives_bridge_restart_and_the_next_turn_resumes_engine_session() {
    let peer = BridgePeer::one_bot().await;
    let (old_state, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    send_json(&mut socket, chat_inject("inject-1", "group-a:session-a", "saved context")).await;
    let ack = recv_application(&mut socket, |frame| frame["id"] == "inject-1").await;
    assert_eq!(ack["ok"], true);
    socket.close(None).await.unwrap();
    stop(shutdown, task).await;
    assert!(old_state.upgrade().is_none(), "serve returned while retaining AppState");

    let (_, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", Some("token1")).await;
    send_json(&mut socket, chat_send("send-1", "run-first", "group-a:session-a", "first turn")).await;
    let ack = recv_application(&mut socket, |frame| frame["id"] == "send-1").await;
    assert_eq!(ack["payload"]["run_id"], "run-first");
    let first_final = recv_application(&mut socket, is_final).await;
    assert!(first_final.to_string().contains("saved context"));
    assert!(first_final.to_string().contains("first turn"));

    send_json(&mut socket, chat_send("send-2", "run-second", "group-a:session-a", "second turn")).await;
    let ack = recv_application(&mut socket, |frame| frame["id"] == "send-2").await;
    assert_eq!(ack["payload"]["run_id"], "run-second");
    let second_final = recv_application(&mut socket, is_final).await;
    assert!(second_final.to_string().contains("resume=11111111-1111-4111-8111-111111111111"));

    let starts = engine_starts(peer.dir.path());
    assert_eq!(starts.len(), 2);
    assert!(starts[0]["prompt"].as_str().unwrap().contains("saved context"));
    assert_eq!(starts[1]["resume"], "11111111-1111-4111-8111-111111111111");
    assert!(!starts[1]["prompt"].as_str().unwrap().contains("saved context"),
        "a committed inject must be consumed exactly once");
    stop(shutdown, task).await;
}

#[tokio::test]
async fn abort_targets_the_requested_run_and_reaps_its_engine() {
    let peer = BridgePeer::one_bot().await;
    let (_, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    send_json(&mut socket, chat_send("send-abort", "run-abort", "group-a", "WAIT_FOR_ABORT")).await;
    let _ack = recv_application(&mut socket, |frame| frame["id"] == "send-abort").await;
    let _delta = recv_application(&mut socket, is_delta).await;

    send_json(&mut socket, json!({
        "type": "req", "id": "abort-1", "method": "chat.abort",
        "params": {"session_key": "group-a", "bcs_group_id": "group-a", "run_id": "run-abort"}
    })).await;
    let ack = recv_application(&mut socket, |frame| frame["id"] == "abort-1").await;
    assert_eq!(ack["ok"], true);
    assert_eq!(ack["payload"]["aborted"], true);
    assert_eq!(ack["payload"]["aborted_run_ids"], json!(["run-abort"]));
    let terminal = recv_application(&mut socket, |frame| frame["payload"]["state"] == "aborted").await;
    assert_eq!(terminal["event"], "chat");
    assert_eq!(terminal["payload"]["runId"], "run-abort");
    stop(shutdown, task).await;
}

#[tokio::test]
async fn missing_heartbeat_response_forces_an_authenticated_reconnect() {
    let mut peer = BridgePeer::one_bot().await;
    let plugin = peer.config.plugin.as_mut().unwrap();
    plugin.heartbeat_interval_ms = 20;
    plugin.connect_timeout_ms = 80;
    let (_, shutdown, task) = peer.start().await;
    let mut first = peer.accept("bot-a", None).await;
    let status = recv_json(&mut first).await;
    assert_eq!(status["type"], "req");
    assert_eq!(status["method"], "bot.status");
    assert_eq!(status["params"]["status"], "idle");

    // Deliberately leave bot.status unanswered. The next accepted socket proves
    // that the bounded heartbeat timeout closed the stale generation.
    let _reconnected = peer.accept("bot-a", Some("token1")).await;
    stop(shutdown, task).await;
}

#[tokio::test]
async fn two_bots_keep_same_group_sessions_and_events_isolated() {
    let dir = tempfile::tempdir().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let config = config_for(&dir, listener.local_addr().unwrap(), &["worker-1", "worker-2"]);
    let peer = BridgePeer { dir, listener, config, version: 3 };
    let (_, shutdown, task) = peer.start().await;
    let (first_id, first) = peer.accept_any(None).await;
    let (second_id, second) = peer.accept_any(None).await;
    let (mut bot_a, mut bot_b) = if first_id == "bot-a" {
        assert_eq!(second_id, "bot-b");
        (first, second)
    } else {
        assert_eq!(first_id, "bot-b");
        assert_eq!(second_id, "bot-a");
        (second, first)
    };

    send_json(&mut bot_a, chat_send("a-send", "run-a", "shared-group", "alpha-only")).await;
    send_json(&mut bot_b, chat_send("b-send", "run-b", "shared-group", "beta-only")).await;
    let a_ack = recv_response_without_events(&mut bot_a, "a-send").await;
    let b_ack = recv_response_without_events(&mut bot_b, "b-send").await;
    assert_eq!(a_ack["payload"]["run_id"], "run-a");
    assert_eq!(b_ack["payload"]["run_id"], "run-b");
    let a_final = recv_application(&mut bot_a, is_final).await;
    let b_final = recv_application(&mut bot_b, is_final).await;
    assert!(a_final.to_string().contains("alpha-only"));
    assert!(!a_final.to_string().contains("beta-only"));
    assert!(b_final.to_string().contains("beta-only"));
    assert!(!b_final.to_string().contains("alpha-only"));
    assert_eq!(engine_starts_for(peer.dir.path(), "worker-1").len(), 1);
    assert_eq!(engine_starts_for(peer.dir.path(), "worker-2").len(), 1);
    stop(shutdown, task).await;
}

#[tokio::test]
async fn identity_write_failure_after_handshake_is_fatal_and_accepts_no_work() {
    let peer = BridgePeer::one_bot().await;
    let state = Arc::new(AppState::new(peer.config.clone(), &EngineRegistry::builtin()).unwrap());
    let db = rusqlite::Connection::open(&peer.config.state_path).unwrap();
    db.execute_batch("
        CREATE TRIGGER fail_identity_insert
        BEFORE INSERT ON connection_identities
        BEGIN
            SELECT RAISE(ABORT, 'synthetic identity write failure');
        END;
    ").unwrap();
    let (_, _shutdown, task) = peer.start_state(state);
    let mut socket = peer.accept("bot-a", None).await;

    let error = tokio::time::timeout(IO_TIMEOUT, task).await
        .expect("serve did not fail after identity persistence failure")
        .expect("serve task panicked").expect_err("identity persistence failure must be fatal");
    assert!(error.to_string().contains("cannot persist plugin identity"), "unexpected error: {error:#}");
    let closed = tokio::time::timeout(IO_TIMEOUT, socket.next()).await
        .expect("socket stayed usable after fatal identity failure");
    assert!(matches!(closed, None | Some(Ok(Message::Close(_))) | Some(Err(_))));
    assert_eq!(identity_count(&db), 0, "failed credentials must not be cached");
    assert!(!peer.dir.path().join("worker-1/engine-starts.jsonl").exists(),
        "Bridge must not accept work before credentials persist");
}

#[tokio::test]
async fn mismatched_handshake_identity_or_protocol_is_fatal_and_not_cached() {
    assert_bad_handshake(json!({
        "bot_uuid": "wrong-bot", "token": "wrong-token", "is_new": false,
        "protocol_version": 2, "min_supported_version": 1
    })).await;
    assert_bad_handshake(json!({
        "bot_uuid": "bot-a", "token": "wrong-token", "is_new": false,
        "protocol_version": 1, "min_supported_version": 1
    })).await;
}

#[cfg(unix)]
#[tokio::test]
async fn shutdown_reaps_a_running_engine_before_serve_returns() {
    let peer = BridgePeer::one_bot().await;
    let (_, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    send_json(&mut socket, chat_send("shutdown-send", "shutdown-run", "group-a", "WAIT_FOR_ABORT")).await;
    let _ack = recv_response_without_events(&mut socket, "shutdown-send").await;
    let _delta = recv_application(&mut socket, is_delta).await;
    let starts = engine_starts(peer.dir.path());
    let pid = starts[0]["pid"].as_u64().expect("fixture records child PID");
    assert!(process_exists(pid), "mock engine exited before shutdown");

    shutdown.cancel();
    tokio::time::timeout(IO_TIMEOUT, task).await
        .expect("serve ignored shutdown").expect("serve task panicked").unwrap();
    assert!(!process_exists(pid), "serve returned before mock engine PID {pid} was reaped");
}

#[tokio::test]
async fn bot_kicked_stops_the_client_without_reconnecting() {
    let peer = BridgePeer::one_bot().await;
    let (_, _shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    send_json(&mut socket, json!({
        "type": "event", "event": "bot.kicked", "payload": {"reason": "delivery mode changed"}
    })).await;

    tokio::time::timeout(IO_TIMEOUT, task).await
        .expect("serve retried after bot.kicked").expect("serve task panicked").unwrap();
    assert!(tokio::time::timeout(Duration::from_millis(100), peer.listener.accept()).await.is_err(),
        "bot.kicked must not open another WebSocket connection");
}

/// A V2-only server keeps the legacy wire (event "chat.event", snake_case
/// run_id) and interactions stay unsupported — the terminal error is the
/// documented V2 limitation, keeping the fallback path observable.
#[tokio::test]
async fn v2_fallback_keeps_legacy_frames_and_reports_unsupported_interaction() {
    let peer = BridgePeer::with_version(2).await;
    let (_, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    send_json(&mut socket, chat_send("send-permission", "run-permission", "group-a", "REQUEST_PERMISSION")).await;
    let _ack = recv_application(&mut socket, |frame| frame["id"] == "send-permission").await;
    let terminal = recv_application(&mut socket, |frame| frame["payload"]["state"] == "error").await;
    assert_eq!(terminal["event"], "chat.event");
    assert_eq!(terminal["payload"]["run_id"], "run-permission");
    assert_eq!(terminal["payload"]["errorKind"], "unsupported_interaction");
    assert!(terminal["payload"].get("bcs_group_id").is_some(), "legacy V2 frames carry bcs_group_id");
    assert!(!terminal.to_string().contains("\"phase\":\"requested\""));
    stop(shutdown, task).await;
}

/// The full V3 HITL loop: the engine's permission request surfaces as an
/// uplink `interaction` (requested) frame carrying the canonical V3 run
/// contract; a downlink `interaction.resolve` delivers the human decision to
/// the parked driver; the resolved frame and the run final follow.
#[tokio::test]
async fn v3_interaction_round_trip_resolves_and_finishes_the_run() {
    init_tracing();
    let peer = BridgePeer::one_bot().await;
    let (_, shutdown, task) = peer.start().await;
    let mut socket = peer.accept("bot-a", None).await;
    // V3-shaped downlink: the real group id plus an explicit session id.
    let mut send = chat_send("send-permission", "run-permission", "group-a", "REQUEST_PERMISSION");
    send["params"]["bcs_session_id"] = json!("group-a:session-a");
    send_json(&mut socket, send).await;
    let _ack = recv_application(&mut socket, |frame| frame["id"] == "send-permission").await;
    let delta = recv_application(&mut socket, is_delta).await;
    let requested = recv_application(&mut socket, |frame| frame["event"] == "interaction").await;
    assert_eq!(requested["type"], "event");
    assert_eq!(requested["payload"]["runId"], "run-permission");
    assert_eq!(requested["payload"]["sessionId"], "group-a:session-a");
    assert_eq!(requested["payload"]["phase"], "requested");
    assert_eq!(requested["payload"]["kind"], "exec");
    assert!(requested["payload"]["interactionId"].as_str().unwrap().starts_with("int-"));
    assert!(requested["payload"]["seq"].as_u64().is_some(), "V3 requires a payload seq");
    assert!(requested["payload"]["ts"].as_u64().is_some(), "V3 requires a payload ts");
    assert_eq!(requested["seq"], requested["payload"]["seq"], "outer seq must equal payload seq");
    assert!(requested["payload"]["options"].is_array(), "engine-native options ride in raw");
    assert_eq!(requested["payload"]["command"], "printf forbidden");

    // The canonical seq ledger is per run: the interaction frame follows the
    // delta without reusing its sequence value.
    assert!(requested["payload"]["seq"].as_u64().unwrap() > delta["payload"]["seq"].as_u64().unwrap());

    // Deliver the human decision over the downlink interaction.resolve request.
    let interaction_id = requested["payload"]["interactionId"].as_str().unwrap().to_owned();
    send_json(&mut socket, json!({
        "type": "req", "id": "resolve-1", "method": "interaction.resolve",
        "params": {
            "interactionId": interaction_id, "idempotencyKey": "resolve-key-1",
            "kind": "exec", "decision": "deny",
            "bcsRunId": "bcs-run-x", "runId": "run-permission"
        }
    })).await;
    let ack = recv_application(&mut socket, |frame| frame["id"] == "resolve-1").await;
    assert_eq!(ack["type"], "res");
    assert_eq!(ack["ok"], true);
    assert_eq!(ack["payload"]["ok"], true);

    // A duplicate resolve of the same interaction replies the same ack
    // without re-writing the engine control channel.
    send_json(&mut socket, json!({
        "type": "req", "id": "resolve-2", "method": "interaction.resolve",
        "params": {
            "interactionId": interaction_id, "idempotencyKey": "resolve-key-1",
            "kind": "exec", "decision": "deny"
        }
    })).await;
    let replay = recv_application(&mut socket, |frame| frame["id"] == "resolve-2").await;
    assert_eq!(replay["ok"], true);
    assert_eq!(replay["payload"]["ok"], true);

    let resolved = recv_application(&mut socket, |frame|
        frame["event"] == "interaction" && frame["payload"]["phase"] == "resolved").await;
    assert_eq!(resolved["payload"]["runId"], "run-permission");
    assert_eq!(resolved["payload"]["decision"], "deny");
    assert_eq!(resolved["seq"], resolved["payload"]["seq"]);

    // The driver mapped deny to the engine's control channel; the run finishes.
    let final_frame = recv_application(&mut socket, is_final).await;
    assert_eq!(final_frame["event"], "chat");
    assert_eq!(final_frame["payload"]["runId"], "run-permission");
    assert!(final_frame.to_string().contains("REQUEST_PERMISSION"));
    stop(shutdown, task).await;
}

fn config_for(dir: &TempDir, addr: std::net::SocketAddr, bots: &[&str]) -> ProviderConfig {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mock_cc_upstream.py");
    ProviderConfig {
        provider_id: "upstream-test".into(),
        mode: ConnectionMode::Plugin,
        listen: None,
        bcs_to_provider_token: None,
        plugin: Some(PluginConfig {
            url: format!("ws://{addr}/ws/bot"),
            reconnect_interval_ms: 20,
            heartbeat_interval_ms: 60_000,
            connect_timeout_ms: 1_000,
        }),
        bot_runtime_token: None,
        trace_dir: None,
        state_path: dir.path().join("bridge-state.sqlite3"),
        bots: bots.iter().enumerate().map(|(index, name)| {
            let cwd = dir.path().join(name);
            std::fs::create_dir_all(&cwd).unwrap();
            BotConfig {
                provider_bot_ref: (*name).into(),
                bot_id: Some(format!("bot-{}", (b'a' + index as u8) as char)),
                token: None,
                engine: "claude-code".into(),
                model: None,
                cwd,
                permission_mode: None,
                engine_bin: Some(fixture.clone()),
                engine_options: toml::Table::new(),
            }
        }).collect(),
    }
}

fn chat_send(id: &str, run_id: &str, group: &str, text: &str) -> Value {
    json!({
        "type": "req", "id": id, "method": "chat.send",
        "params": {
            "session_key": group, "bcs_group_id": group,
            "message": {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": 0},
            "channel": {"type": "group", "id": group},
            "session_context": {"source": "upstream-integration-test"},
            "idempotency_key": run_id
        }
    })
}

fn chat_inject(id: &str, group: &str, text: &str) -> Value {
    json!({
        "type": "req", "id": id, "method": "chat.inject",
        "params": {
            "session_key": group, "bcs_group_id": group,
            "message": {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": 0},
            "from": {"kind": "bot", "name": "observer"},
            "idempotency_key": id
        }
    })
}

async fn send_json(socket: &mut Socket, value: Value) {
    socket.send(Message::Text(value.to_string().into())).await.unwrap();
}

async fn recv_json(socket: &mut Socket) -> Value {
    loop {
        let message = tokio::time::timeout(IO_TIMEOUT, socket.next()).await
            .unwrap_or_else(|_| panic!("timed out waiting for WebSocket frame"))
            .expect("WebSocket closed").expect("valid WebSocket frame");
        if let Message::Text(text) = message {
            return serde_json::from_str(&text).unwrap_or_else(|error| panic!("invalid JSON frame ({error}): {text}"));
        }
    }
}

async fn recv_application(mut socket: &mut Socket, predicate: impl Fn(&Value) -> bool) -> Value {
    loop {
        let frame = recv_json(&mut socket).await;
        if frame["type"] == "req" && frame["method"] == "bot.status" {
            send_json(&mut socket, json!({"type":"res", "id":frame["id"], "ok":true, "payload":{"updated":true}})).await;
            continue;
        }
        if predicate(&frame) { return frame; }
    }
}

async fn recv_response_without_events(socket: &mut Socket, id: &str) -> Value {
    loop {
        let frame = recv_json(socket).await;
        if frame["type"] == "req" && frame["method"] == "bot.status" {
            send_json(socket, json!({"type":"res", "id":frame["id"], "ok":true, "payload":{"updated":true}})).await;
            continue;
        }
        assert_ne!(frame["type"], "event", "event was delivered before response {id}: {frame}");
        if frame["type"] == "res" && frame["id"] == id { return frame; }
    }
}

fn is_delta(frame: &Value) -> bool {
    let state = frame["payload"]["state"].as_str().unwrap_or_default();
    (frame["event"] == "chat" || frame["event"] == "chat.event") && state == "delta"
}

fn is_final(frame: &Value) -> bool {
    let state = frame["payload"]["state"].as_str().unwrap_or_default();
    (frame["event"] == "chat" || frame["event"] == "chat.event") && state == "final"
}

async fn wait_for_file(path: &Path) {
    tokio::time::timeout(IO_TIMEOUT, async {
        while !path.exists() { tokio::time::sleep(Duration::from_millis(10)).await; }
    }).await.unwrap_or_else(|_| panic!("timed out waiting for {}", path.display()));
}

fn engine_starts(dir: &Path) -> Vec<Value> {
    engine_starts_for(dir, "worker-1")
}

fn engine_starts_for(dir: &Path, bot: &str) -> Vec<Value> {
    std::fs::read_to_string(dir.join(bot).join("engine-starts.jsonl")).unwrap()
        .lines().map(|line| serde_json::from_str(line).unwrap()).collect()
}

async fn assert_bad_handshake(payload: Value) {
    let peer = BridgePeer::one_bot().await;
    let (_, _shutdown, task) = peer.start().await;
    let (stream, _) = tokio::time::timeout(IO_TIMEOUT, peer.listener.accept())
        .await.expect("Bridge did not connect").unwrap();
    let mut socket = accept_async(stream).await.unwrap();
    let connect = recv_json(&mut socket).await;
    assert_eq!(connect["method"], "bot.connect");
    send_json(&mut socket, json!({
        "type": "res", "id": connect["id"], "ok": true, "payload": payload
    })).await;

    let error = tokio::time::timeout(IO_TIMEOUT, task).await
        .expect("serve did not reject mismatched handshake")
        .expect("serve task panicked").expect_err("mismatched handshake must be fatal");
    assert!(error.to_string().contains("mismatched identity"), "unexpected error: {error:#}");
    let db = rusqlite::Connection::open(&peer.config.state_path).unwrap();
    assert_eq!(identity_count(&db), 0, "mismatched credentials must not be cached");
}

fn identity_count(db: &rusqlite::Connection) -> i64 {
    db.query_row("SELECT COUNT(*) FROM connection_identities", [], |row| row.get(0)).unwrap()
}

#[cfg(unix)]
#[allow(unsafe_code)]
fn process_exists(pid: u64) -> bool {
    let pid = libc::pid_t::try_from(pid).expect("fixture PID fits pid_t");
    assert!(pid > 0, "fixture must record an individual process PID");
    // SAFETY: signal 0 only checks existence/permission; it sends no signal.
    // Unlike /proc, this also works on macOS. EPERM still means the PID exists.
    let result = unsafe { libc::kill(pid, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

async fn stop(shutdown: CancellationToken, task: JoinHandle<anyhow::Result<()>>) {
    shutdown.cancel();
    tokio::time::timeout(IO_TIMEOUT, task).await
        .expect("plugin server ignored shutdown").expect("plugin task panicked").unwrap();
}
