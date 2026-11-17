//! The A2A endpoint over every layer: adapter → application → core → store
//! providers on the in-memory store model, with a fake Worker.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

#[path = "../../../../services/bcs-dist-gateway-store/tests/common/mod.rs"]
mod common;

use a2a::{Artifact, Message, Part, Role};
use async_trait::async_trait;
use axum::Router;
use axum::body::Body;
use axum::extract::Extension;
use axum::http::{Request, StatusCode};
use bcs_app_dist_gateway::{Clock, DistGateway, DistGatewayConfig};
use bcs_dist_gateway::DistGatewayCore;
use bcs_dist_gateway_http::{GatewayCaller, GatewayHttpConfig, cursor, router};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::application::principal::{BotActor, CallerContext};
use bcs_service_api::port::dist_agent::*;
use bcs_store_api::stream::StreamStorePlugin;
use common::{Ports, eid, ports, question, snapshot, status_event};
use futures::StreamExt;
use serde_json::{Value, json};
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::Arc;
use std::time::Duration;
use tower::ServiceExt;

struct Groups;
#[async_trait]
impl DistGroupDirectoryPort for Groups {
    async fn resolve(&self, _scope: &TaskScope, group: &GroupId) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        Ok(FrozenGroupDefinition {
            group: group.clone(),
            workflow: WorkflowRef {
                definition_id: "wf-research".into(),
                revision: "7".into(),
                digest: Digest("sha256:ab12".into()),
            },
            participants: vec![],
            limits: SessionLimits {
                max_queued_tasks: 16,
                max_task_steps: 200,
                task_timeout_millis: 1_800_000,
                max_inline_bytes: 65_536,
            },
        })
    }
    async fn load_workflow(
        &self,
        _scope: &TaskScope,
        _reference: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        Err(GroupDirectoryError::Unavailable)
    }
}

struct TestClock;
impl Clock for TestClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(common::NOW)
    }
}

fn caller() -> GatewayCaller {
    GatewayCaller {
        tenant: TenantId("t-ant".into()),
        caller: CallerContext::Bot(BotActor {
            bot_uuid: "pa-001".into(),
        }),
    }
}

fn app_over(gateway: Arc<dyn DistGatewayService>) -> Router {
    let mut config = GatewayHttpConfig::new("https://gw.example");
    config.blocking_send_wait = Duration::from_secs(5);
    config.cancel_wait = Duration::from_millis(100);
    router(gateway, config).layer(Extension(caller()))
}

fn app(p: &Ports) -> Router {
    let core = DistGatewayCore::new(p.inbox.clone(), p.tasks.clone(), p.sessions.clone(), p.blobs.clone());
    let mut config = DistGatewayConfig::new(64);
    config.snapshot_cover_retry = Duration::from_millis(10);
    config.snapshot_cover_limit = Duration::from_millis(300);
    let gateway = DistGateway::new(Arc::new(core), Arc::new(Groups), Arc::new(TestClock), config).unwrap();
    app_over(Arc::new(gateway))
}

fn rpc(method: &str, params: Value) -> Request<Body> {
    rpc_with(method, params, &[])
}

fn rpc_with(method: &str, params: Value, headers: &[(&str, &str)]) -> Request<Body> {
    let body = json!({ "jsonrpc": "2.0", "id": "r1", "method": method, "params": params });
    let mut request = Request::post("/a2a/grp-research-01")
        .header("content-type", "application/json")
        .header("a2a-version", "1.0");
    for (name, value) in headers {
        request = request.header(*name, *value);
    }
    request.body(Body::from(body.to_string())).unwrap()
}

async fn json_of(app: &Router, request: Request<Body>) -> Value {
    let response = app.clone().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(response.into_body(), 1 << 20).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn error_code(response: &Value) -> i64 {
    response["error"]["code"].as_i64().unwrap_or_else(|| panic!("no error in {response}"))
}

fn user_message(id: &str, text: &str, context: Option<&str>, task: Option<&str>) -> Value {
    let mut message = Message::new(Role::User, vec![Part::text(text)]);
    message.message_id = id.into();
    message.context_id = context.map(str::to_owned);
    message.task_id = task.map(str::to_owned);
    serde_json::to_value(message).unwrap()
}

fn send(id: &str, text: &str, context: Option<&str>, task: Option<&str>, immediately: bool) -> Request<Body> {
    rpc(
        "SendMessage",
        json!({
            "message": user_message(id, text, context, task),
            "configuration": { "returnImmediately": immediately },
        }),
    )
}

/// One parsed SSE frame: its `id:` and the JSON of its `data:`.
#[derive(Debug)]
struct Frame {
    id: Option<String>,
    data: Value,
}

fn parse_frames(text: &str) -> Vec<Frame> {
    text.split("\n\n")
        .filter_map(|block| {
            let mut id = None;
            let mut data = None;
            for line in block.lines() {
                if let Some(value) = line.strip_prefix("id:") {
                    id = Some(value.trim().to_owned());
                } else if let Some(value) = line.strip_prefix("data:") {
                    data = serde_json::from_str(value.trim()).ok();
                }
            }
            data.map(|data| Frame { id, data })
        })
        .collect()
}

/// Reads SSE frames until `count` arrived or the stream ended.
async fn read_frames(response: axum::response::Response, count: usize) -> Vec<Frame> {
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        response.headers()["content-type"].to_str().unwrap().starts_with("text/event-stream"),
        "a stream answers with SSE"
    );
    let mut body = response.into_body().into_data_stream();
    let mut text = String::new();
    loop {
        let frames = parse_frames(&text);
        if frames.len() >= count {
            return frames;
        }
        match tokio::time::timeout(Duration::from_secs(5), body.next()).await {
            Ok(Some(Ok(chunk))) => text.push_str(std::str::from_utf8(&chunk).unwrap()),
            Ok(_) => return parse_frames(&text),
            Err(_) => panic!("timed out after frames: {:?}", parse_frames(&text)),
        }
    }
}

fn artifact_event(tid: &str, id: EventId, text: &str) -> OutputEvent {
    let artifact = Artifact {
        artifact_id: "a-1".into(),
        name: None,
        description: None,
        parts: vec![Part::text(text)],
        metadata: None,
        extensions: None,
    };
    OutputEvent {
        id,
        tid: Tid(tid.into()),
        body: OutputBody::Artifact {
            node: NodeId("n1".into()),
            class: OutputClass::Output,
            content: Payload::Inline(json!({ "artifact": artifact, "append": true })),
        },
    }
}

async fn publish(p: &Ports, tid: &str, status: TaskStatus, covered: Option<EventId>, questions: &[&str]) {
    let mut task = snapshot(tid, status, covered, common::NOW);
    task.sid = Sid(tid.rsplit_once('.').unwrap().0.to_owned());
    task.scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    task.content.interactions = questions.iter().map(|id| question(id)).collect();
    p.worker.publish(&task).await;
}

async fn stream_len(p: &Ports, key: &str) -> usize {
    StreamStorePlugin::range(
        p.model.as_ref(),
        key,
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(1000).unwrap(),
        false,
    )
    .await
    .unwrap()
    .entries
    .len()
}

#[tokio::test]
async fn send_get_and_list_return_the_stored_task() {
    let p = ports();
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let task = &sent["result"]["task"];
    assert_eq!(task["status"]["state"], "TASK_STATE_SUBMITTED");
    let tid = task["id"].as_str().unwrap().to_owned();
    let sid = task["contextId"].as_str().unwrap().to_owned();
    assert!(tid.starts_with(&format!("{sid}.")));

    // The same message again is the same task; changed content is refused.
    let again = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    assert_eq!(again["result"]["task"]["id"], tid.as_str());
    // A retry that adds the context id it learned is still the same request.
    let with_context = json_of(&app, send("msg-1", "hello", Some(&sid), None, true)).await;
    assert_eq!(with_context["result"]["task"]["id"], tid.as_str());
    assert_eq!(error_code(&json_of(&app, send("msg-1", "bye", None, None, true)).await), -32602);

    let got = json_of(&app, rpc("GetTask", json!({ "id": tid }))).await;
    assert_eq!(got["result"]["id"], tid.as_str());
    assert_eq!(error_code(&json_of(&app, rpc("GetTask", json!({ "id": format!("{sid}.none") }))).await), -32001);

    json_of(&app, send("msg-2", "next", Some(&sid), None, true)).await;
    let listed = json_of(&app, rpc("ListTasks", json!({ "contextId": sid, "pageSize": 1 }))).await;
    assert_eq!(listed["result"]["tasks"].as_array().unwrap().len(), 1);
    let token = listed["result"]["nextPageToken"].as_str().unwrap().to_owned();
    assert!(!token.is_empty());
    let rest = json_of(&app, rpc("ListTasks", json!({ "contextId": sid, "pageSize": 1, "pageToken": token }))).await;
    assert_eq!(rest["result"]["tasks"].as_array().unwrap().len(), 1);
    assert_eq!(error_code(&json_of(&app, rpc("ListTasks", json!({}))).await), -32602);
}

#[tokio::test]
async fn admission_always_writes_wake_while_the_listener_contract_is_stubbed() {
    let p = ports();
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let sid = sent["result"]["task"]["contextId"].as_str().unwrap().to_owned();
    json_of(&app, send("msg-2", "next", Some(&sid), None, true)).await;
    json_of(&app, send("msg-3", "more", Some(&sid), None, true)).await;
    let shard = sid.split_once('.').unwrap().0;
    let prefix = format!("bcn:v1:c01:main:{shard}");
    assert_eq!(stream_len(&p, &format!("{prefix}:inbox:{sid}")).await, 3);
    // D1: without a listener read, every admitted input also wakes the shard.
    assert_eq!(stream_len(&p, &format!("{prefix}:wake")).await, 3);
}

#[tokio::test]
async fn a_streaming_send_sends_the_task_first_then_events_with_cursor_ids() {
    let p = Arc::new(ports());
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let tid = sent["result"]["task"]["id"].as_str().unwrap().to_owned();
    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            p.worker.append(&status_event(&tid, eid(1, 1), TaskStatus::Working)).await;
            p.worker.append(&artifact_event(&tid, eid(2, 1), "Q3 revenue grew ")).await;
            p.worker.append(&status_event(&tid, eid(3, 1), TaskStatus::InputRequired)).await;
        })
    };
    // The same message as a stream: admission answers from the first attempt.
    let request = rpc("SendStreamingMessage", json!({ "message": user_message("msg-1", "hello", None, None) }));
    let frames = read_frames(app.clone().oneshot(request).await.unwrap(), 10).await;
    worker.await.unwrap();
    assert_eq!(frames.len(), 4, "task, status, artifact, input-required, then the stream ends: {frames:?}");
    assert_eq!(frames[0].data["result"]["task"]["id"], tid.as_str());
    assert_eq!(frames[0].data["id"], "r1");
    assert_eq!(frames[1].data["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_WORKING");
    assert_eq!(frames[2].data["result"]["artifactUpdate"]["artifact"]["artifactId"], "a-1");
    assert_eq!(frames[2].data["result"]["artifactUpdate"]["append"], true);
    assert_eq!(frames[3].data["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED");

    // Every event id is an opaque cursor of this task at that event.
    let positions: Vec<String> = frames[1..]
        .iter()
        .map(|frame| {
            let id = frame.id.as_ref().expect("event frames carry an id");
            assert!(id.starts_with("1."));
            let decoded = cursor::decode(id).unwrap();
            assert_eq!(decoded.tid.0, tid);
            decoded.token.value
        })
        .collect();
    assert_eq!(positions, ["1-1", "2-1", "3-1"]);
}

#[tokio::test]
async fn last_event_id_resumes_without_a_snapshot_and_a_bad_one_gets_the_task_first() {
    let p = ports();
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let tid = sent["result"]["task"]["id"].as_str().unwrap().to_owned();
    for n in 1..=2 {
        p.worker.append(&artifact_event(&tid, eid(n, 1), "x")).await;
    }
    p.worker.append(&status_event(&tid, eid(3, 1), TaskStatus::InputRequired)).await;
    publish(&p, &tid, TaskStatus::Working, Some(eid(1, 1)), &[]).await;

    let resume_at = cursor::encode(&TaskCursor {
        sid: Sid(tid.rsplit_once('.').unwrap().0.to_owned()),
        tid: Tid(tid.clone()),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: "2-1".into(),
        },
    })
    .unwrap();
    let request = rpc_with("SubscribeToTask", json!({ "id": tid }), &[("last-event-id", &resume_at)]);
    let frames = read_frames(app.clone().oneshot(request).await.unwrap(), 10).await;
    assert_eq!(frames.len(), 1, "only what was missed: {frames:?}");
    assert_eq!(frames[0].data["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED");

    // No usable id: the stored task first, then the events after its coverage.
    for header in [None, Some("garbage")] {
        let headers: Vec<(&str, &str)> = header.map(|h| ("last-event-id", h)).into_iter().collect();
        let request = rpc_with("SubscribeToTask", json!({ "id": tid }), &headers);
        let frames = read_frames(app.clone().oneshot(request).await.unwrap(), 10).await;
        assert_eq!(frames.len(), 3, "{frames:?}");
        assert_eq!(frames[0].data["result"]["task"]["status"]["state"], "TASK_STATE_WORKING");
        assert_eq!(cursor::decode(frames[0].id.as_ref().unwrap()).unwrap().token.value, "1-1");
        assert!(frames[1].data["result"]["artifactUpdate"].is_object());
    }
}

#[tokio::test]
async fn a_blocking_reply_returns_the_task_once_it_finished() {
    let p = Arc::new(ports());
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let tid = sent["result"]["task"]["id"].as_str().unwrap().to_owned();
    p.worker.append(&status_event(&tid, eid(7, 1), TaskStatus::InputRequired)).await;
    publish(&p, &tid, TaskStatus::InputRequired, Some(eid(7, 1)), &["i-3"]).await;

    // A blocking send on a task that waits for input returns that state at once.
    let waiting = json_of(&app, rpc("GetTask", json!({ "id": tid }))).await;
    assert_eq!(waiting["result"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED");

    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(80)).await;
            p.worker.append(&status_event(&tid, eid(8, 1), TaskStatus::Working)).await;
            p.worker.append(&status_event(&tid, eid(9, 1), TaskStatus::Completed)).await;
            publish(&p, &tid, TaskStatus::Completed, Some(eid(9, 1)), &[]).await;
        })
    };
    // The answer: a message with the task id, blocking by default.
    let reply = rpc("SendMessage", json!({ "message": user_message("msg-2", "B", None, Some(&tid)) }));
    let replied = json_of(&app, reply).await;
    worker.await.unwrap();
    assert_eq!(replied["result"]["task"]["status"]["state"], "TASK_STATE_COMPLETED");

    // Finished: no cancel, no subscription; GetTask still answers.
    assert_eq!(error_code(&json_of(&app, rpc("CancelTask", json!({ "id": tid }))).await), -32002);
    assert_eq!(error_code(&json_of(&app, rpc("SubscribeToTask", json!({ "id": tid }))).await), -32004);
    let got = json_of(&app, rpc("GetTask", json!({ "id": tid }))).await;
    assert_eq!(got["result"]["status"]["state"], "TASK_STATE_COMPLETED");
    // A retried streaming send that finds its task finished gets the task and a closed stream.
    let retry = rpc("SendStreamingMessage", json!({ "message": user_message("msg-1", "hello", None, None) }));
    let frames = read_frames(app.clone().oneshot(retry).await.unwrap(), 10).await;
    assert_eq!(frames.len(), 1);
    assert_eq!(frames[0].data["result"]["task"]["status"]["state"], "TASK_STATE_COMPLETED");
}

#[tokio::test]
async fn cancel_returns_the_final_task_or_the_current_one_at_its_timeout() {
    let p = Arc::new(ports());
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let tid = sent["result"]["task"]["id"].as_str().unwrap().to_owned();
    // Nobody processes the cancel: after the bounded wait the current task is returned.
    let pending = json_of(&app, rpc("CancelTask", json!({ "id": tid }))).await;
    assert_eq!(pending["result"]["status"]["state"], "TASK_STATE_SUBMITTED");

    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(30)).await;
            p.worker.append(&status_event(&tid, eid(4, 1), TaskStatus::Canceled)).await;
            publish(&p, &tid, TaskStatus::Canceled, Some(eid(4, 1)), &[]).await;
        })
    };
    let canceled = json_of(&app, rpc("CancelTask", json!({ "id": tid }))).await;
    worker.await.unwrap();
    assert_eq!(canceled["result"]["status"]["state"], "TASK_STATE_CANCELED");
}

#[tokio::test]
async fn push_notifications_are_declared_and_answered_as_unsupported() {
    let p = ports();
    let app = app(&p);
    for method in [
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
    ] {
        assert_eq!(error_code(&json_of(&app, rpc(method, json!({}))).await), -32003, "{method}");
    }
    let with_webhook = rpc(
        "SendMessage",
        json!({
            "message": user_message("msg-1", "hello", None, None),
            "configuration": { "taskPushNotificationConfig": { "url": "https://client.example/hook" } },
        }),
    );
    assert_eq!(error_code(&json_of(&app, with_webhook).await), -32003);

    let card = app
        .clone()
        .oneshot(Request::get("/a2a/grp-research-01/.well-known/agent-card.json").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(card.status(), StatusCode::OK);
    let card: Value = serde_json::from_slice(&axum::body::to_bytes(card.into_body(), 1 << 20).await.unwrap()).unwrap();
    assert_eq!(card["capabilities"]["pushNotifications"], false);
    assert_eq!(card["capabilities"]["streaming"], true);
    assert_eq!(card["supportedInterfaces"][0]["url"], "https://gw.example/a2a/grp-research-01");
}

#[tokio::test]
async fn protocol_errors_are_reported_as_json_rpc_errors() {
    let p = ports();
    let app = app(&p);
    assert_eq!(error_code(&json_of(&app, rpc("tasks/get", json!({ "id": "x" }))).await), -32601);
    assert_eq!(error_code(&json_of(&app, rpc("GetTask", json!({ "nope": 1 }))).await), -32602);
    let old = rpc_with("GetTask", json!({ "id": "x" }), &[]);
    let (mut parts, body) = old.into_parts();
    parts.headers.insert("a2a-version", "0.3".parse().unwrap());
    assert_eq!(error_code(&json_of(&app, Request::from_parts(parts, body)).await), -32009);
    let broken = Request::post("/a2a/grp-research-01")
        .header("content-type", "application/json")
        .body(Body::from("{"))
        .unwrap();
    assert_eq!(error_code(&json_of(&app, broken).await), -32700);

    // Without a resolved caller the request is refused.
    let core = DistGatewayCore::new(p.inbox.clone(), p.tasks.clone(), p.sessions.clone(), p.blobs.clone());
    let gateway = DistGateway::new(Arc::new(core), Arc::new(Groups), Arc::new(TestClock), DistGatewayConfig::new(64)).unwrap();
    let bare = router(Arc::new(gateway), GatewayHttpConfig::new("https://gw.example"));
    let response = bare.oneshot(rpc("GetTask", json!({ "id": "x" }))).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

/// A Gateway whose every use case fails with one error.
struct Failing(GatewayError);
#[async_trait]
impl DistGatewayService for Failing {
    async fn submit(&self, _: &GatewayCaller, _: SubmitInput) -> Result<AdmissionReceipt, GatewayError> {
        Err(self.0.clone())
    }
    async fn get_task(&self, _: &GatewayCaller, _: &Tid) -> Result<TaskView, GatewayError> {
        Err(self.0.clone())
    }
    async fn subscribe(&self, _: &GatewayCaller, _: SubscribeTask) -> Result<TaskSubscription, GatewayError> {
        Err(self.0.clone())
    }
    async fn wait_task(&self, _: &GatewayCaller, _: WaitTask) -> Result<TaskView, GatewayError> {
        Err(self.0.clone())
    }
    async fn cancel(&self, _: &GatewayCaller, _: CancelTask) -> Result<AdmissionReceipt, GatewayError> {
        Err(self.0.clone())
    }
    async fn list_tasks(&self, _: &GatewayCaller, _: ListTasks) -> Result<TaskPage, GatewayError> {
        Err(self.0.clone())
    }
}

#[tokio::test]
async fn every_gateway_error_maps_to_its_code() {
    let unavailable = GatewayError::Unavailable { code: "store_not_applied".into() };
    let unknown = GatewayError::OutcomeUnknown { request_id: RequestId("msg-1".into()) };
    let cases = [
        (GatewayError::NotFound, -32001, false),
        (GatewayError::NotCancelable, -32002, false),
        (GatewayError::AccessDenied, -32600, false),
        (GatewayError::Conflict, -32602, false),
        (GatewayError::Invalid { code: "interaction_not_open".into() }, -32602, false),
        (GatewayError::Quarantined, -32603, false),
        (unavailable, -32603, true),
        (unknown, -32603, true),
    ];
    for (error, code, retryable) in cases {
        let app = app_over(Arc::new(Failing(error.clone())));
        for request in [rpc("GetTask", json!({ "id": "s0001.a.b" })), send("msg-1", "hi", None, None, true)] {
            let response = json_of(&app, request).await;
            assert_eq!(error_code(&response), code, "{error:?}");
            let details = response["error"]["data"].as_array().cloned().unwrap_or_default();
            assert_eq!(details.iter().any(|d| d["retryable"] == true), retryable, "{error:?}: {response}");
        }
    }
    let quarantined = json_of(&app_over(Arc::new(Failing(GatewayError::Quarantined))), rpc("GetTask", json!({ "id": "x" }))).await;
    assert_eq!(quarantined["error"]["message"], "session unavailable");
}

#[test]
fn a_cursor_round_trips_and_other_formats_are_rejected() {
    let at = TaskCursor {
        sid: Sid("s0042.7f3a9c".into()),
        tid: Tid("s0042.7f3a9c.b21e".into()),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: "12-1".into(),
        },
    };
    let id = cursor::encode(&at).unwrap();
    // The example of the decisions page: base64url of "s0042.7f3a9c.b21e:12-1".
    assert_eq!(id, "1.czAwNDIuN2YzYTljLmIyMWU6MTItMQ");
    assert_eq!(cursor::decode(&id), Some(at.clone()));

    for bad in ["", "12-1", "2.czAwNDIuN2YzYTljLmIyMWU6MTItMQ", "1.!!!", "1.czAwNDI"] {
        assert_eq!(cursor::decode(bad), None, "{bad}");
    }
    // Another backend or generation has no format-1 id.
    let mut other = at.clone();
    other.token.backend = BackendId("other".into());
    assert_eq!(cursor::encode(&other), None);
    let mut rebuilt = at;
    rebuilt.token.generation = LogGeneration("2".into());
    assert_eq!(cursor::encode(&rebuilt), None);
}

#[tokio::test]
async fn a_stream_on_a_task_that_already_waits_for_input_is_the_task_and_ends() {
    let p = Arc::new(ports());
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let tid = sent["result"]["task"]["id"].as_str().unwrap().to_owned();
    p.worker.append(&status_event(&tid, eid(7, 1), TaskStatus::InputRequired)).await;
    publish(&p, &tid, TaskStatus::InputRequired, Some(eid(7, 1)), &["i-3"]).await;

    // Subscribing, or retrying the send as a stream: one Task frame, then the end.
    let subscribe = rpc("SubscribeToTask", json!({ "id": tid }));
    let retry = rpc("SendStreamingMessage", json!({ "message": user_message("msg-1", "hello", None, None) }));
    for request in [subscribe, retry] {
        let frames = read_frames(app.clone().oneshot(request).await.unwrap(), 10).await;
        assert_eq!(frames.len(), 1, "{frames:?}");
        assert_eq!(frames[0].data["result"]["task"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED");
    }

    // The reply as a stream stays open past the question it answers.
    let worker = {
        let p = p.clone();
        let tid = tid.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(80)).await;
            p.worker.append(&status_event(&tid, eid(8, 1), TaskStatus::Working)).await;
            p.worker.append(&status_event(&tid, eid(9, 1), TaskStatus::Completed)).await;
        })
    };
    let reply = rpc("SendStreamingMessage", json!({ "message": user_message("msg-2", "B", None, Some(&tid)) }));
    let frames = read_frames(app.clone().oneshot(reply).await.unwrap(), 10).await;
    worker.await.unwrap();
    assert_eq!(frames.len(), 3, "{frames:?}");
    assert_eq!(frames[0].data["result"]["task"]["status"]["state"], "TASK_STATE_INPUT_REQUIRED");
    assert_eq!(frames[2].data["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_COMPLETED");
}

#[tokio::test]
async fn a_malformed_page_token_is_an_invalid_params_error() {
    let p = ports();
    let app = app(&p);
    let sent = json_of(&app, send("msg-1", "hello", None, None, true)).await;
    let sid = sent["result"]["task"]["contextId"].as_str().unwrap().to_owned();
    let listed = json_of(&app, rpc("ListTasks", json!({ "contextId": sid, "pageToken": "garbage" }))).await;
    assert_eq!(error_code(&listed), -32602);
}
