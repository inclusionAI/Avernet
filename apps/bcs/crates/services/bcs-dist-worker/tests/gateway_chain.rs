//! Real HTTP adapter → admission → Inbox → Worker → TaskFeed → A2A.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "joint behavior assertions"
)]
mod processor_support;
mod support;
use axum::{Router, body::Body, extract::Extension, http::Request};
use bcs_dist_model::*;
use bcs_service_api::core::dist_runner::*;
use bcs_service_api::{
    application::{
        dist_gateway::*,
        principal::{BotActor, CallerContext},
    },
    port::{
        dist_agent::*,
        repo::distributed::{session::*, task_feed::*},
    },
};
use futures::StreamExt;
use processor_support::*;
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
use tower::ServiceExt;

#[derive(Default)]
struct Groups {
    changed: AtomicBool,
}
#[async_trait::async_trait]
impl DistGroupDirectoryPort for Groups {
    async fn resolve(
        &self,
        _: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        let mut s = spec();
        if self.changed.load(Ordering::SeqCst) {
            s.workflow.revision = "updated".into();
        }
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: s.workflow,
            participants: s.participants,
            limits: s.limits,
        })
    }
    async fn load_workflow(
        &self,
        _: &TaskScope,
        _: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        unreachable!()
    }
}
fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t".into()),
        principal: PrincipalId("bot:caller".into()),
    }
}
fn app(f: &Fixture, groups: Arc<Groups>) -> Router {
    let c = f.clock.clone();
    let admission = bcs_dist_worker::WorkerTaskAdmission::new(
        f.inbox.clone(),
        Arc::new(move || bcs_dist_store::inbox::Clock::now(c.as_ref())),
    );
    let output = bcs_dist_worker::WorkerOutputReader::new(
        f.feed.clone(),
        f.archive.clone(),
        Arc::new(bcs_dist_a2a_output::A2aOutputAdapter),
    );
    let core = bcs_dist_gateway::DistGatewayCore::new(
        Arc::new(admission),
        Arc::new(output),
        f.coordination.clone(),
    );
    // No group surface hosted by this joint chain: the policy denies and
    // no listing is wired; the legacy JSON-RPC chain stays as it was.
    struct GroupDeny;
    #[async_trait::async_trait]
    impl GroupGrantPolicy for GroupDeny {
        async fn grants(&self, _: &GatewayCaller, _: &GroupId) -> GroupRoundGrants {
            GroupRoundGrants { published: false, invoke: false, read: false, cancel: false }
        }
    }
    struct NoListing;
    #[async_trait::async_trait]
    impl bcs_service_api::port::repo::distributed::group_listing::GroupListingPort for NoListing {
        async fn first_page(
            &self,
            _: &TaskScope,
            _: &GroupId,
            _: &bcs_service_api::port::repo::distributed::group_listing::GroupIndexQuery,
            _: TimestampMillis,
        ) -> Result<bcs_service_api::port::repo::distributed::group_listing::GroupIndexPage, bcs_service_api::port::repo::distributed::common::StoreError> {
            Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied {
                code: "group_listing_unconfigured".to_owned(),
            })
        }
        async fn continue_page(
            &self,
            _: &TaskScope,
            _: &GroupId,
            _: &PageToken,
            _: bool,
            _: TimestampMillis,
        ) -> Result<bcs_service_api::port::repo::distributed::group_listing::GroupIndexPage, bcs_service_api::port::repo::distributed::common::StoreError> {
            Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied {
                code: "group_listing_unconfigured".to_owned(),
            })
        }
    }
    struct NoDiscovery;
    #[async_trait::async_trait]
    impl GroupDiscoveryService for NoDiscovery {
        async fn card(&self, _: &GatewayCaller, _: &GroupId) -> Result<GroupCard, GatewayError> {
            Err(GatewayError::NotFound)
        }
    }
    let gateway = bcs_app_dist_gateway::DistGateway::new(
        Arc::new(core),
        groups,
        Arc::new(GroupDeny),
        Arc::new(NoListing),
        Arc::new(bcs_app_dist_gateway::SystemClock),
        bcs_app_dist_gateway::DistGatewayConfig::new(1),
    )
    .unwrap();
    bcs_dist_gateway_http::router(
        Arc::new(gateway),
        Arc::new(NoDiscovery),
        bcs_dist_gateway_http::GatewayHttpConfig::new("http://localhost"),
    )
    .layer(Extension(GatewayCaller {
        tenant: TenantId("t".into()),
        caller: CallerContext::Bot(BotActor {
            bot_uuid: "caller".into(),
        }),
    }))
}
fn req(method: &str, params: Value) -> Request<Body> {
    Request::post("/a2a/g")
        .header("content-type", "application/json")
        .body(Body::from(
            json!({"jsonrpc":"2.0","id":"rpc","method":method,"params":params}).to_string(),
        ))
        .unwrap()
}
fn send(id: &str, text: &str) -> Request<Body> {
    req(
        "SendMessage",
        json!({"message":{"messageId":id,"role":"ROLE_USER","parts":[{"text":text}]},"configuration":{"returnImmediately":true}}),
    )
}
async fn json_response(app: &Router, r: Request<Body>) -> Value {
    let response = app.clone().oneshot(r).await.unwrap();
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[tokio::test]
async fn gateway_retries_survive_clock_config_and_lost_response_without_reexecution() {
    let runner = Arc::new(FinishingRunner {
        text: 70000,
        ..Default::default()
    });
    let f = Fixture::with_shards(runner.clone(), 64, 128, 256, vec![ShardId(0)]).await;
    let groups = Arc::new(Groups::default());
    let app = app(&f, groups.clone());
    // Drop the response body: actual admission has completed but caller received no result.
    let dropped = app.clone().oneshot(send("same", "hello")).await.unwrap();
    drop(dropped);
    groups.changed.store(true, Ordering::SeqCst);
    f.clock.0.fetch_add(1000, Ordering::SeqCst);
    let reply = json_response(&app, send("same", "hello")).await;
    let tid = Tid(reply["result"]["task"]["id"]
        .as_str()
        .unwrap_or_else(|| panic!("{reply}"))
        .into());
    let sid = Sid(reply["result"]["task"]["contextId"]
        .as_str()
        .unwrap()
        .into());
    let frozen = f.coordination.get_spec(&sid).await.unwrap().unwrap();
    assert_eq!(frozen.workflow.revision, "1");
    let conflict = json_response(&app, send("same", "changed")).await;
    assert!(conflict.get("error").is_some());
    f.warm().await;
    for _ in 0..100 {
        f.step().await.unwrap();
        if f.feed
            .load_snapshot(&scope(), &tid)
            .await
            .unwrap()
            .is_some_and(|t| t.content.status == TaskStatus::Completed)
        {
            break;
        }
    }
    let result = json_response(&app, req("GetTask", json!({"id":tid.0}))).await;
    assert_eq!(
        result["result"]["status"]["state"], "TASK_STATE_COMPLETED",
        "{result}"
    );
    assert_eq!(
        result["result"]["artifacts"][0]["parts"][0]["text"]
            .as_str()
            .unwrap()
            .len(),
        70000
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    let retry = json_response(&app, send("same", "hello")).await;
    assert_eq!(retry["result"]["task"]["id"], tid.0);
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
}

struct AppendingRunner {
    calls: AtomicUsize,
}
impl DistRunnerCoreService for AppendingRunner {
    fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: Value::Null,
        })
    }
    fn reduce(
        &self,
        _: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let RunnerInput::Start { tid, .. } = &inputs[0] else {
            panic!("unexpected input")
        };
        Ok(Decision {
            next_state: state.clone(),
            next_deadlines: vec![],
            actions: vec![
                ActionIntent::Show {
                    tid: tid.clone(),
                    node: NodeId("n".into()),
                    artifact_id: ArtifactId("answer".into()),
                    class: OutputClass::Output,
                    update: ArtifactUpdate::Upsert {
                        content: Payload::Inline(json!("seed:")),
                    },
                },
                ActionIntent::Show {
                    tid: tid.clone(),
                    node: NodeId("n".into()),
                    artifact_id: ArtifactId("answer".into()),
                    class: OutputClass::Output,
                    update: ArtifactUpdate::AppendText {
                        delta: Payload::Inline(json!("x".repeat(70000))),
                    },
                },
                ActionIntent::FinishTask {
                    tid: tid.clone(),
                    status: TerminalStatus::Completed,
                    result: Payload::Inline(json!({"done":true})),
                },
            ],
        })
    }
}
#[tokio::test]
async fn gateway_sse_append_and_get_task_agree_on_stable_artifact() {
    let runner = Arc::new(AppendingRunner {
        calls: AtomicUsize::new(0),
    });
    let f = Fixture::with_shards(runner.clone(), 64, 128, 256, vec![ShardId(0)]).await;
    let app = app(&f, Arc::new(Groups::default()));
    let sent = json_response(&app, send("stream", "hello")).await;
    let tid = sent["result"]["task"]["id"].as_str().unwrap().to_owned();
    let response = app
        .clone()
        .oneshot(req(
            "SendStreamingMessage",
            json!({"message":{"messageId":"stream","role":"ROLE_USER","parts":[{"text":"hello"}]}}),
        ))
        .await
        .unwrap();
    let mut body = response.into_body().into_data_stream();
    let read = async {
        let mut all = String::new();
        while let Some(chunk) = body.next().await {
            all.push_str(std::str::from_utf8(&chunk.unwrap()).unwrap());
        }
        all
    };
    let work = async {
        f.warm().await;
        for _ in 0..100 {
            f.step().await.unwrap();
            if f.feed
                .load_snapshot(&scope(), &Tid(tid.clone()))
                .await
                .unwrap()
                .is_some_and(|t| t.content.status == TaskStatus::Completed)
            {
                break;
            }
        }
    };
    let (frames, ()) = tokio::time::timeout(std::time::Duration::from_secs(20), async {
        tokio::join!(read, work)
    })
    .await
    .unwrap();
    let mut text = String::new();
    let mut artifact_id = None;
    for line in frames.lines().filter_map(|l| l.strip_prefix("data:")) {
        let frame: Value = serde_json::from_str(line.trim()).unwrap();
        if let Some(update) = frame["result"].get("artifactUpdate") {
            let id = update["artifact"]["artifactId"].as_str().unwrap();
            if let Some(previous) = &artifact_id {
                assert_eq!(previous, id);
            } else {
                artifact_id = Some(id.to_owned());
            }
            if update["append"] != true {
                text.clear();
            }
            text.push_str(update["artifact"]["parts"][0]["text"].as_str().unwrap());
        }
    }
    let result = json_response(&app, req("GetTask", json!({"id":tid}))).await;
    assert_eq!(text, "seed:".to_owned() + &"x".repeat(70000));
    assert_eq!(result["result"]["artifacts"][0]["parts"][0]["text"], text);
    assert_eq!(
        result["result"]["artifacts"][0]["artifactId"],
        artifact_id.unwrap()
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
}
