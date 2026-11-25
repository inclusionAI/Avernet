//! Plan T6: the per-request query profile. The negotiated request-id index
//! extension resolves a request to its task through a real HTTP adapter —
//! the index runs late, the original task is found, then read and its stop
//! evidence verified through the standard GetTask. A `NotFound` from the
//! index stays unknown; standard A2A profiles without the negotiated
//! extension stay `Unsupported`, so the call upstream keeps its
//! verification obligation — never a resend, never a fabricated Task id.
use async_trait::async_trait;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use bcs_a2a_http::dist_agent::{
    A2aDistAgentClient, DistAgentResolver, ResolvedAgent, QUERY_BY_REQUEST_EXT,
};
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::port::dist_agent::*;
use bcs_service_api::types::a2a::A2aUpdateMode;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface};
use serde_json::{Value, json};
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

/// One scripted JSON-RPC answer.
#[derive(Clone)]
enum Answer {
    Result(Value),
    /// A JSON-RPC error answer with this code.
    Rpc(i32),
}

#[derive(Default)]
struct Agent {
    script: Mutex<HashMap<String, VecDeque<Answer>>>,
    calls: Mutex<Vec<(Value, Option<String>)>>,
}

impl Agent {
    fn on(&self, method: &str, answer: Answer) {
        self.script.lock().unwrap().entry(method.into()).or_default().push_back(answer);
    }
    fn calls(&self) -> Vec<(Value, Option<String>)> {
        self.calls.lock().unwrap().clone()
    }
}

async fn rpc(State(agent): State<Arc<Agent>>, headers: HeaderMap, Json(request): Json<Value>) -> Response {
    let extension = headers
        .get("a2a-extensions")
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    agent.calls.lock().unwrap().push((request.clone(), extension));
    let method = request["method"].as_str().unwrap().to_owned();
    let answer = agent.script.lock().unwrap().get_mut(&method).and_then(VecDeque::pop_front);
    let id = request["id"].clone();
    match answer.unwrap_or(Answer::Rpc(-32601)) {
        Answer::Result(result) => Json(json!({"jsonrpc": "2.0", "id": id, "result": result})).into_response(),
        Answer::Rpc(code) => {
            Json(json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": "diagnostic"}})).into_response()
        }
    }
}

/// Resolves every operation through a fixed, aggressive-authority-blind
/// endpoint that declares the negotiated request-id index.
struct Indexed {
    url: String,
    extensions: Vec<String>,
}

#[async_trait]
impl DistAgentResolver for Indexed {
    async fn resolve(&self, _authority: &CallAuthority) -> Result<ResolvedAgent, String> {
        Ok(ResolvedAgent {
            interface: A2aInterface {
                url: self.url.clone(),
                tenant: None,
                protocol_version: "1.0".into(),
                binding: A2aBinding::JsonRpc,
                update_mode: A2aUpdateMode::Stream,
            },
            bearer: None,
            extensions: self.extensions.clone(),
        })
    }
}

fn authority() -> CallAuthority {
    CallAuthority {
        round: "s0042.d1.fsm:1".into(),
        node: NodeId("start-research".into()),
        attempt: 1,
        binding: AgentBinding {
            agent: AgentId("agent-analyst".into()),
            binding_version: BindingVersion("3".into()),
            credential_ref: CredentialRef("cred://agent-analyst/v3".into()),
        },
        operation: CallOperation::Query,
        remote_ref: None,
    }
}

async fn agent(extensions: Vec<String>) -> (Arc<Agent>, A2aDistAgentClient) {
    let agent = Arc::new(Agent::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = Router::new().route("/rpc", post(rpc)).with_state(agent.clone());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let resolver = Indexed { url: format!("http://{address}/rpc"), extensions };
    let client = A2aDistAgentClient::new(Arc::new(resolver), OutboundUrlGuard::new(false, true), None);
    (agent, client)
}

fn done_task() -> Value {
    json!({
        "id": "rt-late",
        "contextId": "ctx-late",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"artifactId": "a-1", "parts": [{"text": "the whole answer"}]}]
    })
}

#[tokio::test]
async fn a_late_request_index_finds_the_original_task_and_verifies_it() {
    let (agent, client) = agent(vec![QUERY_BY_REQUEST_EXT.to_owned()]).await;
    // The agent's request-id index runs late: twice "not registered yet".
    agent.on("GetTaskByRequest", Answer::Rpc(-32001));
    agent.on("GetTaskByRequest", Answer::Rpc(-32001));
    // Then it resolves the original start request to its task ...
    agent.on("GetTaskByRequest", Answer::Result(json!({"id": "rt-late"})));
    // ... whose own record is read and verified through the standard GetTask.
    agent.on("GetTask", Answer::Result(done_task()));

    // Index late: never a proof, never a fabricated task id.
    assert_eq!(
        client.query(&authority(), RemoteQuery::ByRequest(RequestId("req-1".into()))).await,
        QueryOutcome::NotFound
    );
    assert_eq!(
        client.query(&authority(), RemoteQuery::ByRequest(RequestId("req-1".into()))).await,
        QueryOutcome::NotFound
    );

    // The original task is found through the negotiated index.
    let expected = QueryOutcome::Found {
        remote: RemoteTaskRef { task_id: "rt-late".into(), context_id: Some("ctx-late".into()) },
        status: TaskStatus::Completed,
        // The snapshot of the whole task: the complete terminal state with
        // the ordered artifact list — the stop evidence the Connector
        // verifies before any Final.
        snapshot: Payload::Inline(json!({
            "state": "completed",
            "artifacts": [{"artifactId": "a-1", "parts": [{"text": "the whole answer"}]}]
        })),
    };
    let answer = client.query(&authority(), RemoteQuery::ByRequest(RequestId("req-1".into()))).await;
    assert_eq!(answer, expected);

    // The index lookup bound the original request id, over the same
    // extension-declaring binding, and the evidence came from GetTask.
    let calls = agent.calls();
    assert_eq!(calls.len(), 4, "two late lookups, one index hit, one task read");
    assert_eq!(calls[2].0["method"], "GetTaskByRequest");
    assert_eq!(calls[2].0["params"]["requestId"], "req-1", "bound to the original operation's request identity");
    assert_eq!(calls[2].1.as_deref(), Some(QUERY_BY_REQUEST_EXT), "the negotiated extension carries the adapter contract");
    assert_eq!(calls[3].0["method"], "GetTask");
    assert_eq!(calls[3].0["params"]["id"], "rt-late");
}

#[tokio::test]
async fn a_task_found_by_index_but_gone_reads_as_not_found() {
    let (agent, client) = agent(vec![QUERY_BY_REQUEST_EXT.to_owned()]).await;
    // The index still knows the request, the task record does not.
    agent.on("GetTaskByRequest", Answer::Result(json!({"id": "rt-gone"})));
    agent.on("GetTask", Answer::Rpc(-32001));
    assert_eq!(
        client.query(&authority(), RemoteQuery::ByRequest(RequestId("req-1".into()))).await,
        QueryOutcome::NotFound
    );
}

#[tokio::test]
async fn a_standard_profile_without_the_extension_stays_unsupported() {
    let (agent, client) = agent(Vec::new()).await;
    // The standard A2A profile: no negotiated index, no by-request lookup.
    // The call upstream keeps its verification obligation and warning.
    assert_eq!(
        client.query(&authority(), RemoteQuery::ByRequest(RequestId("req-1".into()))).await,
        QueryOutcome::Unsupported
    );
    // Asking by task still reads the standard way.
    agent.on("GetTask", Answer::Result(done_task()));
    assert_eq!(
        client.query(&authority(), RemoteQuery::ByTask(RemoteTaskRef { task_id: "rt-late".into(), context_id: None })).await,
        QueryOutcome::Found {
            remote: RemoteTaskRef { task_id: "rt-late".into(), context_id: Some("ctx-late".into()) },
            status: TaskStatus::Completed,
            snapshot: Payload::Inline(json!({
                "state": "completed",
                "artifacts": [{"artifactId": "a-1", "parts": [{"text": "the whole answer"}]}]
            })),
        }
    );
}
