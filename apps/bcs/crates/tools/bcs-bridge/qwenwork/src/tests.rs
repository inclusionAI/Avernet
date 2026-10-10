#![allow(clippy::unwrap_used, clippy::expect_used)]
use super::*;
use bcs_bridge_core::engine::{SessionObserver, TurnError, TurnRequest};
use bcs_protocol::stream::{AgentData, StreamEvent, ToolPhase};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{sync::Mutex, time::Duration};
use tokio_tungstenite::{connect_async, tungstenite::Message, MaybeTlsStream, WebSocketStream};

type Socket = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;
#[derive(Default)]
struct Observer(Mutex<Vec<String>>);
#[async_trait::async_trait]
impl SessionObserver for Observer {
    async fn established(&self, session: &str) -> Result<(), String> {
        self.0.lock().unwrap().push(session.into());
        Ok(())
    }
}

struct Fixture {
    dir: tempfile::TempDir,
    address: SocketAddr,
    engine: Arc<dyn Engine>,
}
impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let db = rusqlite::Connection::open(dir.path().join("qwen.db")).unwrap();
        db.execute_batch(
            "CREATE TABLE chats(id TEXT PRIMARY KEY, ext TEXT, deleted_at INTEGER);
            CREATE TABLE sub_chats(id TEXT PRIMARY KEY, chat_id TEXT, session_id TEXT);",
        )
        .unwrap();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        drop(listener);
        let options: toml::Table = toml::from_str(&format!(
            "listen = '{address}'\nbot_id = 'test-bot'\nsecret = 'test-secret'\nhook_secret = 'test-hooks'\ndatabase_path = '{}'",
            dir.path().join("qwen.db").display())).unwrap();
        let engine = QwenWorkFactory.build(PathBuf::new(), &options).unwrap();
        Self {
            dir,
            address,
            engine,
        }
    }
    async fn connect(&self, secret: &str) -> (Socket, Value) {
        let (mut socket, _) = connect_async(format!("ws://{}/ws", self.address))
            .await
            .unwrap();
        socket
            .send(Message::Text(
                json!({"cmd":"aibot_subscribe","headers":{"req_id":"auth"},
            "body":{"bot_id":"test-bot","secret":secret}})
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
        let ack = frame(&mut socket).await;
        (socket, ack)
    }
    fn correlate(&self, conversation: &str, session: &str) {
        let db = rusqlite::Connection::open(self.dir.path().join("qwen.db")).unwrap();
        let ext = json!({"channelPlatform":"wecom-bot","imConversationId":format!("wecom-bot:test-bot:{conversation}")}).to_string();
        db.execute("INSERT INTO chats VALUES(?1,?2,NULL)", [session, &ext])
            .unwrap();
        db.execute("INSERT INTO sub_chats VALUES(?1,?1,?1)", [session])
            .unwrap();
    }
    async fn raw_hook(&self, value: Value, secret: &str) -> reqwest::Response {
        reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(3)).build().unwrap()
            .post(format!("http://{}/hooks", self.address)).bearer_auth(secret)
            .json(&value).send().await.unwrap()
    }
    async fn begin(&self, session: &str, query: &str, prompt: &str) {
        assert!(self.raw_hook(json!({"session_id":session,"hook_event_name":"UserPromptSubmit",
            "request_set_id":query,"prompt":prompt}), "test-hooks").await.status().is_success());
    }
    async fn end(&self, session: &str, query: &str) -> reqwest::Response {
        self.raw_hook(json!({"session_id":session,"hook_event_name":"QueryEnd",
            "request_set_id":query,"reason":"end_turn"}), "test-hooks").await
    }
    async fn hook(&self, session: &str, name: &str, secret: &str) -> reqwest::Response {
        let response = self.raw_hook(json!({"session_id":session,"hook_event_name":name,
            "tool_use_id":"tool-1","tool_name":"Read","tool_input":{"path":"example.txt"},
            "tool_response":{"result":{"answer":"中文输出"}}}), secret).await;
        if name == "Stop" && secret == "test-hooks" {
            self.begin(session, "main-request", "hello").await;
            return self.end(session, "main-request").await;
        }
        response
    }
    fn turn(
        &self,
        id: &str,
        session: Option<String>,
        observer: Arc<Observer>,
        abort: CancellationToken,
    ) -> (
        tokio::task::JoinHandle<Result<bcs_bridge_core::engine::TurnOutcome, TurnError>>,
        mpsc::Receiver<StreamEvent>,
    ) {
        let engine = self.engine.clone();
        let mut req = TurnRequest::new(id, "hello", self.dir.path(), observer);
        req.engine_session_id = session;
        let (tx, rx) = mpsc::channel(32);
        (
            tokio::spawn(async move { engine.run_turn(req, tx, abort).await }),
            rx,
        )
    }
}

async fn frame(socket: &mut Socket) -> Value {
    let message = tokio::time::timeout(Duration::from_secs(2), socket.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    serde_json::from_str(message.to_text().unwrap()).unwrap()
}

async fn reply(socket: &mut Socket, request: &str, text: &str, finish: bool) {
    socket
        .send(Message::Text(
            json!({"cmd":"aibot_respond_msg","headers":{"req_id":request},
        "body":{"stream":{"content":text,"finish":finish}}})
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    assert_eq!(frame(socket).await["errcode"], 0);
}

#[tokio::test]
async fn native_turn_preserves_tools_waits_for_stop_and_resumes_conversation() {
    let f = Fixture::new();
    let (mut socket, ack) = f.connect("test-secret").await;
    assert_eq!(ack["errcode"], 0);
    let observer = Arc::new(Observer::default());
    let (task, mut events) = f.turn("run-1", None, observer.clone(), CancellationToken::new());
    let request = frame(&mut socket).await;
    let id = request["headers"]["req_id"].as_str().unwrap();
    let conversation = request["body"]["chatid"].as_str().unwrap();
    assert_eq!(request["body"]["text"]["content"], "hello"); // no body correlation marker
    assert_eq!(observer.0.lock().unwrap().as_slice(), [conversation]);
    f.correlate(conversation, "session-1");
    assert_eq!(
        f.hook("session-1", "PreToolUse", "wrong").await.status(),
        401
    );
    assert!(f
        .hook("unrelated", "PreToolUse", "test-hooks")
        .await
        .status()
        .is_success());
    reply(&mut socket, id, "你", false).await;
    // Out-of-order and duplicate HTTP deliveries still yield exactly one start/result pair.
    assert!(f
        .hook("session-1", "PostToolUse", "test-hooks")
        .await
        .status()
        .is_success());
    assert!(f
        .hook("session-1", "PreToolUse", "test-hooks")
        .await
        .status()
        .is_success());
    assert!(f
        .hook("session-1", "PreToolUse", "test-hooks")
        .await
        .status()
        .is_success());
    reply(&mut socket, id, "你好", true).await;
    assert!(
        !task.is_finished(),
        "text finish must not discard pending Hooks"
    );
    let hook = f.hook("session-1", "Stop", "test-hooks").await;
    assert_eq!(
        hook.json::<Value>().await.unwrap(),
        json!({"continue":true})
    );
    let outcome = task.await.unwrap().unwrap();
    assert_eq!(outcome.final_text.as_deref(), Some("你好"));
    let mut deltas = String::new();
    let mut tools = Vec::new();
    while let Some(event) = events.recv().await {
        match event {
            StreamEvent::Chat(c) => deltas.push_str(c.delta_text.as_deref().unwrap()),
            StreamEvent::Agent(a) => {
                if let AgentData::Tool(t) = a.data {
                    tools.push(t);
                }
            }
            _ => panic!("unexpected engine event"),
        }
    }
    assert_eq!(deltas, "你好");
    assert_eq!(tools.len(), 2);
    assert_eq!(tools[0].phase, ToolPhase::Start);
    assert_eq!(tools[0].args, Some(json!({"path":"example.txt"})));
    assert_eq!(tools[1].phase, ToolPhase::Result);
    assert_eq!(tools[1].result, Some(json!({"answer":"中文输出"})));
    assert_eq!(tools[0].tool_call_id, tools[1].tool_call_id);
    let (second, _events) = f.turn(
        "run-2",
        outcome.engine_session_id,
        observer,
        CancellationToken::new(),
    );
    let request = frame(&mut socket).await;
    assert_eq!(request["body"]["chatid"], conversation);
    assert!(f
        .hook("session-1", "Stop", "test-hooks")
        .await
        .status()
        .is_success());
    reply(
        &mut socket,
        request["headers"]["req_id"].as_str().unwrap(),
        "again",
        true,
    )
    .await;
    assert_eq!(
        second.await.unwrap().unwrap().final_text.as_deref(),
        Some("again")
    );
}

#[tokio::test]
async fn abort_sends_stop_to_the_same_conversation_and_waits_for_confirmation() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let abort = CancellationToken::new();
    let (task, _events) = f.turn("run", None, Arc::new(Observer::default()), abort.clone());
    let initial = frame(&mut socket).await;
    abort.cancel();
    let stop = frame(&mut socket).await;
    assert_eq!(stop["body"]["chatid"], initial["body"]["chatid"]);
    assert_eq!(stop["body"]["text"]["content"], "/stop");
    assert_ne!(stop["headers"]["req_id"], initial["headers"]["req_id"]);
    assert!(!task.is_finished());
    reply(
        &mut socket,
        stop["headers"]["req_id"].as_str().unwrap(),
        "✅ 已停止，当前任务已终止。",
        true,
    )
    .await;
    assert!(matches!(task.await.unwrap(), Err(TurnError::Aborted)));
}

#[tokio::test]
async fn auth_and_duplicate_socket_rejection_preserve_the_existing_connection() {
    let f = Fixture::new();
    let (_, ack) = f.connect("wrong").await;
    assert_eq!(ack["errcode"], 1);
    let (mut original, ack) = f.connect("test-secret").await;
    assert_eq!(ack["errcode"], 0);
    let (_, ack) = f.connect("test-secret").await;
    assert_eq!(ack["errcode"], 1);
    original
        .send(Message::Text(
            json!({"cmd":"ping","headers":{"req_id":"p"}})
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
    assert_eq!(frame(&mut original).await["errcode"], 0);
}

#[tokio::test]
async fn disconnect_after_dispatch_fences_new_turns_instead_of_claiming_cancellation() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, _events) = f.turn(
        "run",
        None,
        Arc::new(Observer::default()),
        CancellationToken::new(),
    );
    frame(&mut socket).await;
    socket.close(None).await.unwrap();
    let error = task.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("cancellation unconfirmed"), "{error}");
    let (second, _) = f.turn(
        "next",
        None,
        Arc::new(Observer::default()),
        CancellationToken::new(),
    );
    assert!(second
        .await
        .unwrap()
        .unwrap_err()
        .to_string()
        .contains("execution state uncertain"));
}

#[tokio::test]
async fn observer_failure_prevents_prompt_dispatch() {
    struct Failing;
    #[async_trait::async_trait]
    impl SessionObserver for Failing {
        async fn established(&self, _: &str) -> Result<(), String> {
            Err("disk full".into())
        }
    }
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (tx, _) = mpsc::channel(32);
    let result = f
        .engine
        .run_turn(
            TurnRequest::new("r", "hi", ".", Arc::new(Failing)),
            tx,
            CancellationToken::new(),
        )
        .await;
    assert!(matches!(result, Err(TurnError::SessionStorage(_))));
    assert!(
        tokio::time::timeout(Duration::from_millis(50), socket.next())
            .await
            .is_err()
    );
}

#[test]
fn configuration_and_factory_contract_reject_unsupported_inputs() {
    assert!(!QwenWorkFactory.requires_bin());
    assert!(QwenWorkFactory
        .build(PathBuf::from("python"), &toml::Table::new())
        .is_err());
    assert!(QwenWorkFactory
        .build(PathBuf::new(), &toml::Table::new())
        .is_err());
}

#[tokio::test]
async fn bridge_runtime_invokes_native_engine_and_emits_one_terminal() {
    use bcs_bridge_core::{
        config::ProviderConfig,
        engine::EngineRegistry,
        runtime::{self, AppState, RuntimeReply},
        sse,
    };
    struct Existing(Arc<dyn Engine>);
    impl EngineFactory for Existing {
        fn id(&self) -> &str {
            "qwenwork"
        }
        fn default_bin(&self) -> &str {
            ""
        }
        fn requires_bin(&self) -> bool {
            false
        }
        fn build(&self, _: PathBuf, _: &toml::Table) -> Result<Arc<dyn Engine>, String> {
            Ok(self.0.clone())
        }
    }
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let config = ProviderConfig::from_toml(&format!(
        "provider_id='local'\nlisten='127.0.0.1:1'\nbcs_to_provider_token='test'\nstate_path='state.db'\n[[bot]]\nprovider_bot_ref='qwen'\nengine='qwenwork'\ncwd='{}'",
        f.dir.path().display()), &f.dir.path().join("bridge.toml")).unwrap();
    let mut engines = EngineRegistry::new();
    engines.register(Existing(f.engine.clone()));
    let state = Arc::new(AppState::new(config, &engines).unwrap());
    let req = serde_json::from_value(
        json!({"id":"bridge-run","method":"chat.send","session_id":"bcs-session",
        "to_bot":{"provider_id":"local","provider_bot_ref":"qwen"},
        "message":{"role":"user","content":[{"type":"text","text":"hi"}]}}),
    )
    .unwrap();
    let RuntimeReply::Run { mut stream, .. } = runtime::dispatch(state.clone(), req).await.unwrap()
    else {
        panic!("run missing");
    };
    let request = frame(&mut socket).await;
    assert_eq!(request["body"]["text"]["content"], "hi");
    let conversation = request["body"]["chatid"].as_str().unwrap();
    f.correlate(conversation, "bridge-native-session");
    f.begin("bridge-native-session", "main-request", "hi").await;
    f.hook("bridge-native-session", "PreToolUse", "test-hooks")
        .await;
    reply(
        &mut socket,
        request["headers"]["req_id"].as_str().unwrap(),
        "done",
        true,
    )
    .await;
    f.hook("bridge-native-session", "PostToolUse", "test-hooks")
        .await;
    f.hook("bridge-native-session", "Stop", "test-hooks").await;
    let mut wire = String::new();
    while let Some(event) = tokio::time::timeout(Duration::from_secs(2), stream.next())
        .await
        .unwrap()
    {
        wire.push_str(
            &sse::event_to_frame(&event.event, event.seq, event.ts, &event.run_id).unwrap(),
        );
    }
    assert_eq!(wire.matches("\"state\":\"final\"").count(), 1);
    assert!(wire.contains("\"deltaText\":\"done\""));
    assert!(wire.contains("\"toolCallId\":\"tool-1\""));
    assert!(wire.contains("中文输出"));
    // Reopen the bridge-owned store: the mapping was durably acknowledged before dispatch.
    state.runs.abort_all("test teardown").await;
    let config = state.config.clone();
    drop(stream);
    drop(state);
    let reopened = bcs_bridge_core::session::SessionStore::open(
        &config.state_path,
        &config.provider_id,
        &config.bots,
    )
    .unwrap();
    assert_eq!(
        reopened
            .mapping("qwen", "bcs-session")
            .await
            .unwrap()
            .engine_session_id
            .as_deref(),
        Some(conversation)
    );
}

#[test]
fn bot_debug_redacts_engine_option_values() {
    let mut options = toml::Table::new();
    options.insert("secret".into(), "sensitive-value".into());
    let bot = bcs_bridge_core::config::BotConfig {
        provider_bot_ref: "qwen".into(),
        bot_id: None,
        token: None,
        engine: "qwenwork".into(),
        cwd: ".".into(),
        model: None,
        permission_mode: None,
        engine_bin: None,
        engine_options: options,
    };
    assert!(!format!("{bot:?}").contains("sensitive-value"));
}

#[tokio::test]
async fn snapshot_revisions_use_authoritative_final_without_duplicated_deltas() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, mut events) = f.turn(
        "revision",
        None,
        Arc::new(Observer::default()),
        CancellationToken::new(),
    );
    let request = frame(&mut socket).await;
    let id = request["headers"]["req_id"].as_str().unwrap();
    f.correlate(
        request["body"]["chatid"].as_str().unwrap(),
        "revision-session",
    );
    reply(&mut socket, id, "first", false).await;
    reply(&mut socket, id, "revised", false).await;
    reply(&mut socket, id, "revised final", true).await;
    f.hook("revision-session", "Stop", "test-hooks").await;
    assert_eq!(
        task.await.unwrap().unwrap().final_text.as_deref(),
        Some("revised final")
    );
    let mut deltas = String::new();
    while let Some(event) = events.recv().await {
        if let StreamEvent::Chat(c) = event {
            deltas.push_str(c.delta_text.as_deref().unwrap());
        }
    }
    assert_eq!(deltas, "first");
}

#[tokio::test]
async fn cached_hooks_do_not_query_the_database_again_within_a_turn() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, _events) = f.turn(
        "cache",
        None,
        Arc::new(Observer::default()),
        CancellationToken::new(),
    );
    let request = frame(&mut socket).await;
    f.correlate(
        request["body"]["chatid"].as_str().unwrap(),
        "cached-session",
    );
    f.hook("cached-session", "PreToolUse", "test-hooks").await;
    // Removing the lookup table exposes any accidental per-tool database read.
    rusqlite::Connection::open(f.dir.path().join("qwen.db"))
        .unwrap()
        .execute_batch("DROP TABLE sub_chats")
        .unwrap();
    assert!(f
        .hook("cached-session", "PostToolUse", "test-hooks")
        .await
        .status()
        .is_success());
    assert!(f
        .hook("cached-session", "Stop", "test-hooks")
        .await
        .status()
        .is_success());
    reply(
        &mut socket,
        request["headers"]["req_id"].as_str().unwrap(),
        "done",
        true,
    )
    .await;
    assert!(task.await.unwrap().is_ok());
}

#[tokio::test]
async fn query_end_without_text_final_fails_and_stops_the_desktop_task() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, _events) = f.turn(
        "incomplete",
        None,
        Arc::new(Observer::default()),
        CancellationToken::new(),
    );
    let request = frame(&mut socket).await;
    f.correlate(request["body"]["chatid"].as_str().unwrap(), "incomplete-session");
    f.begin("incomplete-session", "main-request", "hello").await;
    f.end("incomplete-session", "main-request").await;
    reply(
        &mut socket,
        request["headers"]["req_id"].as_str().unwrap(),
        "partial",
        false,
    )
    .await;
    let stop = tokio::time::timeout(Duration::from_secs(12), socket.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let stop: Value = serde_json::from_str(stop.to_text().unwrap()).unwrap();
    assert_eq!(stop["body"]["text"]["content"], "/stop");
    reply(
        &mut socket,
        stop["headers"]["req_id"].as_str().unwrap(),
        "ℹ️ 当前没有正在执行或排队的任务。",
        true,
    )
    .await;
    assert!(task
        .await
        .unwrap()
        .unwrap_err()
        .to_string()
        .contains("completion incomplete"));
}

#[tokio::test]
async fn hook_queue_overflow_fences_execution_instead_of_losing_tools_silently() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (tx, _receiver) = mpsc::channel(1); // downstream never drains tool events
    let req = TurnRequest::new(
        "overflow",
        "hello",
        f.dir.path(),
        Arc::new(Observer::default()),
    );
    let engine = f.engine.clone();
    let task =
        tokio::spawn(async move { engine.run_turn(req, tx, CancellationToken::new()).await });
    let request = frame(&mut socket).await;
    f.correlate(
        request["body"]["chatid"].as_str().unwrap(),
        "overflow-session",
    );
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    let mut rejected = false;
    for i in 0..QUEUE_SIZE + 4 {
        let response = client
            .post(format!("http://{}/hooks", f.address))
            .bearer_auth("test-hooks")
            .json(
                &json!({"session_id":"overflow-session","hook_event_name":"PreToolUse",
                "tool_use_id":format!("tool-{i}"),"tool_name":"Read","tool_input":{}}),
            )
            .send()
            .await
            .unwrap();
        if response.status() == 503 {
            rejected = true;
            break;
        }
        let _: Value = response.json().await.unwrap();
    }
    assert!(rejected);
    let error = tokio::time::timeout(Duration::from_secs(2), task)
        .await
        .unwrap()
        .unwrap()
        .unwrap_err();
    assert!(error.to_string().contains("cancellation unconfirmed"));
    let health: Value = client
        .get(format!("http://{}/health", f.address))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(health["faulted"], true);
}

#[tokio::test]
async fn compact_and_advisory_stop_keep_the_same_run_until_matching_query_end() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, _events) = f.turn("compact", None, Arc::new(Observer::default()), CancellationToken::new());
    let request = frame(&mut socket).await;
    let id = request["headers"]["req_id"].as_str().unwrap();
    let conversation = request["body"]["chatid"].as_str().unwrap();
    f.correlate(conversation, "compact-session");
    f.begin("compact-session", "current-query", "hello").await;
    for event in ["PreCompact", "Stop"] {
        f.raw_hook(json!({"session_id":"compact-session","hook_event_name":event}), "test-hooks").await;
    }
    // Compact can outlast the former Stop-based ten-second timer.
    tokio::time::sleep(Duration::from_secs(11)).await;
    assert!(!task.is_finished());
    f.raw_hook(json!({"session_id":"compact-session","hook_event_name":"PostCompact"}), "test-hooks").await;
    reply(&mut socket, id, "after compact", true).await;
    // Even a final snapshot and Stop do not imply main-request completion.
    f.end("compact-session", "previous-query").await;
    f.raw_hook(json!({"session_id":"compact-session","hook_event_name":"QueryEnd",
        "request_set_id":"current-query","reason":"end_turn","agent_id":"compact-fork"}), "test-hooks").await;
    tokio::time::sleep(Duration::from_millis(30)).await;
    assert!(!task.is_finished());
    f.end("compact-session", "current-query").await;
    let outcome = task.await.unwrap().unwrap();
    assert_eq!(outcome.engine_session_id.as_deref(), Some(conversation));
    assert_eq!(outcome.final_text.as_deref(), Some("after compact"));
}

#[tokio::test]
async fn query_end_does_not_hide_missing_tool_results_or_failed_main_requests() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, _events) = f.turn("failed", None, Arc::new(Observer::default()), CancellationToken::new());
    let request = frame(&mut socket).await;
    f.correlate(request["body"]["chatid"].as_str().unwrap(), "failed-session");
    f.begin("failed-session", "query", "hello").await;
    f.hook("failed-session", "PreToolUse", "test-hooks").await;
    reply(&mut socket, request["headers"]["req_id"].as_str().unwrap(), "partial", true).await;
    f.end("failed-session", "query").await;
    tokio::time::sleep(Duration::from_millis(30)).await;
    assert!(!task.is_finished());
    f.raw_hook(json!({"session_id":"failed-session","hook_event_name":"QueryEnd",
        "request_set_id":"query","reason":"error"}), "test-hooks").await;
    let stop = frame(&mut socket).await;
    assert_eq!(stop["body"]["text"]["content"], "/stop");
    reply(&mut socket, stop["headers"]["req_id"].as_str().unwrap(), "ℹ stopped", true).await;
    assert!(task.await.unwrap().unwrap_err().to_string().contains("ended with error"));
}
