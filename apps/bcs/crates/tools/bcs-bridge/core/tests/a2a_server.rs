//! A2A v1.0 server end-to-end: card, auth/version, the three delivery modes
//! (stream, poll, push), HITL continuation, resume, cancel and recovery.
use std::{sync::{Arc, Mutex}, time::Duration};

use bcs_bridge_core::{config::ProviderConfig, engine::EngineRegistry, webhook, AppState};
use futures::StreamExt;
use serde_json::{json, Value};

const TOKEN: &str = "tok-b2p";

struct Bridge { base: String, rpc: String, state: Arc<AppState>, _dir: tempfile::TempDir }

async fn spawn(script: &str, extra: &str) -> Bridge {
    let dir = tempfile::tempdir().unwrap();
    let bin = format!("{}/tests/fixtures/{script}", env!("CARGO_MANIFEST_DIR"));
    let document = format!(r#"
provider_id = "bridge-1"
listen = "127.0.0.1:0"
bcs_to_provider_token = "{TOKEN}"
state_path = "{state}"
[a2a]
client_tokens = ["third-party"]
push_url_allowlist = ["http://127.0.0.1:"]
{extra}
[[bot]]
provider_bot_ref = "worker-1"
engine = "claude-code"
cwd = "/tmp"
engine_bin = "{bin}"
engine_options = {{ completion_mode = "legacy" }}
[bot.a2a]
name = "Worker One"
description = "Test coding agent"
"#, state = dir.path().join("state.sqlite3").display());
    let config = ProviderConfig::from_toml(&document, &dir.path().join("bridge.toml")).unwrap();
    let state = Arc::new(AppState::new(config, &EngineRegistry::builtin()).unwrap());
    bcs_bridge_core::a2a::recover(&state).await;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = webhook::multi_router(vec![state.clone()]);
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let base = format!("http://{addr}");
    Bridge { rpc: format!("{base}/a2a/bridge-1/worker-1"), base, state, _dir: dir }
}

fn client() -> reqwest::Client { reqwest::Client::builder().no_proxy().build().unwrap() }

fn rpc_body(method: &str, params: Value) -> Value { json!({"jsonrpc": "2.0", "id": "1", "method": method, "params": params}) }

fn user_message(id: &str, text: &str) -> Value {
    json!({"messageId": id, "role": "ROLE_USER", "parts": [{"text": text}]})
}

async fn call(bridge: &Bridge, method: &str, params: Value) -> Value {
    client().post(&bridge.rpc).bearer_auth(TOKEN).header("A2A-Version", "1.0")
        .json(&rpc_body(method, params)).send().await.unwrap().json().await.unwrap()
}

async fn stream_request(bridge: &Bridge, method: &str, params: Value, last_event_id: Option<u64>) -> reqwest::Response {
    let mut request = client().post(&bridge.rpc).bearer_auth(TOKEN).header("A2A-Version", "1.0");
    if let Some(id) = last_event_id { request = request.header("Last-Event-ID", id.to_string()); }
    let response = request.json(&rpc_body(method, params)).send().await.unwrap();
    assert_eq!(response.headers()["content-type"], "text/event-stream; charset=utf-8");
    response
}

/// Incremental SSE reader yielding `(id, result)` per JSON-RPC frame.
struct Sse { body: futures::stream::BoxStream<'static, reqwest::Result<bytes::Bytes>>, buffer: String }

impl Sse {
    fn new(response: reqwest::Response) -> Self { Self { body: response.bytes_stream().boxed(), buffer: String::new() } }

    async fn next(&mut self) -> Option<(u64, Value)> {
        loop {
            while let Some(end) = self.buffer.find("\n\n") {
                let frame: String = self.buffer.drain(..end + 2).collect();
                let id = frame.lines().find_map(|l| l.strip_prefix("id: ")).and_then(|v| v.parse().ok());
                let data = frame.lines().find_map(|l| l.strip_prefix("data: "));
                if let (Some(id), Some(data)) = (id, data) {
                    let envelope: Value = serde_json::from_str(data).unwrap();
                    assert_eq!(envelope["jsonrpc"], "2.0");
                    assert_eq!(envelope["id"], "1");
                    return Some((id, envelope["result"].clone()));
                }
            }
            let chunk = tokio::time::timeout(Duration::from_secs(20), self.body.next()).await.expect("sse stalled")?.unwrap();
            self.buffer.push_str(&String::from_utf8_lossy(&chunk));
        }
    }

    async fn collect(mut self) -> Vec<(u64, Value)> {
        let mut out = Vec::new();
        while let Some(event) = self.next().await { out.push(event); }
        out
    }
}

fn state_of(event: &Value) -> Option<&str> {
    event.pointer("/statusUpdate/status/state").or_else(|| event.pointer("/task/status/state")).and_then(Value::as_str)
}

#[tokio::test]
async fn agent_card_is_public_and_describes_the_jsonrpc_interface() {
    let bridge = spawn("mock_cc.sh", "").await;
    let card: Value = client().get(format!("{}/.well-known/agent-card.json", bridge.rpc)).send().await.unwrap().json().await.unwrap();
    assert_eq!(card["name"], "Worker One");
    assert_eq!(card["supportedInterfaces"][0], json!({"url": bridge.rpc, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}));
    assert_eq!(card["capabilities"]["streaming"], true);
    assert_eq!(card["capabilities"]["pushNotifications"], true);
    let uris: Vec<_> = card["capabilities"]["extensions"].as_array().unwrap().iter().map(|e| e["uri"].as_str().unwrap()).collect();
    assert!(uris.contains(&"https://avernet.local/a2a/ext/replay/v1"));
    serde_json::from_value::<bcs_a2a_protocol::AgentCard>(card).unwrap();
    let missing = client().get(format!("{}/a2a/bridge-1/nobody/.well-known/agent-card.json", bridge.base)).send().await.unwrap();
    assert_eq!(missing.status(), 404);
}

#[tokio::test]
async fn rejects_missing_token_and_unsupported_versions() {
    let bridge = spawn("mock_cc.sh", "").await;
    let body = rpc_body("GetTask", json!({"id": "x"}));
    let unauthorized = client().post(&bridge.rpc).json(&body).send().await.unwrap();
    assert_eq!(unauthorized.status(), 401);
    let legacy: Value = client().post(&bridge.rpc).bearer_auth(TOKEN).json(&body).send().await.unwrap().json().await.unwrap();
    assert_eq!(legacy["error"]["code"], -32009);
    let unknown = call(&bridge, "GetTask", json!({"id": "x"})).await;
    assert_eq!(unknown["error"]["code"], -32001);
    assert_eq!(unknown["error"]["data"][0]["reason"], "TASK_NOT_FOUND");
    let method = call(&bridge, "message/send", json!({})).await;
    assert_eq!(method["error"]["code"], -32601);
}

#[tokio::test]
async fn blocking_send_returns_the_completed_task_with_the_conclusion() {
    let bridge = spawn("mock_cc.sh", "").await;
    let reply = call(&bridge, "SendMessage", json!({"message": user_message("m-1", "hello")})).await;
    let task = &reply["result"]["task"];
    assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED", "{reply}");
    assert_eq!(task["artifacts"][0]["artifactId"], "response");
    assert_eq!(task["artifacts"][0]["parts"], json!([{"text": "完成了"}]));
    assert_eq!(task["history"][0]["parts"][0]["text"], "hello");
    let context = task["contextId"].as_str().unwrap();
    // Polling the same task returns the same result; tasks are owner-scoped.
    let polled = call(&bridge, "GetTask", json!({"id": task["id"], "historyLength": 0})).await;
    assert_eq!(polled["result"]["artifacts"], task["artifacts"]);
    assert!(polled["result"].get("history").is_none());
    let foreign: Value = client().post(&bridge.rpc).bearer_auth("third-party").header("A2A-Version", "1.0")
        .json(&rpc_body("GetTask", json!({"id": task["id"]}))).send().await.unwrap().json().await.unwrap();
    assert_eq!(foreign["error"]["code"], -32001);
    // A retried messageId re-attaches instead of starting another engine turn.
    let retry = call(&bridge, "SendMessage", json!({"message": user_message("m-1", "hello")})).await;
    assert_eq!(retry["result"]["task"]["id"], task["id"]);
    assert_eq!(retry["result"]["task"]["contextId"], context);
    // Immediate sequential follow-ups in the same context never see it busy.
    for turn in 2..5 {
        let next = call(&bridge, "SendMessage", json!({"message": {"messageId": format!("m-{turn}"), "role": "ROLE_USER",
            "contextId": context, "parts": [{"text": "again"}]}})).await;
        assert_eq!(next["result"]["task"]["status"]["state"], "TASK_STATE_COMPLETED", "{next}");
        assert_eq!(next["result"]["task"]["contextId"], context);
    }
}

#[tokio::test]
async fn streaming_send_emits_ordered_progress_and_result_events() {
    let bridge = spawn("mock_cc.sh", "").await;
    let response = stream_request(&bridge, "SendStreamingMessage", json!({"message": user_message("m-1", "hello")}), None).await;
    let events = Sse::new(response).collect().await;
    let ids: Vec<u64> = events.iter().map(|(id, _)| *id).collect();
    assert_eq!(ids, (1..=ids.len() as u64).collect::<Vec<_>>(), "seq must be gapless");
    assert_eq!(state_of(&events[0].1), Some("TASK_STATE_SUBMITTED"));
    assert!(events[0].1.get("task").is_some(), "stream starts with the Task");
    for (id, event) in &events { assert_eq!(event.as_object().unwrap().len(), 1, "oneof"); assert_eq!(event.pointer("/statusUpdate/metadata/seq").or_else(|| event.pointer("/artifactUpdate/metadata/seq")).or_else(|| event.pointer("/task/metadata/seq")).and_then(Value::as_u64), Some(*id)); }
    assert!(events.iter().any(|(_, e)| e.pointer("/statusUpdate/metadata/kind") == Some(&json!("tool"))), "tool progress");
    let artifacts: Vec<_> = events.iter().filter_map(|(_, e)| e.get("artifactUpdate")).collect();
    assert_eq!(artifacts.first().unwrap()["artifact"]["parts"][0]["text"], "正在");
    let last_artifact = artifacts.last().unwrap();
    assert_eq!(last_artifact["lastChunk"], true);
    assert_eq!(last_artifact["artifact"]["parts"][0]["text"], "完成了", "final text replaces streamed deltas");
    let (_, last) = events.last().unwrap();
    assert_eq!(last["task"]["status"]["state"], "TASK_STATE_COMPLETED");
    assert_eq!(state_of(&events[events.len() - 2].1), Some("TASK_STATE_COMPLETED"));
}

#[tokio::test]
async fn return_immediately_then_poll_until_completed() {
    let bridge = spawn("mock_cc_paced.sh", "").await;
    let reply = call(&bridge, "SendMessage", json!({"message": user_message("m-1", "paced:3:0.1"),
        "configuration": {"returnImmediately": true}})).await;
    let task = &reply["result"]["task"];
    assert!(matches!(task["status"]["state"].as_str(), Some("TASK_STATE_SUBMITTED" | "TASK_STATE_WORKING")), "{reply}");
    let id = task["id"].clone();
    let mut last = Value::Null;
    for _ in 0..100 {
        last = call(&bridge, "GetTask", json!({"id": id})).await["result"].clone();
        if last["status"]["state"] == "TASK_STATE_COMPLETED" { break; }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(last["status"]["state"], "TASK_STATE_COMPLETED", "{last}");
    assert_eq!(last["artifacts"][0]["parts"][0]["text"], "paced done");
}

#[tokio::test]
async fn push_notifications_deliver_every_event_with_authentication() {
    let received: Arc<Mutex<Vec<(Option<String>, Value)>>> = Arc::default();
    let sink = received.clone();
    let receiver = axum::Router::new().route("/push", axum::routing::post(move |headers: axum::http::HeaderMap, body: axum::Json<Value>| {
        let sink = sink.clone();
        async move {
            let auth = headers.get("authorization").and_then(|v| v.to_str().ok()).map(str::to_owned);
            sink.lock().unwrap().push((auth, body.0));
            axum::http::StatusCode::OK
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let push_url = format!("http://{}/push", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, receiver).await.unwrap() });

    let bridge = spawn("mock_cc.sh", "").await;
    let denied = call(&bridge, "SendMessage", json!({"message": user_message("m-0", "hello"), "configuration": {
        "returnImmediately": true, "taskPushNotificationConfig": {"url": "http://10.0.0.1/steal"}}})).await;
    assert_eq!(denied["error"]["code"], -32602, "non-allowlisted push target");
    let reply = call(&bridge, "SendMessage", json!({"message": user_message("m-1", "hello"), "configuration": {
        "returnImmediately": true,
        "taskPushNotificationConfig": {"url": push_url, "token": "t-1", "authentication": {"scheme": "Bearer", "credentials": "push-secret"}}}})).await;
    let task_id = reply["result"]["task"]["id"].as_str().unwrap().to_owned();
    for _ in 0..100 {
        if received.lock().unwrap().iter().any(|(_, e)| e.pointer("/task/status/state") == Some(&json!("TASK_STATE_COMPLETED"))) { break; }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let received = received.lock().unwrap().clone();
    assert!(received.iter().all(|(auth, _)| auth.as_deref() == Some("Bearer push-secret")));
    assert!(received.iter().all(|(_, e)| bcs_a2a_protocol::StreamResponse::task_id(&serde_json::from_value(e.clone()).unwrap()) == Some(task_id.as_str())));
    let seqs: Vec<u64> = received.iter().map(|(_, e)| e.as_object().unwrap().values().next().unwrap()["metadata"]["seq"].as_u64().unwrap()).collect();
    assert_eq!(seqs, (1..=seqs.len() as u64).collect::<Vec<_>>(), "ordered, at least once, from the first event");
    let last = &received.last().unwrap().1;
    assert_eq!(last["task"]["artifacts"][0]["parts"][0]["text"], "完成了");
    let configs = call(&bridge, "ListTaskPushNotificationConfigs", json!({"taskId": task_id})).await;
    assert!(configs["error"]["code"] == -32004 || configs["result"]["configs"][0]["url"] == push_url, "{configs}");
}

#[tokio::test]
async fn input_required_is_answered_by_a_follow_up_message_on_the_same_stream() {
    let bridge = spawn("mock_cc_approval.py", "").await;
    let response = stream_request(&bridge, "SendStreamingMessage", json!({"message": user_message("m-1", "bash")}), None).await;
    let mut sse = Sse::new(response);
    let (task_id, prompt) = loop {
        let (_, event) = sse.next().await.expect("stream ended before input-required");
        if state_of(&event) == Some("TASK_STATE_INPUT_REQUIRED") && event.get("statusUpdate").is_some() {
            let update = &event["statusUpdate"];
            assert_eq!(update["metadata"]["kind"], "interaction.requested");
            assert_eq!(update["metadata"]["interactionKind"], "exec");
            break (update["taskId"].as_str().unwrap().to_owned(), update["status"]["message"]["parts"][0]["text"].as_str().unwrap().to_owned());
        }
    };
    assert!(prompt.contains("printf approved") && prompt.contains("allow-once"), "{prompt}");
    let polled = call(&bridge, "GetTask", json!({"id": task_id})).await;
    assert_eq!(polled["result"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED");
    // Plain text that is not an offered decision is refused rather than guessed.
    let vague = call(&bridge, "SendMessage", json!({"message": {"messageId": "a-0", "taskId": task_id, "role": "ROLE_USER", "parts": [{"text": "sure"}]}})).await;
    assert_eq!(vague["error"]["code"], -32602);
    let answer = call(&bridge, "SendMessage", json!({"message": {"messageId": "a-1", "taskId": task_id, "role": "ROLE_USER", "parts": [{"text": "allow-once"}]}})).await;
    assert!(answer["result"]["task"]["status"]["state"].is_string(), "{answer}");
    let rest = sse.collect().await;
    assert!(rest.iter().any(|(_, e)| e.pointer("/statusUpdate/metadata/kind") == Some(&json!("interaction.resolved"))));
    let (_, last) = rest.last().unwrap();
    assert_eq!(last["task"]["status"]["state"], "TASK_STATE_COMPLETED", "{rest:?}");
    assert!(last["task"]["artifacts"][0]["parts"][0]["text"].as_str().unwrap().contains("approved"));
    let after = call(&bridge, "SendMessage", json!({"message": {"messageId": "a-2", "taskId": task_id, "role": "ROLE_USER", "parts": [{"text": "deny"}]}})).await;
    assert_eq!(after["error"]["code"], -32004, "terminal tasks accept no messages");
}

#[tokio::test]
async fn task_survives_disconnect_and_resumes_after_last_event_id() {
    let bridge = spawn("mock_cc_paced.sh", "").await;
    let response = stream_request(&bridge, "SendStreamingMessage", json!({"message": user_message("m-1", "paced:8:0.15")}), None).await;
    let mut sse = Sse::new(response);
    let mut seen = Vec::new();
    while seen.len() < 4 { seen.push(sse.next().await.unwrap()); }
    let task_id = seen[0].1["task"]["id"].as_str().unwrap().to_owned();
    let last_seen = seen.last().unwrap().0;
    drop(sse);
    tokio::time::sleep(Duration::from_millis(400)).await;
    let resumed = Sse::new(stream_request(&bridge, "SubscribeToTask", json!({"id": task_id}), Some(last_seen)).await).collect().await;
    assert_eq!(resumed.first().unwrap().0, last_seen + 1, "replay starts right after the cursor");
    let ids: Vec<u64> = seen.iter().chain(resumed.iter()).map(|(id, _)| *id).collect();
    assert_eq!(ids, (1..=ids.len() as u64).collect::<Vec<_>>(), "no gap and no duplicate across the reconnect");
    assert_eq!(resumed.last().unwrap().1["task"]["status"]["state"], "TASK_STATE_COMPLETED");
    // Standard re-subscribe on a terminal task is refused; replay with a cursor still works.
    let terminal = call(&bridge, "SubscribeToTask", json!({"id": task_id})).await;
    assert_eq!(terminal["error"]["code"], -32004);
}

#[tokio::test]
async fn subscribe_without_cursor_starts_with_a_snapshot() {
    let bridge = spawn("mock_cc_paced.sh", "").await;
    let reply = call(&bridge, "SendMessage", json!({"message": user_message("m-1", "paced:6:0.15"), "configuration": {"returnImmediately": true}})).await;
    let task_id = reply["result"]["task"]["id"].clone();
    tokio::time::sleep(Duration::from_millis(400)).await;
    let events = Sse::new(stream_request(&bridge, "SubscribeToTask", json!({"id": task_id}), None).await).collect().await;
    let snapshot = &events[0].1["task"];
    assert_eq!(snapshot["status"]["state"], "TASK_STATE_WORKING");
    assert!(snapshot["artifacts"][0]["parts"][0]["text"].as_str().unwrap().starts_with("chunk-1"), "{snapshot}");
    assert_eq!(events.last().unwrap().1["task"]["status"]["state"], "TASK_STATE_COMPLETED");
}

#[tokio::test]
async fn cancel_task_aborts_the_engine_turn() {
    let bridge = spawn("mock_cc_paced.sh", "").await;
    let reply = call(&bridge, "SendMessage", json!({"message": user_message("m-1", "paced:50:0.2"), "configuration": {"returnImmediately": true}})).await;
    let task_id = reply["result"]["task"]["id"].clone();
    tokio::time::sleep(Duration::from_millis(300)).await;
    let canceled = call(&bridge, "CancelTask", json!({"id": task_id})).await;
    assert_eq!(canceled["result"]["status"]["state"], "TASK_STATE_CANCELED", "{canceled}");
    let again = call(&bridge, "CancelTask", json!({"id": task_id})).await;
    assert_eq!(again["error"]["code"], -32002);
    // The context is free for the next task.
    let next = call(&bridge, "SendMessage", json!({"message": {"messageId": "m-2", "role": "ROLE_USER",
        "contextId": canceled["result"]["contextId"], "parts": [{"text": "paced:1:0.01"}]}})).await;
    assert_eq!(next["result"]["task"]["status"]["state"], "TASK_STATE_COMPLETED", "{next}");
}

#[tokio::test]
async fn interrupted_tasks_are_failed_after_restart_and_stay_queryable() {
    let dir = tempfile::tempdir().unwrap();
    let state_path = dir.path().join("state.sqlite3");
    let document = format!("provider_id = 'bridge-1'\nlisten = '127.0.0.1:0'\nbcs_to_provider_token = 't'\nstate_path = '{}'\n[[bot]]\nprovider_bot_ref = 'w'\nengine = 'claude-code'\ncwd = '/tmp'\n[bot.a2a]\n", state_path.display());
    let config = ProviderConfig::from_toml(&document, &dir.path().join("b.toml")).unwrap();
    let task = json!({"id": "t-1", "contextId": "c-1", "status": {"state": "TASK_STATE_WORKING"},
        "artifacts": [{"artifactId": "response", "parts": [{"text": "partial"}]}]});
    {
        let state = AppState::new(config.clone(), &EngineRegistry::builtin()).unwrap();
        state.sessions.save_a2a_task(bcs_bridge_core::session::A2aTaskRow { task_id: "t-1".into(), owner: "bcs".into(), bot: "w".into(),
            state: "TASK_STATE_WORKING".into(), snapshot: task.to_string() }).await.unwrap();
    }
    let state = AppState::new(config, &EngineRegistry::builtin()).unwrap();
    bcs_bridge_core::a2a::recover(&state).await;
    let row = state.sessions.load_a2a_task("t-1").await.unwrap().unwrap();
    assert_eq!(row.state, "TASK_STATE_FAILED");
    let snapshot: Value = serde_json::from_str(&row.snapshot).unwrap();
    assert_eq!(snapshot["artifacts"][0]["parts"][0]["text"], "partial", "partial output is kept");
    assert!(snapshot["status"]["message"]["parts"][0]["text"].as_str().unwrap().contains("restarted"));
}
