//! Gate vectors of the REST/A2A group surface (plan T8, review round 1):
//! the D0 grant matrix on the task routes, terminal SSE closure on
//! revocation and frame-budget overflow, and the quarantined-context
//! mapping on every group leg.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

#[path = "rest_common/mod.rs"]
mod rest_common;

use async_trait::async_trait;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::port::repo::distributed::group_listing::GroupIndexPage;
use rest_common::*;
use futures::StreamExt;
use serde_json::json;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tower::ServiceExt;

/// The D0 matrix on the task routes: a subject whose fresh grants lack
/// `cancel` cannot cancel through the group route, and a read-revoked
/// subject cannot read one task any more than it can list them.
#[tokio::test]
async fn d0_matrix_gates_rest_task_read_and_cancel() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);
    let (_, body) =
        response_of(&app, rest_send("m-d0", "hello", None, json!({ "blocking": false }))).await;
    let tid = body.unwrap()["task"]["id"].as_str().unwrap().to_owned();

    // Cancel grant revoked: the cancel route refuses and nothing durable
    // happens for the round.
    stack.grants.cancel.store(false, Ordering::SeqCst);
    let (status, body) = response_of(&app, rest_cancel(&tid, "d0-cancel")).await;
    assert_eq!(status.as_u16(), 403, "{body:?}");
    assert_eq!(code_of(&body), "PERMISSION_DENIED");
    // The task is untouched and still cancelable once the grant returns.
    stack.grants.cancel.store(true, Ordering::SeqCst);
    let (status, body) = response_of(&app, rest_cancel(&tid, "d0-cancel")).await;
    assert_eq!(status.as_u16(), 200, "{body:?}");

    // Read grant revoked: one task answers exactly like the listing does.
    stack.grants.read.store(false, Ordering::SeqCst);
    let (status, body) = response_of(&app, rest_task("GET", &tid)).await;
    assert_eq!(status.as_u16(), 403, "{body:?}");
    assert_eq!(code_of(&body), "PERMISSION_DENIED");
    let (status, body) = response_of(&app, rest_list("pageSize=2")).await;
    assert_eq!(status.as_u16(), 403, "{body:?}");
    assert_eq!(code_of(&body), "PERMISSION_DENIED");
    let sid = tid.rsplit_once('.').unwrap().0;
    let (status, body) = response_of(&app, rpc_request("ListTasks", json!({"contextId": sid}), "list-revoked")).await;
    assert_eq!(status.as_u16(), 200);
    let rpc = body.expect("RPC error envelope");
    assert_eq!(rpc["error"]["code"], -32001);
    assert!(rpc.get("result").is_none());
    stack.grants.read.store(true, Ordering::SeqCst);
    let (status, body) = response_of(&app, rest_task("GET", &tid)).await;
    assert_eq!(status.as_u16(), 200, "{body:?}");
}

/// Terminal closure on revocation (review round 1): an open group stream
/// whose grants get revoked mid-flight emits exactly one terminal error
/// frame and closes within one recheck interval; the server-side
/// subscription drops with it.
#[tokio::test]
async fn revoked_grant_ends_an_open_stream_within_one_recheck() {
    let p = Arc::new(ports());
    let stack = group_stack(&p);
    let app = test_router(&stack);
    let (_, body) =
        response_of(&app, rest_send("m-live", "hello", None, json!({ "blocking": false }))).await;
    let tid = body.unwrap()["task"]["id"].as_str().unwrap().to_owned();

    let response = app
        .clone()
        .oneshot(rest_task("GET", &format!("{tid}:subscribe")))
        .await
        .unwrap();
    assert!(response
        .headers()["content-type"]
        .to_str()
        .unwrap()
        .starts_with("text/event-stream"));
    let mut body_stream = response.into_body().into_data_stream();
    let mut text = String::new();
    // The authorized snapshot arrives first.
    while parse_frames(&text).is_empty() {
        let chunk = tokio::time::timeout(Duration::from_secs(5), body_stream.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        text.push_str(std::str::from_utf8(&chunk).unwrap());
    }
    // Revoke mid-stream; the stream must end with one denial frame within
    // one recheck interval (50 ms here) plus scheduling slack.
    stack.grants.read.store(false, Ordering::SeqCst);
    let started = std::time::Instant::now();
    loop {
        match tokio::time::timeout(Duration::from_secs(5), body_stream.next()).await {
            Ok(Some(Ok(chunk))) => text.push_str(std::str::from_utf8(&chunk).unwrap()),
            Ok(_) => break,
            Err(_) => panic!("the stream stayed open past one recheck interval: {}", parse_frames(&text).len()),
        }
    }
    let closed_within = started.elapsed() < Duration::from_millis(2_000);
    let frames = parse_frames(&text);
    assert!(closed_within, "closed within the bounded window, frames: {frames:?}");
    assert_eq!(frames.len(), 2, "exactly the snapshot and one terminal denial: {frames:?}");
    assert_eq!(frames[1].data["error"]["code"], "PERMISSION_DENIED");

    // Server-side: the guarded subscription dropped, so the shared session
    // reader has nothing left to serve for this session.
    let reader = p.tasks.clone();
    let deadline = std::time::Instant::now() + Duration::from_millis(2_000);
    while reader.served_sessions() > 0 && std::time::Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert_eq!(p.tasks.served_sessions(), 0, "the guarded subscription is dropped, not parked");
}

/// Terminal closure on frame-budget overflow (review round 1): one frame
/// whose serialized envelope exceeds the public frame budget — built from
/// otherwise legal parts — ends the stream with one FRAME_BUDGET_EXCEEDED
/// frame and closes; the client is never served a truncated frame.
#[tokio::test]
async fn an_oversized_stream_frame_closes_with_the_budget_code() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);
    let (_, body) =
        response_of(&app, rest_send("m-big", "hello", None, json!({ "blocking": false }))).await;
    let tid = body.unwrap()["task"]["id"].as_str().unwrap().to_owned();

    // A legal snapshot unit whose content alone passes the input budget's
    // spirit but whose public frame exceeds it.
    let giant = "B".repeat(8 * 1024 * 1024 + 1024);
    let mut task = snapshot(&tid, TaskStatus::Working, None, NOW);
    task.sid = Sid(tid.rsplit_once('.').unwrap().0.to_owned());
    task.scope = TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    };
    task.content.snapshot = Payload::Inline(json!({
        "format_version": 1,
        "units": [{ "node": "n1", "artifact_id": "a-1", "class": "output", "content": giant }]
    }));
    p.worker.publish(&task).await;

    let response = app
        .clone()
        .oneshot(rest_task("GET", &format!("{tid}:subscribe")))
        .await
        .unwrap();
    let mut body_stream = response.into_body().into_data_stream();
    let mut text = String::new();
    loop {
        match tokio::time::timeout(Duration::from_secs(5), body_stream.next()).await {
            Ok(Some(Ok(chunk))) => text.push_str(std::str::from_utf8(&chunk).unwrap()),
            Ok(_) => break,
            Err(_) => panic!("the stream stayed open: {} frames", parse_frames(&text).len()),
        }
    }
    let frames = parse_frames(&text);
    assert_eq!(frames.len(), 1, "one terminal frame only: {frames:?}");
    assert!(text.len() < 2 * 1024 * 1024, "no truncated frame was served: {} bytes", text.len());
    assert_eq!(frames[0].data["error"]["code"], "FRAME_BUDGET_EXCEEDED");
}

/// The quarantined-context mapping on every group leg (review round 1):
/// Get, cancel and a late refusal inside an opened stream all answer
/// CONTEXT_UNAVAILABLE — 400 for the one-shot legs, one terminal SSE error
/// frame for the stream leg. The Get/retry WORKING-face stop-unconfirmed
/// semantics ride the T16 dispatch-reader mount (T9 assembly).
#[tokio::test]
async fn quarantined_context_maps_on_every_group_leg() {
    struct Failing(GatewayError, bool);
    #[async_trait]
    impl DistGatewayService for Failing {
        async fn submit(&self, _: &GatewayCaller, _: SubmitInput) -> Result<AdmissionReceipt, GatewayError> {
            Err(self.0.clone())
        }
        async fn get_task(
            &self,
            _: &GatewayCaller,
            _: &GroupId,
            _: &Tid,
            _: CreationSource,
        ) -> Result<TaskView, GatewayError> {
            Err(self.0.clone())
        }
        async fn subscribe(&self, _: &GatewayCaller, _: SubscribeTask) -> Result<TaskSubscription, GatewayError> {
            if self.1 {
                // The stream opens; the refusal arrives as its first (and
                // only) item, the way a late revocation does.
                let error = self.0.clone();
                return Ok(futures::stream::once(async move { Err(error) }).boxed());
            }
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
        async fn group_send(&self, _: &GatewayCaller, _: SubmitInput) -> Result<AdmissionReceipt, GatewayError> {
            Err(self.0.clone())
        }
        async fn group_list_tasks(
            &self,
            _: &GatewayCaller,
            _: GroupTaskList,
        ) -> Result<GroupIndexPage, GatewayError> {
            Err(self.0.clone())
        }
        async fn group_wait_task(
            &self,
            _: &GatewayCaller,
            _: WaitTask,
        ) -> Result<(TaskView, bool), GatewayError> {
            Err(self.0.clone())
        }
    }
    struct NoDiscovery;
    #[async_trait]
    impl GroupDiscoveryService for NoDiscovery {
        async fn card(&self, _: &GatewayCaller, _: &GroupId) -> Result<GroupCard, GatewayError> {
            Err(GatewayError::NotFound)
        }
    }
    let mut config = GatewayHttpConfig::new("https://gw.example");
    config.blocking_send_wait = Duration::from_millis(200);
    let error = GatewayError::Quarantined;

    // One-shot legs: 400 CONTEXT_UNAVAILABLE, never a live fallback.
    let app = router(
        Arc::new(Failing(error.clone(), false)),
        Arc::new(NoDiscovery),
        config.clone(),
    )
    .layer(Extension(caller()));
    let (status, body) = response_of(&app, rest_task("GET", "s0042.7f3a9c.b21e")).await;
    assert_eq!(status.as_u16(), 400, "{body:?}");
    assert_eq!(code_of(&body), "CONTEXT_UNAVAILABLE");
    let (status, body) = response_of(&app, rest_cancel("s0042.7f3a9c.b21e", "q")).await;
    assert_eq!(status.as_u16(), 400, "{body:?}");
    assert_eq!(code_of(&body), "CONTEXT_UNAVAILABLE");

    // Stream leg: the stream opens and ends with exactly one terminal
    // CONTEXT_UNAVAILABLE frame.
    let stream_app = router(Arc::new(Failing(error.clone(), true)), Arc::new(NoDiscovery), config)
        .layer(Extension(caller()));
    let response = stream_app
        .oneshot(rest_task("GET", "s0042.7f3a9c.b21e:subscribe"))
        .await
        .unwrap();
    assert!(response
        .headers()["content-type"]
        .to_str()
        .unwrap()
        .starts_with("text/event-stream"));
    let mut body_stream = response.into_body().into_data_stream();
    let mut text = String::new();
    loop {
        match tokio::time::timeout(Duration::from_secs(5), body_stream.next()).await {
            Ok(Some(Ok(chunk))) => text.push_str(std::str::from_utf8(&chunk).unwrap()),
            Ok(_) => break,
            Err(_) => panic!("the stream stayed open: {text}"),
        }
    }
    let frames = parse_frames(&text);
    assert_eq!(frames.len(), 1, "{frames:?}");
    assert_eq!(frames[0].data["error"]["code"], "CONTEXT_UNAVAILABLE");
}
