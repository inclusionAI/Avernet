//! GW admission through a real Worker, Connector and loopback A2A agent.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "integration assertions")]
mod processor_support;
mod support;

use async_trait::async_trait;
use axum::{
    Json, Router,
    body::Body,
    extract::{Extension, State},
    http::Request,
    response::IntoResponse,
    routing::post,
};
use bcs_a2a_http::dist_agent::{A2aDistAgentClient, DistAgentResolver, ResolvedAgent};
use bcs_direct_a2a_runner::DirectA2aRunner;
use bcs_dist_connector::{
    ConnectorConfig, ConnectorEngine, ConnectorPorts, rules::DistConnectorRules,
};
use bcs_dist_model::*;
use bcs_dist_worker::{RunnerRegistry, WorkerOutputReader, WorkerTaskAdmission};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{
    application::{
        dist_gateway::GatewayCaller,
        principal::{BotActor, CallerContext},
    },
    port::{dist_agent::*, dist_runtime::DistRuntimeControlPort},
    types::{
        a2a::A2aUpdateMode,
        a2a_bot::{A2aBinding, A2aInterface},
    },
};
use futures::{StreamExt, stream};
use processor_support::Fixture;
use serde_json::{Value, json};
use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tower::ServiceExt;

struct Directory(AtomicBool);

#[async_trait]
impl DistSessionDirectoryPort for Directory {
    async fn authorize(
        &self,
        scope: &TaskScope,
        target: &SessionTarget,
    ) -> Result<(), DirectoryError> {
        assert_eq!(scope.principal.0, "bot:caller");
        assert_eq!(target, &SessionTarget::DirectAgent { agent: AgentId("analyst".into()) });
        self.0.load(Ordering::SeqCst).then_some(()).ok_or(DirectoryError::AccessDenied)
    }

    async fn resolve(
        &self,
        _: &TaskScope,
        target: &SessionTarget,
    ) -> Result<FrozenSessionProposal, DirectoryError> {
        let SessionTarget::DirectAgent { agent } = target else {
            return Err(DirectoryError::NotFound);
        };
        let config = json!({"agent": agent});
        Ok(FrozenSessionProposal {
            target: target.clone(),
            authorized_agents: vec![AuthorizedAgent {
                agent: agent.clone(),
                pool: PoolId("pool".into()),
            }],
            mode_envelope: ModeConfigEnvelope {
                mode: CollaborationMode::DirectA2a,
                mode_version: 1,
                revision: "1".into(),
                digest: bcs_dist_encoding::digest_json(&config),
                payload: Payload::Inline(config),
            },
            limits: processor_support::spec().limits,
        })
    }
}

fn gateway(f: &Fixture, directory: Arc<Directory>) -> Router {
    let clock = f.clock.clone();
    let admission = WorkerTaskAdmission::new(
        f.inbox.clone(),
        Arc::new(move || bcs_dist_store::inbox::Clock::now(clock.as_ref())),
    );
    let output = WorkerOutputReader::new(
        f.feed.clone(),
        f.archive.clone(),
        Arc::new(bcs_dist_a2a_output::A2aOutputAdapter),
    );
    let core = bcs_dist_gateway::DistGatewayCore::new(
        Arc::new(admission),
        Arc::new(output),
        f.coordination.clone(),
    );
    let gateway = bcs_app_dist_gateway::DistGateway::new(
        Arc::new(core),
        directory,
        Arc::new(bcs_app_dist_gateway::SystemClock),
        bcs_app_dist_gateway::DistGatewayConfig::new(1),
    )
    .unwrap();
    bcs_dist_gateway_http::router(
        Arc::new(gateway),
        bcs_dist_gateway_http::GatewayHttpConfig::new("http://localhost"),
    )
    .layer(Extension(GatewayCaller {
        tenant: TenantId("t".into()),
        caller: CallerContext::Bot(BotActor { bot_uuid: "caller".into() }),
    }))
}

async fn rpc(app: &Router, method: &str, params: Value) -> Value {
    let request = Request::post("/a2a/agents/analyst")
        .header("content-type", "application/json")
        .body(Body::from(
            json!({"jsonrpc":"2.0","id":"client","method":method,"params":params}).to_string(),
        ))
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let bytes = axum::body::to_bytes(response.into_body(), 1_000_000).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

async fn send(app: &Router, text: &str, context: Option<&str>, task: Option<&str>) -> Value {
    let mut message = json!({"messageId":text, "role":"ROLE_USER", "parts":[{"text":text}]});
    if let Some(sid) = context {
        message["contextId"] = json!(sid);
    }
    if let Some(tid) = task {
        message["taskId"] = json!(tid);
    }
    rpc(app, "SendMessage", json!({"message":message, "configuration":{"returnImmediately":true}}))
        .await
}

async fn wait_for(app: &Router, worker: &Fixture, tid: &str, state: &str) -> Value {
    let mut observed = Value::Null;
    let pending = tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            worker.step().await.unwrap();
            let result = rpc(app, "GetTask", json!({"id":tid})).await;
            let task = &result["result"];
            assert_ne!(task["status"]["state"], "TASK_STATE_FAILED", "{result}");
            if task["status"]["state"] == state {
                return task.clone();
            }
            observed = result;
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await;
    pending.unwrap_or_else(|_| panic!("task {tid} never reached {state}, last GetTask {observed}"))
}

struct AgentRoute(String);
#[async_trait]
impl DistAgentResolver for AgentRoute {
    async fn resolve(&self, target: &AgentId) -> Result<ResolvedAgent, String> {
        assert_eq!(target.0, "analyst");
        Ok(ResolvedAgent {
            interface: A2aInterface {
                url: self.0.clone(),
                tenant: None,
                protocol_version: "1.0".into(),
                binding: A2aBinding::JsonRpc,
                update_mode: A2aUpdateMode::Stream,
            },
            bearer: None,
            extensions: vec![],
        })
    }
}

type Calls = Arc<Mutex<Vec<Value>>>;

// Scripted A2A wire responses. The asking stream stays live until the reply
// arrives, just like an agent that waits for a user before sending its result.
async fn downstream(State(calls): State<Calls>, Json(request): Json<Value>) -> impl IntoResponse {
    assert_eq!(request["method"], "SendStreamingMessage");
    let message = &request["params"]["message"];
    calls.lock().unwrap().push(message.clone());
    let context = &message["contextId"];
    let text = message["parts"][0]["text"].as_str().unwrap();
    let task_id = if text == "first" { "remote1" } else { "remote2" };
    let delta = |s: &str, append: bool| {
        json!({"artifactUpdate":{"taskId":task_id,"contextId":context,
        "artifact":{"artifactId":"out", "parts":[{"text":s}]},"append":append}})
    };
    let mut events = vec![
        json!({"task":{"id":task_id,"contextId":context,"status":{"state":"TASK_STATE_WORKING"}}}),
    ];
    match text {
        "first" => events.extend([delta("stale", false), delta("hello", false), delta("!", true)]),
        "second" => {
            events.push(json!({"statusUpdate":{"taskId":task_id,"contextId":context,"status":{
            "state":"TASK_STATE_INPUT_REQUIRED",
            "message":{"role":"ROLE_AGENT","messageId":"prompt","parts":[{"text":"Continue?"}]}}}}))
        }
        "yes" => events.push(delta("done", false)),
        _ => panic!("unexpected downstream send {text}"),
    }
    if text != "second" {
        events.push(json!({"statusUpdate":{"taskId":task_id,"contextId":context,"status":{"state":"TASK_STATE_COMPLETED"}}}));
    }
    let wire: String = events
        .into_iter()
        .map(|result| {
            format!("data: {}\n\n", json!({"jsonrpc":"2.0","id":request["id"],"result":result}))
        })
        .collect();
    let body = if text == "second" {
        Body::from_stream(stream::iter([Ok::<_, std::io::Error>(wire)]).chain(stream::pending()))
    } else {
        Body::from(wire)
    };
    ([("content-type", "text/event-stream")], body)
}

#[tokio::test]
async fn direct_rounds_stream_and_resume_on_the_original_downstream_context() {
    let f = Fixture::with_shards(
        Arc::new(RunnerRegistry::new(DirectA2aRunner)),
        64,
        128,
        256,
        vec![ShardId(0)],
    )
    .await;
    let directory = Arc::new(Directory(AtomicBool::new(true)));
    let app = gateway(&f, directory.clone());
    let first = send(&app, "first", None, None).await;
    let tid = first["result"]["task"]["id"].as_str().expect("first task");
    let sid = first["result"]["task"]["contextId"].as_str().expect("first session");
    directory.0.store(false, Ordering::SeqCst);
    assert!(send(&app, "denied", Some(sid), None).await.get("error").is_some());

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
    let addr = listener.local_addr().unwrap();
    let calls = Calls::default();
    let remote = Router::new().route("/rpc", post(downstream)).with_state(calls.clone());
    let server = tokio::spawn(async move {
        axum::serve(listener, remote).await.unwrap();
    });
    let client = A2aDistAgentClient::new(
        Arc::new(AgentRoute(format!("http://{addr}/rpc"))),
        OutboundUrlGuard::new(false, true),
        None,
    );
    let mut config = ConnectorConfig::new(
        Instance { node: "connector".into(), incarnation: "1".into() },
        PoolId("pool".into()),
    );
    config.poll_interval = Duration::from_millis(30);
    config.receive_timeout = Duration::from_millis(30);
    config.control_address = "http://connector.example.test".into();
    let connector = ConnectorEngine::new(
        config,
        ConnectorPorts {
            queue: Arc::new(f.queue.clone()),
            inbox: f.inbox.clone(),
            agent: Arc::new(client),
            membership: f.coordination.clone(),
            blobs: f.archive.clone(),
            rules: Arc::new(DistConnectorRules),
        },
    )
    .unwrap();
    let running = connector.clone();
    let process = tokio::spawn(async move { running.run().await });
    f.warm().await;
    let task = wait_for(&app, &f, tid, "TASK_STATE_COMPLETED").await;
    assert_eq!(task["artifacts"][0]["parts"][0]["text"], "hello!");

    directory.0.store(true, Ordering::SeqCst);
    let next = send(&app, "second", Some(sid), None).await;
    let next_tid = next["result"]["task"]["id"].as_str().unwrap();
    let asking = wait_for(&app, &f, next_tid, "TASK_STATE_INPUT_REQUIRED").await;
    assert_eq!(asking["status"]["message"]["parts"][0]["text"], "Continue?");
    let reply = send(&app, "yes", Some(sid), Some(next_tid)).await;
    assert!(reply.get("error").is_none(), "{reply}");
    let task = wait_for(&app, &f, next_tid, "TASK_STATE_COMPLETED").await;
    assert_eq!(task["artifacts"][0]["parts"][0]["text"], "done");
    connector.drain(Duration::from_secs(3)).await.unwrap();
    process.await.unwrap().unwrap();
    server.abort();
    let calls = calls.lock().unwrap();
    assert_eq!(calls.len(), 3);
    for call in calls.iter() {
        assert_eq!(call["contextId"], format!("{sid}:0"));
    }
    assert_eq!(calls[2]["taskId"], "remote2");
    assert_eq!(calls[2]["messageId"], format!("{next_tid}.agent.1:in:1"));
}

// Hold the Connector at its reliable Inbox boundary so the test controls the
// exact ordering of the remote terminal event and the Worker's grace timer.
#[tokio::test(start_paused = true)]
async fn a_confirmed_abort_keeps_the_context_and_an_unconfirmed_abort_rotates_it() {
    use bcs_service_api::core::dist_connector::{DistConnectorCoreService, Report, ReportCursor};
    use bcs_service_api::port::repo::distributed::{
        dispatch::DispatchControlPort, inbox::InboxProducerPort, session::SessionStorePort,
    };

    for (terminal_kind, grace_due, generation) in [
        (Some(AgentEventKind::Error), false, 0),
        (Some(AgentEventKind::Error), true, 0),
        (Some(AgentEventKind::Final), true, 0),
        (None, true, 1),
    ] {
        let f = Fixture::with_shards(
            Arc::new(RunnerRegistry::new(DirectA2aRunner)),
            64,
            128,
            256,
            vec![ShardId(0)],
        )
        .await;
        let app = gateway(&f, Arc::new(Directory(AtomicBool::new(true))));
        let submitted = send(&app, "old", None, None).await;
        let sid = Sid(submitted["result"]["task"]["contextId"].as_str().unwrap().to_owned());
        let old_tid = submitted["result"]["task"]["id"].as_str().unwrap();
        let old_did = Did(format!("{old_tid}.agent.1"));
        f.warm().await;
        wait_for(&app, &f, old_tid, "TASK_STATE_WORKING").await;
        let old = f.dispatch.get(&old_did).await.unwrap().unwrap();
        let queued = send(&app, "next", Some(&sid.0), None).await;
        let next_tid = queued["result"]["task"]["id"].as_str().unwrap();
        let (cancel, _) = tokio::join!(
            rpc(&app, "CancelTask", json!({"id":old_tid})),
            wait_for(&app, &f, old_tid, "TASK_STATE_CANCELED"),
        );
        assert_eq!(cancel["result"]["status"]["state"], "TASK_STATE_CANCELED", "{cancel}");
        for _ in 0..12 {
            f.step().await.unwrap();
        }
        let cp = f.coordination.load(&sid).await.unwrap().unwrap().checkpoint;
        assert_eq!(cp.pending_cancel_did.as_ref(), Some(&old_did));
        assert_eq!(cp.downstream_generation, 0);
        let next_did = Did(format!("{next_tid}.agent.1"));
        assert!(f.dispatch.get(&next_did).await.unwrap().is_none());
        if let Some(kind) = terminal_kind {
            let state = if kind == AgentEventKind::Final { "completed" } else { "canceled" };
            let Report::Key(terminal) = DistConnectorRules.report(
                &old.view,
                &mut ReportCursor { holder_epoch: HolderEpoch(1), last_seq: InputSeq(0) },
                kind,
                None,
                Payload::Inline(json!({"state":state})),
            ) else {
                panic!("expected reliable terminal event")
            };
            f.inbox.admit_connector(terminal).await.unwrap();
        }
        if grace_due {
            // The next Worker visit sees both the queued input and the due deadline.
            let deadline = cp
                .deadlines
                .iter()
                .find(|d| matches!(d.kind, DeadlineKind::CancelGrace { .. }))
                .unwrap();
            f.clock.0.store(deadline.at.0, Ordering::SeqCst);
        }
        wait_for(&app, &f, next_tid, "TASK_STATE_WORKING").await;
        let next = f.dispatch.get(&next_did).await.unwrap().unwrap();
        assert_eq!(next.view.spec.downstream_context, Some(format!("{}:{generation}", sid.0)));
        let cp = f.coordination.load(&sid).await.unwrap().unwrap().checkpoint;
        assert_eq!(cp.downstream_generation, generation);
        assert!(cp.pending_cancel_did.is_none());
    }
}
