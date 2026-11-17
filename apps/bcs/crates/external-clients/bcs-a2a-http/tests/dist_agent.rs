//! The distributed agent client against a scripted JSON-RPC A2A agent.
use async_trait::async_trait;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use bcs_a2a_http::dist_agent::{A2aDistAgentClient, DistAgentResolver, ResolvedAgent};
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::port::dist_agent::*;
use bcs_service_api::types::a2a::A2aUpdateMode;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface};
use futures::StreamExt;
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use tokio::io::AsyncReadExt;

/// One scripted answer of the fake agent.
#[derive(Clone)]
enum Answer {
    /// A server-sent event stream of these results.
    Sse(Vec<Value>),
    Result(Value),
    /// A JSON-RPC error answer with this code.
    Rpc(i32),
    /// HTTP 200 and an event stream whose first event is a JSON-RPC error.
    SseError(i32),
    /// A bare HTTP status.
    Http(u16),
}

#[derive(Default)]
struct Agent {
    script: Mutex<HashMap<String, VecDeque<Answer>>>,
    calls: Mutex<Vec<(Value, Option<String>, Option<String>)>>,
}

impl Agent {
    fn on(&self, method: &str, answer: Answer) {
        self.script.lock().unwrap().entry(method.into()).or_default().push_back(answer);
    }
    fn call(&self, n: usize) -> Value {
        self.calls.lock().unwrap()[n].0.clone()
    }
    fn count(&self) -> usize {
        self.calls.lock().unwrap().len()
    }
}

async fn rpc(State(agent): State<Arc<Agent>>, headers: HeaderMap, Json(request): Json<Value>) -> Response {
    let header = |name: &str| headers.get(name).and_then(|v| v.to_str().ok()).map(str::to_owned);
    agent.calls.lock().unwrap().push((request.clone(), header("authorization"), header("a2a-extensions")));
    let method = request["method"].as_str().unwrap().to_owned();
    let answer = agent.script.lock().unwrap().get_mut(&method).and_then(VecDeque::pop_front);
    let id = request["id"].clone();
    match answer.unwrap_or(Answer::Http(500)) {
        Answer::Sse(results) => {
            let wire: String = results
                .iter()
                .map(|result| format!("data: {}\n\n", json!({"jsonrpc": "2.0", "id": id, "result": result})))
                .collect();
            ([("content-type", "text/event-stream")], wire).into_response()
        }
        Answer::SseError(code) => {
            let wire = format!(
                "data: {}\n\n",
                json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": "private diagnostic"}})
            );
            ([("content-type", "text/event-stream")], wire).into_response()
        }
        Answer::Result(result) => Json(json!({"jsonrpc": "2.0", "id": id, "result": result})).into_response(),
        Answer::Rpc(code) => {
            Json(json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": "private diagnostic"}})).into_response()
        }
        Answer::Http(status) => axum::http::StatusCode::from_u16(status).unwrap().into_response(),
    }
}

struct Fixed {
    url: String,
    extensions: Vec<String>,
    /// Fail this many resolutions first.
    failures: Mutex<usize>,
}

#[async_trait]
impl DistAgentResolver for Fixed {
    async fn resolve(&self, target: &AgentBinding) -> Result<ResolvedAgent, String> {
        assert_eq!(target.credential_ref.0, "cred://agent-analyst/v3");
        let mut failures = self.failures.lock().unwrap();
        if *failures > 0 {
            *failures -= 1;
            return Err("target_unresolved".into());
        }
        Ok(ResolvedAgent {
            interface: A2aInterface {
                url: self.url.clone(),
                tenant: None,
                protocol_version: "1.0".into(),
                binding: A2aBinding::JsonRpc,
                update_mode: A2aUpdateMode::Stream,
            },
            bearer: Some("test-fixture-token".into()),
            extensions: self.extensions.clone(),
        })
    }
}

fn target() -> AgentBinding {
    AgentBinding {
        agent: AgentId("agent-analyst".into()),
        binding_version: BindingVersion("3".into()),
        credential_ref: CredentialRef("cred://agent-analyst/v3".into()),
    }
}

fn client_for(url: String, extensions: Vec<String>, failures: usize) -> A2aDistAgentClient {
    let resolver = Fixed { url, extensions, failures: Mutex::new(failures) };
    A2aDistAgentClient::new(Arc::new(resolver), OutboundUrlGuard::new(false, true), None)
}

async fn fixture() -> (A2aDistAgentClient, Arc<Agent>) {
    fixture_with(Vec::new()).await
}

async fn fixture_with(extensions: Vec<String>) -> (A2aDistAgentClient, Arc<Agent>) {
    let agent = Arc::new(Agent::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = Router::new().route("/rpc", post(rpc)).with_state(agent.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (client_for(format!("http://{address}/rpc"), extensions, 0), agent)
}

fn request(id: &str) -> StableSendRequest {
    StableSendRequest {
        request_id: RequestId(id.into()),
        context_id: None,
        payload: Payload::Inline(json!({"parts": [{"text": "compare the quarters"}]})),
    }
}

fn remote() -> RemoteTaskRef {
    RemoteTaskRef { task_id: "rt-1".into(), context_id: Some("ctx-1".into()) }
}

fn task(state: &str) -> Value {
    json!({"id": "rt-1", "contextId": "ctx-1", "status": {"state": state}})
}

fn status(state: &str, message: Option<Value>) -> Value {
    let mut status = json!({"state": state});
    if let Some(message) = message {
        status["message"] = message;
    }
    json!({"statusUpdate": {"taskId": "rt-1", "contextId": "ctx-1", "status": status}})
}

fn agent_message(text: &str) -> Value {
    json!({"messageId": "m-1", "role": "ROLE_AGENT", "parts": [{"text": text}]})
}

async fn collect(stream: RemoteEventStream) -> Vec<Result<RemoteEvent, AgentReadError>> {
    stream.collect().await
}

fn kinds(events: &[Result<RemoteEvent, AgentReadError>]) -> Vec<&'static str> {
    events
        .iter()
        .map(|event| match event {
            Ok(event) => match event.kind {
                AgentEventKind::Delta => "delta",
                AgentEventKind::Thinking => "thinking",
                AgentEventKind::Tool => "tool",
                AgentEventKind::Snapshot => "snapshot",
                AgentEventKind::InputRequired { .. } => "input_required",
                AgentEventKind::AuthRequired { .. } => "auth_required",
                AgentEventKind::Final => "final",
                AgentEventKind::Error => "error",
            },
            Err(_) => "read_error",
        })
        .collect()
}

fn payload(event: &Result<RemoteEvent, AgentReadError>) -> Value {
    match &event.as_ref().unwrap().payload {
        Payload::Inline(value) => value.clone(),
        Payload::Ref(_) => panic!("reference"),
    }
}

fn name(outcome: &SendOutcome) -> String {
    match outcome {
        SendOutcome::Accepted { .. } => "accepted".into(),
        SendOutcome::Completed { .. } => "completed".into(),
        SendOutcome::Rejected { code } => format!("rejected:{code}"),
        SendOutcome::NotSent { code } => format!("not_sent:{code}"),
        SendOutcome::OutcomeUnknown { code } => format!("unknown:{code}"),
    }
}

#[tokio::test]
async fn a_start_is_sent_once_with_a_stable_message_id_and_mapped_to_events() {
    let (client, agent) = fixture().await;
    agent.on(
        "SendStreamingMessage",
        Answer::Sse(vec![
            json!({"task": task("TASK_STATE_WORKING")}),
            json!({"artifactUpdate": {"taskId": "rt-1", "contextId": "ctx-1",
                "artifact": {"artifactId": "a-1", "parts": [{"text": "Q3 grew"}]}, "append": true}}),
            status("TASK_STATE_WORKING", Some(agent_message("comparing"))),
            status("TASK_STATE_WORKING", None),
            status("TASK_STATE_COMPLETED", None),
        ]),
    );
    let outcome = client.start(&target(), request("s0042.d1:start")).await;
    let SendOutcome::Accepted { remote: accepted, events } = outcome else {
        panic!("{}", name(&outcome));
    };
    assert_eq!(accepted, remote());
    let sent = agent.call(0);
    assert_eq!(sent["method"], "SendStreamingMessage");
    assert_eq!(sent["params"]["message"]["messageId"], "s0042.d1:start");
    assert_eq!(sent["params"]["message"]["role"], "ROLE_USER");
    assert_eq!(sent["params"]["message"]["parts"][0]["text"], "compare the quarters");
    assert!(sent["params"]["message"].get("taskId").is_none());
    let (_, authorization, extensions) = agent.calls.lock().unwrap()[0].clone();
    assert_eq!(authorization.as_deref(), Some("Bearer test-fixture-token"));
    assert_eq!(extensions, None);

    let events = collect(events.unwrap()).await;
    // A working status without content produces nothing.
    assert_eq!(kinds(&events), vec!["snapshot", "delta", "thinking", "final"]);
    assert_eq!(payload(&events[0])["task"]["id"], "rt-1");
    assert_eq!(payload(&events[1])["artifact"]["artifactId"], "a-1");
    assert_eq!(payload(&events[1])["append"], true);
    assert_eq!(payload(&events[1])["last_chunk"], false);
    assert_eq!(payload(&events[2])["message"]["parts"][0]["text"], "comparing");
    assert_eq!(payload(&events[3]), json!({"state": "completed"}));
    let first = events[0].as_ref().unwrap();
    assert_eq!((first.remote.clone(), &first.event_id, &first.cursor), (remote(), &None, &None));
    assert_eq!(agent.count(), 1, "no retry inside the adapter");
}

#[tokio::test]
async fn a_direct_message_answer_completes_the_call() {
    let (client, agent) = fixture().await;
    agent.on("SendStreamingMessage", Answer::Sse(vec![json!({"message": agent_message("42")})]));
    let outcome = client.start(&target(), request("r-1")).await;
    let SendOutcome::Completed { result: Payload::Inline(result) } = outcome else {
        panic!("{}", name(&outcome));
    };
    assert_eq!(result["state"], "completed");
    assert_eq!(result["message"]["parts"][0]["text"], "42");
}

/// Resolves to an interface of an agent that declares no streaming.
struct Unary(String);

#[async_trait]
impl DistAgentResolver for Unary {
    async fn resolve(&self, _: &AgentBinding) -> Result<ResolvedAgent, String> {
        Ok(ResolvedAgent {
            interface: A2aInterface {
                url: self.0.clone(),
                tenant: None,
                protocol_version: "1.0".into(),
                binding: A2aBinding::JsonRpc,
                update_mode: A2aUpdateMode::Poll,
            },
            bearer: None,
            extensions: Vec::new(),
        })
    }
}

#[tokio::test]
async fn a_non_streaming_interface_is_sent_to_with_the_unary_operation() {
    let agent = Arc::new(Agent::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = Router::new().route("/rpc", post(rpc)).with_state(agent.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let resolver = Unary(format!("http://{address}/rpc"));
    let client = A2aDistAgentClient::new(Arc::new(resolver), OutboundUrlGuard::new(false, true), None);

    agent.on("SendMessage", Answer::Result(json!({"task": task("TASK_STATE_WORKING")})));
    let outcome = client.start(&target(), request("s0042.d1:start")).await;
    let SendOutcome::Accepted { remote: accepted, events } = outcome else {
        panic!("{}", name(&outcome));
    };
    assert_eq!(accepted, remote());
    let sent = agent.call(0);
    assert_eq!(sent["method"], "SendMessage");
    assert_eq!(sent["params"]["message"]["messageId"], "s0042.d1:start");
    assert_eq!(sent["params"]["configuration"]["returnImmediately"], true);
    // The answer's task is the only event; the caller follows the task.
    assert_eq!(kinds(&collect(events.unwrap()).await), vec!["snapshot"]);

    agent.on("SendMessage", Answer::Result(json!({"message": agent_message("42")})));
    let outcome = client.send_input(&target(), &remote(), request("s0042.d1:in:1")).await;
    assert_eq!(agent.call(1)["params"]["message"]["taskId"], "rt-1");
    assert!(matches!(outcome, SendOutcome::Completed { .. }), "{}", name(&outcome));

    agent.on("SendMessage", Answer::Rpc(-32601));
    assert_eq!(name(&client.start(&target(), request("r-3")).await), "rejected:a2a_method_not_found");
    agent.on("SendMessage", Answer::Http(503));
    assert!(name(&client.start(&target(), request("r-4")).await).starts_with("unknown:"));
    assert_eq!(agent.count(), 4, "no retry inside the adapter");
}

#[tokio::test]
async fn an_input_continues_the_remote_task() {
    let (client, agent) = fixture().await;
    agent.on("SendStreamingMessage", Answer::Sse(vec![status("TASK_STATE_WORKING", None), status("TASK_STATE_COMPLETED", None)]));
    let outcome = client.send_input(&target(), &remote(), request("s0042.d1:in:1")).await;
    let SendOutcome::Accepted { remote: accepted, events } = outcome else {
        panic!("{}", name(&outcome));
    };
    assert_eq!(accepted, remote());
    let message = &agent.call(0)["params"]["message"];
    assert_eq!(message["messageId"], "s0042.d1:in:1");
    assert_eq!(message["taskId"], "rt-1");
    assert_eq!(message["contextId"], "ctx-1");
    assert_eq!(kinds(&collect(events.unwrap()).await), vec!["final"]);
}

#[tokio::test]
async fn terminal_and_question_states_carry_our_state_word() {
    let (client, agent) = fixture().await;
    let cases = [
        ("TASK_STATE_FAILED", "error", json!({"state": "failed", "code": "agent_failed"})),
        ("TASK_STATE_CANCELED", "error", json!({"state": "canceled", "code": "agent_canceled"})),
        ("TASK_STATE_REJECTED", "error", json!({"state": "rejected", "code": "agent_rejected"})),
        ("TASK_STATE_INPUT_REQUIRED", "input_required", json!({"state": "input_required"})),
        ("TASK_STATE_AUTH_REQUIRED", "auth_required", json!({"state": "auth_required"})),
    ];
    for (state, kind, expected) in cases {
        agent.on("SendStreamingMessage", Answer::Sse(vec![json!({"task": task("TASK_STATE_SUBMITTED")}), status(state, None)]));
        let SendOutcome::Accepted { events, .. } = client.start(&target(), request("r")).await else {
            panic!("not accepted");
        };
        let events = collect(events.unwrap()).await;
        assert_eq!(kinds(&events), vec!["snapshot", kind], "{state}");
        assert_eq!(payload(&events[1]), expected, "{state}");
    }
    // A question keeps the agent's message.
    agent.on(
        "SendStreamingMessage",
        Answer::Sse(vec![status("TASK_STATE_INPUT_REQUIRED", Some(agent_message("A or B?")))]),
    );
    let SendOutcome::Accepted { events, .. } = client.start(&target(), request("r")).await else {
        panic!("not accepted");
    };
    let events = collect(events.unwrap()).await;
    assert_eq!(payload(&events[0])["message"]["parts"][0]["text"], "A or B?");
}

#[tokio::test]
async fn tool_records_need_the_negotiated_extension() {
    let extension = bcs_a2a_http::tools::EXTENSION;
    let record = json!({"messageId": "m-2", "role": "ROLE_AGENT",
        "metadata": {extension: {"version": "1"}},
        "parts": [{"data": {"phase": "start", "toolCallId": "call-3", "name": "search", "args": {"q": "x"}}}]});
    let stream = vec![json!({"task": task("TASK_STATE_WORKING")}), status("TASK_STATE_WORKING", Some(record))];

    let (client, agent) = fixture_with(vec![extension.to_owned()]).await;
    agent.on("SendStreamingMessage", Answer::Sse(stream.clone()));
    let SendOutcome::Accepted { events, .. } = client.start(&target(), request("r")).await else {
        panic!("not accepted");
    };
    let events = collect(events.unwrap()).await;
    assert_eq!(kinds(&events), vec!["snapshot", "tool"]);
    assert_eq!(payload(&events[1])["tool"], json!({"phase": "start", "toolCallId": "call-3", "name": "search", "args": {"q": "x"}}));
    assert_eq!(agent.calls.lock().unwrap()[0].2.as_deref(), Some(extension));

    // The agent repeats the same record (a retransmission): no second tool
    // event, and never a "thinking" event carrying tool payload.
    let (client, agent) = fixture_with(vec![extension.to_owned()]).await;
    let mut repeated = stream.clone();
    repeated.push(repeated[1].clone());
    agent.on("SendStreamingMessage", Answer::Sse(repeated));
    let SendOutcome::Accepted { events, .. } = client.start(&target(), request("r")).await else {
        panic!("not accepted");
    };
    assert_eq!(kinds(&collect(events.unwrap()).await), vec!["snapshot", "tool"]);

    // Not negotiated: the record is dropped, not relayed as thinking.
    let (client, agent) = fixture().await;
    agent.on("SendStreamingMessage", Answer::Sse(stream));
    let SendOutcome::Accepted { events, .. } = client.start(&target(), request("r")).await else {
        panic!("not accepted");
    };
    assert_eq!(kinds(&collect(events.unwrap()).await), vec!["snapshot"]);
}

#[tokio::test]
async fn complete_rejections_are_distinct_from_uncertain_outcomes() {
    let (client, agent) = fixture().await;
    let cases = [
        (Answer::Http(400), "rejected:http_400"),
        (Answer::Http(401), "rejected:http_401"),
        (Answer::Http(422), "rejected:http_422"),
        (Answer::Rpc(-32602), "rejected:a2a_invalid_params"),
        (Answer::Rpc(-32601), "rejected:a2a_method_not_found"),
        // A server-defined code ("overloaded", "timeout") proves nothing.
        (Answer::Rpc(-32000), "unknown:a2a_internal_error"),
        (Answer::Rpc(-32099), "unknown:a2a_internal_error"),
        // An error as the first stream event: the task may already exist.
        (Answer::SseError(-32602), "unknown:a2a_invalid_params"),
        // A task that could never be addressed again.
        (Answer::Sse(vec![json!({"task": {"id": "", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"}}})]),
            "unknown:invalid_agent_response"),
        (Answer::Http(408), "unknown:http_408"),
        (Answer::Http(429), "unknown:http_429"),
        (Answer::Http(503), "unknown:http_503"),
        // A JSON-RPC internal error is like a 5xx: the agent may have started.
        (Answer::Rpc(-32603), "unknown:transport"),
        (Answer::Sse(Vec::new()), "unknown:stream_ended_before_first_event"),
    ];
    for (answer, expected) in cases {
        agent.on("SendStreamingMessage", answer);
        assert_eq!(name(&client.start(&target(), request("r")).await), expected);
    }
    assert_eq!(agent.count(), 14, "one request per send: never retried");
}

#[tokio::test]
async fn a_send_that_never_left_the_process_is_not_sent() {
    // The target cannot be resolved.
    let (_, agent) = fixture().await;
    let unresolved = client_for("http://127.0.0.1:9/rpc".into(), Vec::new(), 1);
    assert_eq!(name(&unresolved.start(&target(), request("r")).await), "not_sent:target_unresolved");

    // The address is refused by the outbound policy.
    let resolver = Fixed { url: "http://127.0.0.1:9/rpc".into(), extensions: Vec::new(), failures: Mutex::new(0) };
    let blocked = A2aDistAgentClient::new(Arc::new(resolver), OutboundUrlGuard::new(true, false), None);
    assert_eq!(name(&blocked.start(&target(), request("r")).await), "not_sent:address_rejected");

    // Nothing listens on the port: the connection was never established.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    let refused = client_for(format!("http://{address}/rpc"), Vec::new(), 0);
    assert_eq!(name(&refused.start(&target(), request("r")).await), "not_sent:not_connected");
    assert_eq!(name(&refused.send_input(&target(), &remote(), request("r")).await), "not_sent:not_connected");
    assert_eq!(agent.count(), 0);
}

#[tokio::test]
async fn a_server_that_closes_after_reading_the_request_is_unknown() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        while let Ok((mut socket, _)) = listener.accept().await {
            let mut buffer = [0u8; 4096];
            let _ = socket.read(&mut buffer).await;
        }
    });
    let client = client_for(format!("http://{address}/rpc"), Vec::new(), 0);
    assert_eq!(name(&client.start(&target(), request("r")).await), "unknown:transport");
}

#[tokio::test]
async fn a_request_that_cannot_be_expressed_is_rejected_without_a_call() {
    let (client, agent) = fixture().await;
    let mut by_reference = request("r");
    by_reference.payload = Payload::Ref(BlobRef { key: "k".into(), digest: Digest("d".into()) });
    assert_eq!(name(&client.start(&target(), by_reference).await), "rejected:request_by_reference");
    let mut number = request("r");
    number.payload = Payload::Inline(json!(7));
    assert_eq!(name(&client.start(&target(), number).await), "rejected:request_invalid");
    // Plain text is accepted as one text part.
    let mut text = request("r");
    text.payload = Payload::Inline(json!("hello"));
    agent.on("SendStreamingMessage", Answer::Sse(vec![json!({"task": task("TASK_STATE_WORKING")})]));
    assert_eq!(name(&client.start(&target(), text).await), "accepted");
    assert_eq!(agent.call(0)["params"]["message"]["parts"], json!([{"text": "hello"}]));
    assert_eq!(agent.count(), 1);
}

#[tokio::test]
async fn a_task_query_returns_the_state_and_the_payload_it_stands_for() {
    let (client, agent) = fixture().await;
    agent.on("GetTask", Answer::Result(task("TASK_STATE_WORKING")));
    let mut done = task("TASK_STATE_COMPLETED");
    done["artifacts"] = json!([{"artifactId": "a-1", "parts": [{"text": "Q3 grew 12%"}]}]);
    done["history"] = json!([agent_message("old")]);
    agent.on("GetTask", Answer::Result(done));
    agent.on("GetTask", Answer::Rpc(-32001));
    agent.on("GetTask", Answer::Http(503));

    let by_task = || RemoteQuery::ByTask(remote());
    let QueryOutcome::Found { remote: found, status, snapshot: Payload::Inline(snapshot) } = client.query(&target(), by_task()).await else {
        panic!("not found");
    };
    assert_eq!((found, status), (remote(), TaskStatus::Working));
    assert_eq!(snapshot["task"]["id"], "rt-1");
    assert_eq!(agent.call(0)["params"]["id"], "rt-1");

    let QueryOutcome::Found { status, snapshot: Payload::Inline(snapshot), .. } = client.query(&target(), by_task()).await else {
        panic!("not found");
    };
    assert_eq!(status, TaskStatus::Completed);
    assert_eq!(snapshot["state"], "completed");
    assert_eq!(snapshot["artifacts"][0]["artifactId"], "a-1", "a whole task carries the complete result");

    assert_eq!(client.query(&target(), by_task()).await, QueryOutcome::NotFound);
    // A bare HTTP failure of a JSON-RPC call is not an agent answer at all.
    assert_eq!(
        client.query(&target(), by_task()).await,
        QueryOutcome::Unavailable { code: "a2a_invalid_agent_response".into() }
    );
    // A2A cannot look a task up by message id.
    assert_eq!(
        client.query(&target(), RemoteQuery::ByRequest(RequestId("r".into()))).await,
        QueryOutcome::Unsupported
    );
    assert_eq!(agent.count(), 4);
}

#[tokio::test]
async fn subscribe_follows_the_task_and_reports_read_failures_by_code() {
    let (client, agent) = fixture().await;
    agent.on("SubscribeToTask", Answer::Sse(vec![json!({"task": task("TASK_STATE_WORKING")}), status("TASK_STATE_COMPLETED", None)]));
    let events = collect(client.subscribe(&target(), &remote(), None).await.unwrap()).await;
    assert_eq!(kinds(&events), vec!["snapshot", "final"]);
    assert_eq!(agent.call(0)["params"]["id"], "rt-1");

    agent.on("SubscribeToTask", Answer::Http(503));
    let error = client.subscribe(&target(), &remote(), None).await.err().unwrap();
    assert_eq!(error.code, "http_503");
    let unresolved = client_for("http://127.0.0.1:9/rpc".into(), Vec::new(), 1);
    assert_eq!(unresolved.subscribe(&target(), &remote(), None).await.err().unwrap().code, "target_unresolved");
}

#[tokio::test]
async fn cancel_keeps_accepted_confirmed_finished_and_unknown_apart() {
    let (client, agent) = fixture().await;
    let cancel = || async { client.cancel(&target(), &remote(), RequestId("cancel-1".into())).await };

    agent.on("CancelTask", Answer::Result(task("TASK_STATE_CANCELED")));
    assert_eq!(cancel().await, CancelOutcome::ConfirmedCanceled);
    agent.on("CancelTask", Answer::Result(task("TASK_STATE_WORKING")));
    assert_eq!(cancel().await, CancelOutcome::RequestAccepted);
    agent.on("CancelTask", Answer::Result(task("TASK_STATE_COMPLETED")));
    assert_eq!(cancel().await, CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Completed));
    agent.on("CancelTask", Answer::Result(task("TASK_STATE_FAILED")));
    assert_eq!(cancel().await, CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Failed));

    // "Not cancelable": one read tells a finished task from a refusal.
    agent.on("CancelTask", Answer::Rpc(-32002));
    agent.on("GetTask", Answer::Result(task("TASK_STATE_COMPLETED")));
    assert_eq!(cancel().await, CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Completed));
    agent.on("CancelTask", Answer::Rpc(-32002));
    agent.on("GetTask", Answer::Result(task("TASK_STATE_WORKING")));
    assert_eq!(cancel().await, CancelOutcome::Rejected { code: "task_not_cancelable".into() });
    agent.on("CancelTask", Answer::Rpc(-32002));
    agent.on("GetTask", Answer::Http(503));
    assert_eq!(cancel().await, CancelOutcome::OutcomeUnknown { code: "a2a_invalid_agent_response".into() });

    // A protocol error answer is a refusal; no answer at all is unknown.
    agent.on("CancelTask", Answer::Rpc(-32601));
    assert_eq!(cancel().await, CancelOutcome::Rejected { code: "a2a_method_not_found".into() });
    agent.on("CancelTask", Answer::Http(503));
    assert_eq!(cancel().await, CancelOutcome::OutcomeUnknown { code: "a2a_invalid_agent_response".into() });
    assert_eq!(agent.call(0)["params"]["id"], "rt-1");
}

#[tokio::test]
async fn the_credential_is_not_in_debug_output() {
    let resolved = Fixed { url: "http://127.0.0.1:9/rpc".into(), extensions: Vec::new(), failures: Mutex::new(0) }
        .resolve(&target())
        .await
        .unwrap();
    let debug = format!("{resolved:?}");
    assert!(!debug.contains("test-fixture-token"), "{debug}");
    assert!(debug.contains("<redacted>"));
}
