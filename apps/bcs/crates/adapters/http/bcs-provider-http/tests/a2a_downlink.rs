//! BCS A2A downlink end-to-end: `HttpProviderTransport` against a real
//! bcs-bridge A2A server (mock engines) and against minimal third-party agents.
use std::{collections::HashMap, sync::{Arc, Mutex as StdMutex}, time::Duration};

use async_trait::async_trait;
use axum::{extract::State, http::HeaderMap, response::{IntoResponse, Response}, routing::{get, post}, Json, Router};
use bcs_domain::{BotDeliveryTarget, RedactedToken, PROVIDER_PROTOCOL_A2A};
use bcs_protocol::{BcsFrame, BotDeliveryKind, RequestFrame};
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::*;
use serde_json::{json, Value};
use tokio::sync::RwLock;

const SESSION: &str = "group-1:feedbeef";

// ---------------------------------------------------------------- fakes

#[derive(Default)]
struct Flow { events: StdMutex<Vec<BotEventCommand>> }

impl Flow {
    fn states(&self) -> Vec<String> {
        self.events.lock().unwrap().iter().map(|e| {
            let text = e.event_payload.get("delta_text").or_else(|| e.event_payload.get("deltaText")).or_else(|| e.event_payload.pointer("/message/content/0/text"))
                .or_else(|| e.event_payload.get("errorMessage")).and_then(Value::as_str).unwrap_or("");
            format!("{}:{:?}:{text}", e.event_type, e.state)
        }).collect()
    }

    async fn wait_terminal(&self) -> Vec<String> {
        for _ in 0..200 {
            let states = self.states();
            if states.iter().any(|s| s.contains(":Final:") || s.contains(":Error:") || s.contains(":Aborted:")) { return states; }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        panic!("no terminal event: {:?}", self.states());
    }
}

#[async_trait]
impl MessageFlowService for Flow {
    async fn handle_web_send(&self, _: WebSendCommand) -> ServiceResult<WebSendOutcome> { unreachable!() }
    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        self.events.lock().unwrap().push(cmd);
        Ok(BotEventOutcome { bot_deliveries: Vec::new(), frontend_deliveries: Vec::new(), unregistered_run_ids: Vec::new(),
            mentions: Vec::new(), delivered_count: 1, failed_count: 0, delivery_results: Vec::new() })
    }
    async fn ingest_provider_event(&self, cmd: ProviderEventIngestCommand) -> ServiceResult<BotEventOutcome> { self.handle_bot_event(cmd.event).await }
    async fn handle_group_callback(&self, _: GroupCallbackCommand) -> ServiceResult<GroupCallbackOutcome> { unreachable!() }
    async fn handle_chat_abort(&self, _: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> { unreachable!() }
    async fn register_task_run_alias(&self, _: &str, _: &str, _: &str) -> ServiceResult<TaskRunAliasRegistration> { unreachable!() }
    async fn handle_task_dispatch(&self, _: TaskDispatchCommand) -> ServiceResult<TaskDispatchOutcome> { unreachable!() }
    async fn handle_task_complete(&self, _: TaskCompleteCommand) -> ServiceResult<TaskCompleteOutcome> { unreachable!() }
}

#[async_trait]
impl ProviderEventIngestService for Flow {
    async fn ingest_provider_event(&self, cmd: ProviderEventIngestCommand) -> ServiceResult<BotEventOutcome> {
        MessageFlowService::ingest_provider_event(self, cmd).await
    }
}

#[derive(Default)]
struct Runs { contexts: RwLock<HashMap<String, BotRunContext>>, transports: RwLock<HashMap<String, ProviderRunTransport>> }

#[async_trait]
impl BotRunContextPort for Runs {
    async fn put_context(&self, context: BotRunContext) { self.contexts.write().await.insert(context.run_id.clone(), context); }
    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> { self.contexts.read().await.get(run_id).cloned() }
    async fn try_begin_terminal(&self, _: &str) -> bool { true }
    async fn mark_terminal(&self, _: &str) -> bool { true }
    async fn release_terminal(&self, _: &str) {}
    async fn begin_provider_transport(&self, run_id: &str, _: u64) -> bool {
        self.transports.write().await.insert(run_id.into(), ProviderRunTransport::Negotiating).is_none()
    }
    async fn bind_provider_transport(&self, run_id: &str, transport: ProviderRunTransport) -> bool {
        self.transports.write().await.insert(run_id.into(), transport); true
    }
    async fn get_provider_transport(&self, run_id: &str) -> Option<ProviderRunTransport> { self.transports.read().await.get(run_id).copied() }
    async fn mark_provider_transport_terminal(&self, run_id: &str) { self.transports.write().await.insert(run_id.into(), ProviderRunTransport::Terminal); }
    async fn clear_provider_transport(&self, run_id: &str) { self.transports.write().await.remove(run_id); }
}

#[derive(Default)]
struct Interactions { requested: StdMutex<Vec<ProviderInteractionRequestedCommand>>, resolved: StdMutex<Vec<ProviderInteractionResolvedCommand>> }

#[async_trait]
impl InteractionService for Interactions {
    async fn on_provider_requested(&self, command: ProviderInteractionRequestedCommand) -> ServiceResult<InteractionRequestedOutcome> {
        self.requested.lock().unwrap().push(command); Ok(InteractionRequestedOutcome::Stored)
    }
    async fn on_provider_resolved(&self, command: ProviderInteractionResolvedCommand) -> ServiceResult<()> {
        self.resolved.lock().unwrap().push(command); Ok(())
    }
    async fn resolve(&self, _: ResolveInteractionCommand) -> Result<ResolveInteractionResult, InteractionServiceError> { unreachable!() }
    async fn list_pending(&self, _: &str) -> ServiceResult<Vec<InteractionFrontendEvent>> { Ok(Vec::new()) }
    async fn invalidate_run(&self, _: &str, _: &str, _: u64) -> ServiceResult<usize> { Ok(0) }
    async fn cleanup_terminal(&self, _: u64) -> ServiceResult<usize> { Ok(0) }
}

struct Harness { transport: HttpProviderTransport, flow: Arc<Flow>, runs: Arc<Runs>, interactions: Arc<Interactions>, target: BotDeliveryTarget }

impl Harness {
    fn new(base_url: String, token: &str) -> Self {
        let transport = HttpProviderTransport::allowing_private_networks_for_tests();
        let (flow, runs, interactions) = (Arc::new(Flow::default()), Arc::new(Runs::default()), Arc::new(Interactions::default()));
        transport.set_ingest(flow.clone(), runs.clone());
        transport.set_interactions(interactions.clone());
        let target = BotDeliveryTarget::HttpProvider { bot_id: "bot-a2a".into(), provider_id: "provider-1".into(),
            provider_bot_ref: "worker-1".into(), webhook_url: base_url, bcs_to_provider_token: RedactedToken::new(token),
            protocol_version: PROVIDER_PROTOCOL_A2A.into() };
        Self { transport, flow, runs, interactions, target }
    }

    async fn send(&self, run_id: &str, text: &str) -> BotDeliveryResult {
        self.runs.put_context(BotRunContext { run_id: run_id.into(), bot_id: "bot-a2a".into(), group_id: "group-1".into(),
            bcs_session_id: Some(SESSION.into()), deadline_ms: bcs_protocol::now_ms() + 60_000, terminal: false }).await;
        self.transport.deliver(BotDeliveryCommand { target: self.target.clone(), run_id: run_id.into(),
            frame: BcsFrame::Request(RequestFrame::new(format!("attempt-{run_id}"), "chat.send",
                Some(json!({"bcs_session_id": SESSION, "bcs_group_id": "group-1", "message": {"text": text}})))),
            delivery_kind: BotDeliveryKind::Send, provider_transport: Default::default(), provider_bypass_headers: Vec::new() }).await.unwrap()
    }

    async fn wait_requested(&self) -> ProviderInteractionRequestedCommand {
        for _ in 0..200 {
            if let Some(found) = self.interactions.requested.lock().unwrap().first().cloned() { return found; }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        panic!("no interaction requested: {:?}", self.flow.states());
    }

    async fn resolve(&self, requested: &ProviderInteractionRequestedCommand, resolution: Value) -> InteractionProviderAck {
        self.transport.resolve_interaction(InteractionProviderCommand { target: self.target.clone(), provider_bypass_headers: Vec::new(),
            bcs_run_id: requested.bcs_run_id.clone(), provider_run_id: requested.provider_run_id.clone(), bcs_session_id: SESSION.into(),
            group_id: "group-1".into(), bot_id: "bot-a2a".into(), interaction_id: requested.interaction_id.clone(), kind: requested.kind,
            idempotency_key: format!("idem-{}", requested.interaction_id), resolution }).await.unwrap()
    }
}

// ---------------------------------------------------------------- real bcs-bridge

struct Bridge { base: String, _dir: tempfile::TempDir }

async fn spawn_bridge(script: &str) -> Bridge {
    let dir = tempfile::tempdir().unwrap();
    let bin = format!("{}/../../../tools/bcs-bridge/core/tests/fixtures/{script}", env!("CARGO_MANIFEST_DIR"));
    let document = format!("provider_id = 'bridge-1'\nlisten = '127.0.0.1:0'\nbcs_to_provider_token = 'tok-b2p'\nstate_path = '{}'\n\
        [[bot]]\nprovider_bot_ref = 'worker-1'\nengine = 'claude-code'\ncwd = '/tmp'\nengine_bin = '{bin}'\n\
        engine_options = {{ completion_mode = 'legacy' }}\n[bot.a2a]\nname = 'Worker'\n", dir.path().join("s.sqlite3").display());
    let config = bcs_bridge_core::config::ProviderConfig::from_toml(&document, &dir.path().join("b.toml")).unwrap();
    let state = Arc::new(bcs_bridge_core::AppState::new(config, &bcs_bridge_core::engine::EngineRegistry::builtin()).unwrap());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = bcs_bridge_core::webhook::multi_router(vec![state]);
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    Bridge { base: format!("http://{addr}/a2a/bridge-1/worker-1"), _dir: dir }
}

#[tokio::test]
async fn streams_a_bridge_turn_into_bcn_events() {
    let bridge = spawn_bridge("mock_cc.sh").await;
    let h = Harness::new(bridge.base.clone(), "tok-b2p");
    let result = h.send("run-1", "hello").await;
    assert!(result.delivered, "{:?}", result.error);
    let states = h.flow.wait_terminal().await;
    assert_eq!(states.first().unwrap(), "chat.event:Delta:正在");
    assert!(states.contains(&"agent:ToolCallStart:".to_owned()) && states.contains(&"agent:ToolCallEnd:".to_owned()), "tool progress reaches BCS: {states:?}");
    assert_eq!(states.last().unwrap(), "chat.event:Final:完成了", "{states:?}");
    assert_eq!(h.runs.get_provider_transport("run-1").await, Some(ProviderRunTransport::Terminal));
}

#[tokio::test]
async fn bridge_hitl_is_registered_and_answered_over_a2a() {
    let bridge = spawn_bridge("mock_cc_approval.py").await;
    let h = Harness::new(bridge.base.clone(), "tok-b2p");
    assert!(h.send("run-hitl", "bash").await.delivered);
    let requested = h.wait_requested().await;
    assert_eq!(requested.kind, InteractionKind::Exec);
    assert_eq!(requested.bcs_session_id, SESSION);
    assert_eq!(requested.payload["command"], "printf approved");
    assert_eq!(requested.payload["options"][0]["decision"], "allow-once");
    let ack = h.resolve(&requested, json!({"decision": "allow-once"})).await;
    assert!(ack.ok, "{ack:?}");
    let states = h.flow.wait_terminal().await;
    assert!(states.last().unwrap().contains(":Final:") && states.last().unwrap().contains("approved"), "{states:?}");
    assert_eq!(h.interactions.resolved.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn session_abort_cancels_the_bridge_task() {
    let bridge = spawn_bridge("mock_cc_paced.sh").await;
    let h = Harness::new(bridge.base.clone(), "tok-b2p");
    assert!(h.send("run-abort", "paced:50:0.2").await.delivered);
    for _ in 0..100 { if !h.flow.states().is_empty() { break; } tokio::time::sleep(Duration::from_millis(50)).await; }
    let aborted = h.transport.abort(BotAbortDeliveryCommand { target: h.target.clone(), command_id: "abort-1".into(),
        group_id: "group-1".into(), session_id: SESSION.into(), run_id: None, provider_bypass_headers: Vec::new(), timeout_ms: 10_000 }).await.unwrap();
    assert_eq!(aborted.aborted_run_ids, ["run-abort"]);
    let states = h.flow.wait_terminal().await;
    assert!(states.last().unwrap().contains(":Aborted:"), "{states:?}");
}

#[tokio::test]
async fn wrong_token_is_rejected_without_starting_a_run() {
    let bridge = spawn_bridge("mock_cc.sh").await;
    let h = Harness::new(bridge.base.clone(), "wrong");
    let result = h.send("run-denied", "hello").await;
    assert!(!result.delivered);
    assert_eq!(h.runs.get_provider_transport("run-denied").await, None, "transport reservation is released");
}

// ---------------------------------------------------------------- third-party agents

#[derive(Default)]
struct Agent { streaming: bool, calls: StdMutex<Vec<(String, Option<String>, Value)>>, polls: StdMutex<u32> }

fn card(base: &str, streaming: bool) -> Value {
    json!({"name": "Third", "description": "x", "version": "1", "supportedInterfaces": [{"url": format!("{base}/rpc"), "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
        "capabilities": {"streaming": streaming}, "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"], "skills": []})
}

fn sse(id: &Value, events: &[(u64, Value)]) -> Response {
    let body: String = events.iter().map(|(seq, event)| format!("id: {seq}\ndata: {}\n\n", json!({"jsonrpc": "2.0", "id": id, "result": event}))).collect();
    ([("content-type", "text/event-stream")], body).into_response()
}

async fn agent_rpc(State(agent): State<Arc<Agent>>, headers: HeaderMap, Json(request): Json<Value>) -> Response {
    assert_eq!(headers["a2a-version"], "1.0");
    let method = request["method"].as_str().unwrap().to_owned();
    let last_event_id = headers.get("last-event-id").and_then(|v| v.to_str().ok()).map(str::to_owned);
    agent.calls.lock().unwrap().push((method.clone(), last_event_id, request["params"].clone()));
    let id = request["id"].clone();
    let task = |state: &str, text: Option<&str>| {
        let mut task = json!({"id": "t-1", "contextId": "ctx-agent", "status": {"state": state}});
        if let Some(text) = text { task["artifacts"] = json!([{"artifactId": "out", "parts": [{"text": text}]}]); }
        task
    };
    match method.as_str() {
        "SendStreamingMessage" => sse(&id, &[
            (1, json!({"task": task("TASK_STATE_WORKING", None)})),
            (2, json!({"artifactUpdate": {"taskId": "t-1", "contextId": "ctx-agent", "artifact": {"artifactId": "out", "parts": [{"text": "hel"}]}}})),
        ]),
        "SubscribeToTask" => sse(&id, &[
            (3, json!({"artifactUpdate": {"taskId": "t-1", "contextId": "ctx-agent", "artifact": {"artifactId": "out", "parts": [{"text": "lo"}]}, "append": true}})),
            (4, json!({"statusUpdate": {"taskId": "t-1", "contextId": "ctx-agent", "status": {"state": "TASK_STATE_COMPLETED"}}})),
        ]),
        "SendMessage" if request["params"]["message"]["taskId"] == "t-1" => Json(json!({"jsonrpc": "2.0", "id": id, "result": {"task": task("TASK_STATE_WORKING", None)}})).into_response(),
        "SendMessage" => Json(json!({"jsonrpc": "2.0", "id": id, "result": {"task": task("TASK_STATE_SUBMITTED", None)}})).into_response(),
        "GetTask" => {
            let answered = agent.calls.lock().unwrap().iter().any(|(m, _, p)| m == "SendMessage" && p["message"]["taskId"] == "t-1");
            let mut polls = agent.polls.lock().unwrap();
            *polls += 1;
            let result = if answered {
                task("TASK_STATE_COMPLETED", Some("Paris it is"))
            } else {
                let mut waiting = task("TASK_STATE_INPUT_REQUIRED", Some("Pa"));
                waiting["status"]["message"] = json!({"messageId": "q", "role": "ROLE_AGENT", "parts": [{"text": "Which city?"}]});
                waiting
            };
            Json(json!({"jsonrpc": "2.0", "id": id, "result": result})).into_response()
        }
        other => Json(json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": other}})).into_response(),
    }
}

async fn spawn_agent(streaming: bool) -> (String, Arc<Agent>) {
    let agent = Arc::new(Agent { streaming, ..Default::default() });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let card_base = base.clone();
    let app = Router::new()
        .route("/.well-known/agent-card.json", get(move |State(agent): State<Arc<Agent>>| { let base = card_base.clone(); async move { Json(card(&base, agent.streaming)) } }))
        .route("/rpc", post(agent_rpc))
        .with_state(agent.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (base, agent)
}

#[tokio::test]
async fn dropped_stream_resumes_after_last_event_id() {
    let (base, agent) = spawn_agent(true).await;
    let h = Harness::new(base, "any");
    assert!(h.send("run-resume", "hi").await.delivered);
    let states = h.flow.wait_terminal().await;
    assert_eq!(states, ["chat.event:Delta:hel", "chat.event:Delta:lo", "chat.event:Final:hello"]);
    let calls = agent.calls.lock().unwrap().clone();
    let subscribe = calls.iter().find(|(m, _, _)| m == "SubscribeToTask").expect("resumed");
    assert_eq!(subscribe.2["id"], "t-1");
    // The agent declares no replay extension, so no cursor is sent.
    assert_eq!(subscribe.1, None);
    let send = &calls[0].2["message"];
    assert_eq!(send["messageId"], "run-resume");
    assert_eq!(send["contextId"], SESSION);
    assert_eq!(send["metadata"]["bcs"]["groupId"], "group-1");
}

#[tokio::test]
async fn non_streaming_agent_is_polled_and_free_text_input_is_answered() {
    let (base, agent) = spawn_agent(false).await;
    let h = Harness::new(base, "any");
    assert!(h.send("run-poll", "plan a trip").await.delivered);
    let requested = h.wait_requested().await;
    assert_eq!(requested.kind, InteractionKind::AskUser);
    assert_eq!(requested.payload["questions"][0]["question"], "Which city?");
    let ack = h.resolve(&requested, json!({"action": "submit", "answers": {"answer": {"values": ["Paris"]}}})).await;
    assert!(ack.ok, "{ack:?}");
    let states = h.flow.wait_terminal().await;
    assert_eq!(states.last().unwrap(), "chat.event:Final:Paris it is", "{states:?}");
    assert_eq!(h.interactions.resolved.lock().unwrap().len(), 1, "agent resuming work resolves the question");
    let calls = agent.calls.lock().unwrap().clone();
    assert_eq!(calls[0].2["configuration"]["returnImmediately"], true);
    let answer = calls.iter().find(|(m, _, p)| m == "SendMessage" && p["message"]["taskId"] == "t-1").unwrap();
    assert_eq!(answer.2["message"]["parts"], json!([{"text": "Paris"}]));
    assert_eq!(answer.2["message"]["contextId"], "ctx-agent", "agent-assigned contextId is reused");
}
