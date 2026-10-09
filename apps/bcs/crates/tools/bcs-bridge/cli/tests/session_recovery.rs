//! Exercise durable state through the real binary, HTTP, and a local engine peer.
//! Every instance owns an isolated config, HOME, working directory, and SQLite DB.

use std::{net::TcpListener, path::PathBuf, process::Stdio, time::Duration};

use reqwest::{Client, Response, StatusCode};
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::{process::{Child, Command}, time::{sleep, timeout, Instant}};

const DEADLINE: Duration = Duration::from_secs(6);

struct Bridge {
    child: Option<Child>,
    dir: TempDir,
    config: PathBuf,
    url: String,
    client: Client,
}

impl Bridge {
    async fn start() -> Self {
        Self::with_state_path(Some("session-state.sqlite3")).await
    }

    async fn with_state_path(state_path: Option<&str>) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let config_dir = dir.path().join("config");
        std::fs::create_dir(&config_dir).unwrap();
        std::fs::create_dir(dir.path().join("home")).unwrap();
        let socket = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = socket.local_addr().unwrap();
        let config = config_dir.join("bridge.toml");
        let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../core/tests/fixtures/mock_cc_persistent.py");
        let state_path = state_path.map(|path| format!("state_path = {}\n", json!(path))).unwrap_or_default();
        // JSON quoted paths are also valid TOML basic strings.
        std::fs::write(&config, format!(r#"
provider_id = "bridge-recovery"
listen = "{address}"
{state_path}
[[bot]]
provider_bot_ref = "worker"
engine = "claude-code"
cwd = {}
engine_bin = {}
"#, json!(dir.path()), json!(fixture))).unwrap();
        drop(socket);
        let mut bridge = Self {
            child: None, dir, config, url: format!("http://{address}/webhook"),
            client: Client::builder().no_proxy().timeout(DEADLINE).build().unwrap(),
        };
        bridge.launch().await;
        bridge
    }

    async fn launch(&mut self) {
        let log = std::fs::OpenOptions::new().create(true).append(true)
            .open(self.dir.path().join("bridge.log")).unwrap();
        self.child = Some(Command::new(env!("CARGO_BIN_EXE_bcs-bridge"))
            .arg("start")
            .env("BRIDGE_CONFIG", &self.config)
            .env("BCS_BRIDGE_PROVIDER_TOKENS", r#"{"bridge-recovery":"test-token"}"#)
            .env("HOME", self.dir.path().join("home"))
            .env("RUST_LOG", "warn")
            .current_dir(self.dir.path())
            .stdin(Stdio::null()).stdout(log.try_clone().unwrap()).stderr(log)
            .kill_on_drop(true).spawn().unwrap());
        let deadline = Instant::now() + DEADLINE;
        loop {
            if let Some(status) = self.child.as_mut().unwrap().try_wait().unwrap() {
                panic!("Bridge exited {status}: {}", self.log());
            }
            if let Ok(response) = self.client.post(&self.url)
                .bearer_auth("test-token").timeout(Duration::from_millis(200))
                .json(&request("ping", "bot.ping", None)).send().await
            {
                if response.status() == StatusCode::OK { break; }
            }
            assert!(Instant::now() < deadline, "Bridge did not start: {}", self.log());
            sleep(Duration::from_millis(20)).await;
        }
    }

    fn log(&self) -> String {
        std::fs::read_to_string(self.dir.path().join("bridge.log")).unwrap_or_default()
    }

    async fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            child.start_kill().unwrap();
            timeout(DEADLINE, child.wait()).await.unwrap().unwrap();
        }
    }

    async fn restart(&mut self) {
        self.stop().await;
        self.launch().await;
    }

    async fn post(&self, body: Value) -> Response {
        self.client.post(&self.url).bearer_auth("test-token")
            .header("X-BCN-Protocol-Version", "2.0")
            .json(&body).send().await.unwrap()
    }

    async fn send(&self, id: &str, prompt: &str) -> Response {
        let response = self.post(request(id, "chat.send", Some(prompt))).await;
        assert_eq!(response.status(), StatusCode::OK, "{}", self.log());
        response
    }

    async fn turn(&self, id: &str, prompt: &str) -> String {
        let text = self.send(id, prompt).await.text().await.unwrap();
        final_text(&text)
    }

    async fn inject(&self, id: &str, text: &str, sender: &str) -> Response {
        let mut body = request(id, "chat.inject", Some(text));
        body["from"] = json!({"name": sender, "bot_id": sender});
        self.post(body).await
    }

    async fn inject_ok(&self, id: &str, text: &str, sender: &str) {
        let response = self.inject(id, text, sender).await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.json::<Value>().await.unwrap()["ok"], true);
    }

    fn calls(&self) -> Vec<Value> {
        std::fs::read_to_string(self.dir.path().join("calls.jsonl"))
            .unwrap_or_default().lines()
            .map(|line| serde_json::from_str(line).unwrap()).collect()
    }

    fn release_engine(&self) {
        std::fs::write(self.dir.path().join("release-engine"), "continue").unwrap();
    }
}

#[tokio::test]
async fn home_state_paths_are_created_and_resume_after_restart() {
    for (configured, file_name) in [(None, "bridge-state.sqlite3"), (Some("~/.bcn-bridge/custom.sqlite3"), "custom.sqlite3")] {
        let mut bridge = Bridge::with_state_path(configured).await;
        let database = bridge.dir.path().join("home/.bcn-bridge").join(file_name);
        assert!(database.is_file(), "Bridge must create the database under the child process HOME: {}", database.display());
        assert!(!bridge.dir.path().join("config/bridge-state.sqlite3").exists());
        bridge.turn("first", "initial turn").await;
        let first_id = bridge.calls()[0]["session_id"].clone();
        bridge.inject_ok("observation", "saved under home", "worker").await;
        bridge.restart().await;
        assert!(bridge.turn("second", "follow up").await.contains("saved under home"));
        assert_eq!(bridge.calls()[1]["resume"], first_id);
        bridge.stop().await;
    }
}

fn request(id: &str, method: &str, text: Option<&str>) -> Value {
    let mut body = json!({"type":"req", "id":id, "method":method,
        "session_id":"durable-session",
        "to_bot":{"provider_id":"bridge-recovery", "provider_bot_ref":"worker"}});
    if let Some(text) = text {
        body["message"] = json!({"role":"user", "content":[{"type":"text", "text":text}]});
    }
    body
}

fn events(text: &str) -> impl Iterator<Item = Value> + '_ {
    text.lines().filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|line| serde_json::from_str(line).ok())
}

fn final_text(text: &str) -> String {
    assert!(!events(text).any(|event| event["state"] == "error"), "{text}");
    events(text).find(|event| event["state"] == "final")
        .and_then(|event| event["message"]["content"][0]["text"].as_str().map(str::to_string))
        .unwrap_or_else(|| panic!("missing final event: {text}"))
}

async fn wait_for_init(response: &mut Response) {
    // The marker follows init on the same NDJSON stream. Seeing it via HTTP
    // proves Bridge processed init before a crash, rather than racing the peer.
    timeout(DEADLINE, async {
        let mut text = String::new();
        loop {
            let chunk = response.chunk().await.unwrap().expect("stream ended before init marker");
            text.push_str(&String::from_utf8_lossy(&chunk));
            if events(&text).any(|event| event["deltaText"] == "__engine_initialized__") {
                break;
            }
        }
    }).await.expect("Bridge did not forward the init marker");
}

fn assert_resumed(calls: &[Value]) {
    assert_eq!(calls.len(), 2);
    assert!(calls[0]["resume"].is_null(), "first call must create a session");
    assert_eq!(calls[1]["resume"], calls[0]["session_id"],
        "Bridge must pass --resume with the previously initialized engine session");
}

#[tokio::test]
async fn completed_session_resumes_after_bridge_restart() {
    let mut bridge = Bridge::start().await;
    assert_eq!(bridge.turn("first", "first prompt").await, "first prompt");
    bridge.restart().await;
    assert_eq!(bridge.turn("second", "second prompt").await, "second prompt");
    assert_resumed(&bridge.calls());
    let database = std::fs::read(bridge.config.parent().unwrap().join("session-state.sqlite3"))
        .expect("relative state_path must be resolved against the config directory");
    assert!(database.starts_with(b"SQLite format 3\0"));
    bridge.stop().await;
}

#[tokio::test]
async fn failed_turn_preserves_init_and_retries_injects_after_restart() {
    let mut bridge = Bridge::start().await;
    bridge.inject_ok("observation", "keep this observation", "alice").await;
    let failed = bridge.send("failure", "FAIL_AFTER_INIT").await.text().await.unwrap();
    assert!(events(&failed).any(|event| event["state"] == "error"),
        "is_error:true must fail even when subtype is success: {failed}");
    bridge.restart().await;
    assert_eq!(bridge.turn("retry", "retry prompt").await,
        "[from:alice] keep this observation\n\nretry prompt");
    assert_resumed(&bridge.calls());
    assert_eq!(bridge.turn("after-retry", "clean prompt").await, "clean prompt");
    bridge.stop().await;
}

#[tokio::test]
async fn aborted_turn_preserves_init_and_queued_injects() {
    let mut bridge = Bridge::start().await;
    bridge.inject_ok("observation", "retained after abort", "alice").await;
    let mut active = bridge.send("waiting", "WAIT_AFTER_INIT").await;
    wait_for_init(&mut active).await;
    let response = bridge.post(request("abort", "chat.abort", None)).await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.json::<Value>().await.unwrap()["aborted"], true);
    let terminal = active.text().await.unwrap();
    assert!(events(&terminal).any(|event| event["state"] == "aborted"), "{terminal}");
    bridge.restart().await;
    assert_eq!(bridge.turn("retry", "continue").await,
        "[from:alice] retained after abort\n\ncontinue");
    assert_resumed(&bridge.calls());
    bridge.stop().await;
}

#[cfg(unix)]
#[tokio::test]
async fn stop_command_aborts_an_active_turn_and_exits_cleanly() {
    let mut bridge = Bridge::start().await;
    let mut active = bridge.send("active-at-stop", "WAIT_AFTER_INIT").await;
    wait_for_init(&mut active).await;
    let output = timeout(DEADLINE, Command::new(env!("CARGO_BIN_EXE_bcs-bridge"))
        .arg("stop").env("HOME", bridge.dir.path().join("home"))
        .env("BRIDGE_CONFIG", "/missing/config.toml")
        .env("BCS_BRIDGE_PROVIDER_TOKENS", "invalid-but-not-needed-to-stop")
        .kill_on_drop(true).output()).await.unwrap().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let terminal = active.text().await.unwrap();
    assert!(events(&terminal).any(|event| event["state"] == "aborted"), "{terminal}");
    let mut child = bridge.child.take().unwrap();
    assert!(timeout(DEADLINE, child.wait()).await.unwrap().unwrap().success());
    assert!(!bridge.dir.path().join("home/.bcn-bridge/bridge.pid.json").exists());
}

#[tokio::test]
async fn inject_ack_and_deduplication_survive_restart_and_delivery() {
    let mut bridge = Bridge::start().await;
    bridge.inject_ok("observe-1", "first observation", "alice").await;
    bridge.inject_ok("observe-2", "second observation", "bob").await;
    assert!(bridge.calls().is_empty(), "inject must never start the engine");
    bridge.restart().await;
    assert_eq!(bridge.inject("observe-1", "changed body", "alice").await.status(), StatusCode::CONFLICT);
    assert_eq!(bridge.inject("observe-1", "first observation", "mallory").await.status(), StatusCode::CONFLICT);
    let mut changed_sender = request("observe-1", "chat.inject", Some("first observation"));
    changed_sender["from"] = json!({"name":"alice", "bot_id":"another-bot"});
    assert_eq!(bridge.post(changed_sender).await.status(), StatusCode::CONFLICT);
    bridge.inject_ok("observe-1", "first observation", "alice").await;
    assert!(bridge.calls().is_empty(), "inject replay must never start the engine");
    assert_eq!(bridge.turn("deliver", "answer now").await,
        "[from:alice] first observation\n[from:bob] second observation\n\nanswer now");
    bridge.restart().await;
    bridge.inject_ok("observe-2", "second observation", "bob").await;
    assert_eq!(bridge.turn("clean", "only this prompt").await, "only this prompt");
    assert_eq!(bridge.calls().len(), 2);
    bridge.stop().await;
}

#[tokio::test]
async fn injects_during_and_after_turn_are_delivered_only_on_the_next_turn() {
    let mut bridge = Bridge::start().await;
    let mut active = bridge.send("active", "WAIT_AFTER_INIT").await;
    wait_for_init(&mut active).await;
    bridge.inject_ok("concurrent-inject", "arrived during the run", "alice").await;
    assert_eq!(bridge.calls().len(), 1);
    bridge.release_engine();
    assert_eq!(final_text(&active.text().await.unwrap()), "WAIT_AFTER_INIT");
    bridge.inject_ok("after-turn-inject", "arrived after the run", "bob").await;
    assert_eq!(bridge.calls().len(), 1);
    assert_eq!(bridge.turn("next", "new prompt").await,
        "[from:alice] arrived during the run\n[from:bob] arrived after the run\n\nnew prompt");
    assert_eq!(bridge.turn("last", "last prompt").await, "last prompt");
    bridge.stop().await;
}

#[tokio::test]
async fn crash_after_init_recovers_session_and_inflight_injects() {
    let mut bridge = Bridge::start().await;
    bridge.inject_ok("observe-before-crash", "recover this observation", "alice").await;
    let mut active = bridge.send("crashed", "WAIT_AFTER_INIT").await;
    wait_for_init(&mut active).await;
    // SIGKILL, not graceful shutdown: no run finalization can requeue messages.
    bridge.stop().await;
    drop(active);
    bridge.launch().await;
    assert_eq!(bridge.turn("recovered", "continue after crash").await,
        "[from:alice] recover this observation\n\ncontinue after crash");
    assert_resumed(&bridge.calls());
    assert_eq!(bridge.turn("last", "last prompt").await, "last prompt");
    bridge.stop().await;
}

#[tokio::test]
async fn failed_inject_commit_returns_503_and_same_id_can_retry() {
    let mut bridge = Bridge::start().await;
    let database = rusqlite::Connection::open(
        bridge.config.parent().unwrap().join("session-state.sqlite3")).unwrap();
    database.busy_timeout(Duration::from_secs(2)).unwrap();
    database.execute_batch("
        CREATE TRIGGER fail_inject_insert BEFORE INSERT ON injects
        BEGIN SELECT RAISE(FAIL, 'synthetic inject write failure'); END;
    ").unwrap();

    let rejected = bridge.inject("retryable-inject", "commit this observation", "alice").await;
    assert_eq!(rejected.status(), StatusCode::SERVICE_UNAVAILABLE);
    let error = rejected.json::<Value>().await.unwrap();
    assert_eq!(error["ok"], false);
    assert_eq!(error["error"]["retryable"], true);
    assert!(bridge.calls().is_empty(), "a failed inject cannot invoke the engine");

    database.execute_batch("DROP TRIGGER fail_inject_insert;").unwrap();
    bridge.inject_ok("retryable-inject", "commit this observation", "alice").await;
    bridge.inject_ok("retryable-inject", "commit this observation", "alice").await;
    assert!(bridge.calls().is_empty(), "retrying inject must only commit its receipt");
    assert_eq!(bridge.turn("deliver", "answer now").await,
        "[from:alice] commit this observation\n\nanswer now");
    assert_eq!(bridge.turn("last", "clean prompt").await, "clean prompt");
    bridge.stop().await;
}

#[tokio::test]
async fn failed_delivery_commit_emits_error_and_preserves_the_batch_for_retry() {
    let mut bridge = Bridge::start().await;
    bridge.inject_ok("observation", "keep until delivery commits", "alice").await;
    bridge.inject_ok("later-observation", "keep the entire batch", "bob").await;
    let database = rusqlite::Connection::open(
        bridge.config.parent().unwrap().join("session-state.sqlite3")).unwrap();
    database.busy_timeout(Duration::from_secs(2)).unwrap();
    // Fail after the first row changes to catch partially committed batches.
    database.execute_batch("
        CREATE TRIGGER fail_delivery_update BEFORE UPDATE OF state ON injects
        WHEN NEW.state = 'delivered' AND OLD.id = 'later-observation'
        BEGIN SELECT RAISE(FAIL, 'synthetic delivery write failure'); END;
    ").unwrap();

    let failed = bridge.send("failed-delivery", "first attempt").await.text().await.unwrap();
    assert!(events(&failed).any(|event| event["state"] == "error"),
        "a failed delivery commit must be visible to the caller: {failed}");
    assert!(!events(&failed).any(|event| event["state"] == "final"),
        "Bridge must commit delivery before reporting a successful turn: {failed}");

    database.execute_batch("DROP TRIGGER fail_delivery_update;").unwrap();
    assert_eq!(bridge.turn("retry", "second attempt").await,
        "[from:alice] keep until delivery commits\n[from:bob] keep the entire batch\n\nsecond attempt");
    assert_resumed(&bridge.calls());
    assert_eq!(bridge.turn("last", "clean prompt").await, "clean prompt");
    bridge.stop().await;
}
