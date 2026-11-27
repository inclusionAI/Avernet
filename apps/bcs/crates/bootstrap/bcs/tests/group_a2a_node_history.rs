//! Mounted Group REST -> Worker -> Connector -> real A2A HTTP requests.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "wire assertions")]
#[path = "group_a2a_companion_support/mod.rs"]
mod group_a2a_companion_support;

use async_trait::async_trait;
use axum::{Json, Router, body::Body, extract::{Extension, State}, http::{Request, StatusCode}, routing::post};
use bcs_a2a_http::dist_agent::{A2aDistAgentClient, DistAgentResolver, ResolvedAgent};
use bcs_dist_gateway_http::GROUPS_PREFIX;
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{
    port::{dist_agent::*, dist_runtime::DistRuntimeControlPort, repo::distributed::{
        dispatch::DispatchControlPort, group_round::RoundBudgetProfile, node_history::*,
        result::BlobStorePort, session::SessionStorePort,
    }},
    types::{a2a::A2aUpdateMode, a2a_bot::{A2aBinding, A2aInterface}},
};
use group_a2a_companion_support::*;
use serde_json::{Value, json};
use std::{collections::BTreeMap, num::NonZeroU64, sync::{Arc, Mutex}, time::Duration};
use tower::ServiceExt;

struct SharedBot;

#[async_trait]
impl DistGroupDirectoryPort for SharedBot {
    async fn resolve(&self, scope: &TaskScope, group: &GroupId) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        let mut definition = Catalog.resolve(scope, group).await?;
        for participant in &mut definition.participants {
            participant.target.agent = AgentId("one-shared-bot".into());
        }
        Ok(definition)
    }

    async fn load_workflow(&self, scope: &TaskScope, workflow: &WorkflowRef) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Catalog.load_workflow(scope, workflow).await
    }
}

struct Resolver(String);
#[async_trait]
impl DistAgentResolver for Resolver {
    async fn resolve(&self, authority: &CallAuthority) -> Result<ResolvedAgent, String> {
        assert_eq!(authority.binding.agent.0, "one-shared-bot");
        Ok(ResolvedAgent {
            interface: A2aInterface {
                url: self.0.clone(), tenant: None, protocol_version: "1.0".into(),
                binding: A2aBinding::JsonRpc, update_mode: A2aUpdateMode::Poll,
            },
            bearer: None,
            extensions: Vec::new(),
        })
    }
}

struct OnceNotSent {
    real_http: Arc<A2aDistAgentClient>,
    requests: Mutex<Vec<StableSendRequest>>,
}

#[async_trait]
impl DistAgentClientPort for OnceNotSent {
    async fn start(&self, authority: &CallAuthority, request: StableSendRequest) -> SendOutcome {
        if request.node.0 == NODE_RESEARCH
            && matches!(&request.payload, Payload::Inline(value) if value["generation"] == json!(2))
        {
            let mut requests = self.requests.lock().unwrap();
            requests.push(request.clone());
            if requests.len() == 1 {
                // No network call at all: the strongest `NotSent` evidence.
                return SendOutcome::NotSent { code: "fixture_preflight_not_sent".into() };
            }
        }
        self.real_http.start(authority, request).await
    }

    async fn send_input(&self, authority: &CallAuthority, remote: &RemoteTaskRef, request: StableSendRequest) -> SendOutcome {
        self.real_http.send_input(authority, remote, request).await
    }

    async fn query(&self, authority: &CallAuthority, query: RemoteQuery) -> QueryOutcome {
        self.real_http.query(authority, query).await
    }

    async fn subscribe(&self, authority: &CallAuthority, remote: &RemoteTaskRef, after: Option<ReplayToken>) -> Result<RemoteEventStream, AgentReadError> {
        self.real_http.subscribe(authority, remote, after).await
    }

    async fn cancel(&self, authority: &CallAuthority, remote: &RemoteTaskRef, request_id: RequestId) -> CancelOutcome {
        self.real_http.cancel(authority, remote, request_id).await
    }
}

#[derive(Default)]
struct Remote {
    sends: Mutex<Vec<Value>>,
    tasks: Mutex<BTreeMap<String, Value>>,
}

async fn rpc(State(remote): State<Arc<Remote>>, Json(request): Json<Value>) -> Json<Value> {
    let result = match request["method"].as_str().unwrap() {
        "SendMessage" => {
            let message = request["params"]["message"].clone();
            let text = message["parts"][0]["text"].as_str().unwrap();
            // Research has no predecessor; review must get the public product
            // produced by this round, never the private status message.
            let review = message["parts"].as_array().unwrap().iter()
                .any(|part| !part["data"]["upstream"][NODE_RESEARCH].is_null());
            let (node, other) = if review { (NODE_REVIEW, NODE_RESEARCH) } else { (NODE_RESEARCH, NODE_REVIEW) };
            if review {
                let upstream = message["parts"].as_array().unwrap().iter()
                    .find_map(|part| part["data"].get("upstream")).unwrap();
                assert!(upstream[NODE_RESEARCH].to_string().contains(&format!("research product for {text}")));
                assert!(!upstream.to_string().contains("node-private"));
            }
            assert!(!message.to_string().contains(&format!("{other}-node-private")));
            let id = format!("remote-{}", message["messageId"].as_str().unwrap());
            let context = message["contextId"].as_str().unwrap_or("missing-context");
            let done = json!({
                "id": id, "contextId": context,
                "status": {"state":"TASK_STATE_COMPLETED", "message": {
                    "messageId":format!("reply-{id}"), "role":"ROLE_AGENT",
                    "parts":[{"text":format!("{node}-node-private for {text}")}],
                }},
                "artifacts":[{"artifactId":format!("product-{node}"),"parts":[{"text":format!("{node} product for {text}")}]}],
            });
            remote.sends.lock().unwrap().push(message.clone());
            assert!(remote.tasks.lock().unwrap().insert(id.clone(), done).is_none(), "start must not re-execute");
            json!({"task":{"id":id,"contextId":context,"status":{"state":"TASK_STATE_WORKING"}}})
        }
        "GetTask" => remote.tasks.lock().unwrap()[request["params"]["id"].as_str().unwrap()].clone(),
        method => panic!("unexpected agent method: {method}"),
    };
    Json(json!({"jsonrpc":"2.0","id":request["id"],"result":result}))
}

async fn send(app: &Router, context: Option<&Sid>, id: &str, text: &str) -> (Sid, Tid) {
    let mut message = json!({"messageId":id,"role":"ROLE_USER","parts":[{"text":text}]});
    if let Some(context) = context { message["contextId"] = json!(context.0); }
    let response = app.clone().oneshot(Request::post(format!("{GROUPS_PREFIX}/{GROUP}/message:send"))
        .header("content-type", "application/json")
        .body(Body::from(json!({"message":message,"configuration":{"blocking":false}}).to_string()))
        .unwrap()).await.unwrap();
    let status = response.status();
    let body: Value = serde_json::from_slice(&axum::body::to_bytes(response.into_body(), 1 << 20).await.unwrap()).unwrap();
    assert_eq!(status, StatusCode::OK, "{body}");
    let task = body.get("task").expect("SDK send response must carry a Task");
    (Sid(task["contextId"].as_str().unwrap().into()), Tid(task["id"].as_str().unwrap().into()))
}

fn round(sid: &Sid, tid: &Tid, generation: u64) -> RoundKey {
    RoundKey { scope: scope(), group: group_id(), sid: sid.clone(), tid: tid.clone(), generation: NonZeroU64::new(generation).unwrap() }
}

async fn completed(world: &CompanionWorld, round: &RoundKey) {
    tokio::time::timeout(Duration::from_secs(50), async {
        loop {
            world.drive(2).await;
            if let Some(proof) = world.round_view(round).await.and_then(|v| v.terminal) {
                assert_eq!(proof.status, TerminalStatus::Completed);
                break;
            }
        }
    }).await.expect("mounted strict round did not complete");
}

fn history(message: &Value) -> &Value {
    let mut histories = message["parts"].as_array().unwrap().iter().filter_map(|part| part["data"].get("nodeHistory"));
    let history = histories.next().expect("strict node's history reached the actual HTTP peer");
    assert!(histories.next().is_none(), "history appears only once");
    history
}

async fn start_http_peer() -> (Arc<Remote>, Arc<A2aDistAgentClient>, tokio::task::JoinHandle<()>) {
    let remote = Arc::new(Remote::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let peer = Router::new().route("/", post(rpc)).with_state(remote.clone());
    let endpoint = tokio::spawn(async move { axum::serve(listener, peer).await.unwrap() });
    let client = Arc::new(A2aDistAgentClient::new(
        Arc::new(Resolver(format!("http://{addr}/"))), OutboundUrlGuard::new(false, true), None,
    ));
    (remote, client, endpoint)
}

#[tokio::test]
async fn mounted_same_bot_nodes_keep_private_history_across_rounds_and_worker_takeover() {
    let (remote, client, endpoint) = start_http_peer().await;
    let client = Arc::new(OnceNotSent { real_http: client, requests: Mutex::new(Vec::new()) });
    let world = CompanionWorld::assemble_with_downstream(Arc::new(SharedBot), client.clone()).await;
    let (connector, engine) = world.connect_with_config("history-original", |config| {
        config.backoff_min = Duration::from_secs(10);
        config.backoff_max = Duration::from_secs(10);
    });
    let app = world.assembly.router.clone().layer(Extension(caller()));

    let (sid, first) = send(&app, None, "history-first", "first turn").await;
    completed(&world, &round(&sid, &first, 1)).await;
    let (same_sid, second) = send(&app, Some(&sid), "history-second", "second turn").await;
    assert_eq!(sid, same_sid);

    // A row committed after B's admission but before the successor can
    // dispatch must be invisible under B's admission cut, not only during
    // the first call but on any replay of its stable input identity.
    let definition = Catalog.resolve(&scope(), &group_id()).await.unwrap();
    let key = NodeHistoryKey {
        scope: scope(), group: group_id(), sid: sid.clone(), workflow_ref: definition.workflow,
        node_id: NodeId(NODE_RESEARCH.into()),
    };
    world.assembly.roles.node_history.append_once(&key, &InteractionId("late-after-admission".into()), &NodeHistoryEntry {
        round_generation: NonZeroU64::MIN,
        kind: NodeHistoryKind::NodeResponse,
        content: Payload::Inline(json!({"text":"late row must not leak"})),
        round_outcome: Some(TerminalStatus::Completed),
    }).await.unwrap();
    world.workers.lock().unwrap()[0].stopped = true;
    world.add_worker("history-worker-takeover").await;
    let did = node_did(&second, 2, NODE_RESEARCH);
    let old = tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            world.drive(2).await;
            if let Some(record) = world.assembly.roles.dispatch.get(&did).await.unwrap()
                && record.view.execution.phase == DispatchPhase::Claimed
                && record.view.execution.node_input_delivery == Some(DeliveryState::Undelivered)
            {
                break record.view;
            }
        }
    }).await.expect("first Connector did not persist definite no-send");
    assert_eq!(client.requests.lock().unwrap().len(), 1);
    let _ = engine.drain(Duration::ZERO).await.unwrap();
    connector.abort();
    let (successor, _) = world.connect_successor("history-successor");
    let second_round = round(&sid, &second, 2);
    completed(&world, &second_round).await;
    {
        let attempts = client.requests.lock().unwrap();
        assert_eq!(attempts.len(), 2);
        assert_eq!(attempts[0], attempts[1], "a successor must replay the exact frozen input including request/context identity");
    }
    let new = world.assembly.roles.dispatch.get(&did).await.unwrap().unwrap().view;
    assert_ne!(old.execution.holder, new.execution.holder);
    assert_eq!(old.execution.send_proof, new.execution.send_proof);
    assert_eq!(new.execution.node_responses.len(), 1);
    let pin = new.spec.node_history.unwrap();
    let manifest: HistoryManifest = serde_json::from_slice(
        &world.assembly.roles.archive.get(&pin.cut_manifest).await.unwrap().unwrap(),
    ).unwrap();
    let cut = manifest.cuts.iter().find(|cut| cut.history.node_id.0 == NODE_RESEARCH).unwrap();
    let error = world.assembly.roles.node_history.load_node_input(
        &second_round, &cut.history.node_id, cut, &RoundBudgetProfile::v1(),
    ).await.unwrap_err();
    assert!(error.to_string().contains(CODE_NODE_INPUT_PIN_RELEASED));
    assert_eq!(send(&app, Some(&sid), "history-second", "second turn").await, (sid.clone(), second));

    let (fresh, third) = send(&app, None, "history-fresh", "fresh context").await;
    assert_ne!(fresh, sid);
    completed(&world, &round(&fresh, &third, 1)).await;

    let requests = remote.sends.lock().unwrap();
    assert_eq!(requests.len(), 6, "REST replay and takeover must not duplicate a send");
    let contexts: std::collections::BTreeSet<_> = requests.iter().map(|message| message["contextId"].as_str().unwrap()).collect();
    assert_eq!(contexts.len(), 6, "no two dispatches share a remote conversation");
    for (index, node) in [(2, NODE_RESEARCH), (3, NODE_REVIEW)] {
        let records = history(&requests[index])["entries"].as_array().unwrap();
        assert_eq!(records.len(), 2, "exactly one start and one response from round one");
        assert_eq!(records[0]["entry"]["kind"]["delivery"], "delivered");
        assert!(records[0].to_string().contains("first turn"));
        assert_eq!(records[1]["entry"]["round_outcome"], "completed");
        assert!(records[1].to_string().contains(&format!("{node}-node-private for first turn")));
        assert!(!requests[index].to_string().contains("late row must not leak"));
    }
    for index in [0, 1, 4, 5] {
        assert_eq!(history(&requests[index])["entries"], json!([]));
        assert!(!requests[index].to_string().contains("node-private"));
    }
    successor.abort();
    endpoint.abort();
}

#[tokio::test]
async fn history_write_failure_blocks_send_and_terminal_until_durable_repair() {
    use bcs_db_api::{DbPlugin, DbStatement};
    let (remote, client, endpoint) = start_http_peer().await;
    let world = CompanionWorld::assemble_with_downstream(Arc::new(SharedBot), client).await;
    let connector = world.start_connector();
    let app = world.assembly.router.clone().layer(Extension(caller()));
    let db = bcs_db_local::LocalSqliteDbPlugin::new_file(world.archive_path()).unwrap();
    db.execute(DbStatement::new(
        "CREATE TRIGGER block_history_pin BEFORE INSERT ON bcs_fsm_node_input_pins
         BEGIN SELECT RAISE(ABORT, 'injected pin failure'); END",
    )).await.unwrap();
    let (sid, tid) = send(&app, None, "history-fault", "durable turn").await;
    let key = round(&sid, &tid, 1);
    let did = node_did(&tid, 1, NODE_RESEARCH);
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            world.drive(2).await;
            let state = world.assembly.roles.coordination.load(&sid).await.unwrap().unwrap();
            if state.checkpoint.dispatch_seq.contains_key(&did) { break }
        }
    }).await.expect("first dispatch intent never committed");
    world.drive(20).await;
    assert!(remote.sends.lock().unwrap().is_empty());
    assert!(world.assembly.roles.dispatch.get(&did).await.unwrap().is_none());
    db.execute(DbStatement::new(
        "CREATE TRIGGER block_history_entries BEFORE INSERT ON bcs_fsm_node_history
         BEGIN SELECT RAISE(ABORT, 'injected interaction failure'); END",
    )).await.unwrap();
    db.execute(DbStatement::new("DROP TRIGGER block_history_pin")).await.unwrap();

    let review = node_did(&tid, 1, NODE_REVIEW);
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            world.drive(2).await;
            if world.assembly.roles.dispatch.get(&review).await.unwrap()
                .is_some_and(|r| r.view.execution.phase == DispatchPhase::Ended)
            { break }
        }
    }).await.expect("pin repair did not restore dispatch");
    world.drive(20).await;
    assert_eq!(remote.sends.lock().unwrap().len(), 2);
    assert!(world.round_view(&key).await.unwrap().terminal.is_none());
    db.execute(DbStatement::new("DROP TRIGGER block_history_entries")).await.unwrap();
    completed(&world, &key).await;
    assert_eq!(remote.sends.lock().unwrap().len(), 2, "repair must not repeat remote starts");
    connector.abort();
    endpoint.abort();
}
