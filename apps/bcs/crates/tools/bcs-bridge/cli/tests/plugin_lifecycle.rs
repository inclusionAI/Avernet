#![cfg(unix)]
//! Exercise the actual daemon/control-socket lifecycle against a local WS peer.
use std::{fs, path::PathBuf, process::{Output, Stdio}, time::Duration};

use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::{net::TcpListener, process::Command, time::{sleep, timeout}};
use tokio_tungstenite::{accept_async, tungstenite::{Message, protocol::frame::coding::CloseCode}};

const WAIT: Duration = Duration::from_secs(8);
struct Fixture { home: tempfile::TempDir, config: PathBuf }

impl Fixture {
    fn new(address: std::net::SocketAddr) -> Self {
        let home = tempfile::Builder::new().prefix("bridge-ws-stop-").tempdir_in("/tmp").unwrap();
        let config = home.path().join("bridge.toml");
        fs::write(&config, format!(r#"
provider_id = "lifecycle-test"
mode = "plugin"
state_path = "state.sqlite3"
[plugin]
url = "ws://{address}/ws/bot"
heartbeat_interval_ms = 60000
connect_timeout_ms = 30000
[[bot]]
provider_bot_ref = "worker"
bot_id = "bot-lifecycle"
engine = "claude-code"
engine_bin = "/bin/true"
cwd = {}
"#, json!(home.path()))).unwrap();
        Self { home, config }
    }

    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
        command.current_dir(self.home.path()).env("HOME", self.home.path())
            .env_remove("BRIDGE_CONFIG").env_remove("BCS_BRIDGE_PROVIDER_TOKENS")
            .env("RUST_LOG", "info").kill_on_drop(true);
        command
    }

    async fn output(&self, args: &[&str]) -> Output {
        timeout(WAIT, self.command().args(args).output()).await.unwrap().unwrap()
    }

    async fn status(&self) -> Value {
        let output = self.output(&["status", "--json"]).await;
        success(&output);
        serde_json::from_slice(&output.stdout).unwrap()
    }

    fn runtime(&self, name: &str) -> PathBuf { self.home.path().join(".bcn-bridge").join(name) }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        // Only ever clean up this test's temporary HOME, including failed assertions.
        let _ = std::process::Command::new(env!("CARGO_BIN_EXE_bcs-bridge"))
            .env("HOME", self.home.path()).arg("stop")
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
}

fn success(output: &Output) {
    assert!(output.status.success(), "{} {}{}", output.status,
        String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
}

#[tokio::test]
async fn plugin_daemon_stop_waits_for_close_before_status_stopped_and_restart() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let fixture = Fixture::new(listener.local_addr().unwrap());
    for round in 0..3 {
        let started = fixture.output(&["start", "--daemon", "--config", fixture.config.to_str().unwrap()]).await;
        success(&started);
        assert!(String::from_utf8_lossy(&started.stdout).contains("check Bot connectivity"));
        let status = fixture.status().await;
        assert_eq!(status["state"], "running");
        assert_eq!(status["health"], "connecting", "daemon readiness is not Bot login");
        assert_ne!(status["bots"][0]["state"], "online");
        let (stream, _) = timeout(WAIT, listener.accept()).await.unwrap().unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let Message::Text(text) = timeout(WAIT, socket.next()).await.unwrap().unwrap().unwrap()
            else { panic!("expected bot.connect"); };
        let connect: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(connect["method"], "bot.connect");
        assert_eq!(connect["params"]["bot_id"], "bot-lifecycle");
        if round > 0 { assert_eq!(connect["params"]["token"], "test-lifecycle-token"); }
        socket.send(Message::Text(json!({"type":"res", "id":connect["id"], "ok":true,
            "payload":{"bot_uuid":"bot-lifecycle", "token":"test-lifecycle-token",
                "is_new":false, "protocol_version":3, "min_supported_version":1}}
        ).to_string().into())).await.unwrap();
        // Drain the immediate heartbeat so the next frame is the Close.
        let Message::Text(text) = timeout(WAIT, socket.next()).await.unwrap().unwrap().unwrap()
            else { panic!("expected heartbeat after login"); };
        let heartbeat: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(heartbeat["method"], "bot.status");
        socket.send(Message::Text(json!({"type":"res", "id":heartbeat["id"], "ok":true,
            "payload":{"updated":true}}).to_string().into())).await.unwrap();
        timeout(WAIT, async {
            loop {
                let status = fixture.status().await;
                if status["bots"][0]["state"] == "online" {
                    assert_eq!(status["health"], "ready");
                    break;
                }
                sleep(Duration::from_millis(20)).await;
            }
        }).await.unwrap();
        let child = fixture.command().arg("stop").stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
        let mut stopped = Box::pin(child.wait_with_output());
        let frame = timeout(WAIT, socket.next()).await.expect("stop must send Close")
            .expect("transport dropped without Close").expect("unclean shutdown");
        let Message::Close(Some(close)) = frame else { panic!("expected Close, got {frame:?}"); };
        assert_eq!(close.code, CloseCode::Normal);
        assert!(timeout(Duration::from_millis(100), &mut stopped).await.is_err(),
            "stop reported success before the WebSocket peer acknowledged Close");
        assert_eq!(fixture.status().await["state"], "stopping");
        socket.flush().await.unwrap();
        success(&timeout(WAIT, stopped).await.unwrap().unwrap());
        assert_eq!(fixture.status().await["state"], "stopped");
        assert!(!fixture.runtime("bridge.pid.json").exists());
    }
    let log = fs::read_to_string(fixture.runtime("bridge.log")).unwrap();
    assert!(!log.contains("test-lifecycle-token"));
}
