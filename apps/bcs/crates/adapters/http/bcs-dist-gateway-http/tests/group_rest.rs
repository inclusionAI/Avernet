//! REST/A2A boundary of published FSM groups (plan T8) over the same stack the
//! JSON-RPC binding runs on: HTTP+JSON and SSE under
//! `/api/v1/collaboration/a2a/groups/{group_id}`, without touching the legacy
//! binding. The fake authentication chain installs the trusted `GatewayCaller`
//! the way the real front layer does; the grants policy is the host-registered
//! D0 fixture. The gate-level vectors live in `group_rest_gates.rs`.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

#[path = "rest_common/mod.rs"]
mod rest_common;

use a2a::{Message, Part, Role};
use bcs_store_api::stream::StreamStorePlugin;
use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_service_api::application::dist_gateway::*;
use rest_common::*;
use serde_json::{Value, json};
use std::num::NonZeroU32;
use std::sync::Arc;
use tower::ServiceExt;

const GROUP: &str = "grp-research-01";
const REST: &str = "/api/v1/collaboration/a2a/groups";

// ---- the T8 vectors ----

/// The REST contract, on the same stack the JSON-RPC binding runs on: the
/// legacy entry keeps answering while the group REST entry enforces its own
/// codes. The loser of one message key duel answers 409.
#[tokio::test]
async fn rest_contract_preserves_rpc() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);

    // The legacy JSON-RPC binding keeps answering: same stack, untouched.
    let (status, body) = response_of(&app, rpc_send("m-rpc-1", None)).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    assert_eq!(
        body["result"]["task"]["status"]["state"],
        "TASK_STATE_SUBMITTED",
        "{body:?}"
    );

    // REST send: the same message key twice is the same task; changed content
    // under the same key is a 409 conflict (the concurrent-loser answer).
    let (status, body) =
        response_of(&app, rest_send("m-rest-1", "hello", None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let tid = body.unwrap()["id"].as_str().unwrap().to_owned();
    let (again, body) =
        response_of(&app, rest_send("m-rest-1", "hello", None, json!({ "blocking": false }))).await;
    assert_eq!(again, StatusCode::OK, "{body:?}");
    assert_eq!(body.unwrap()["id"], tid.as_str());
    let (collision, _) = response_of(
        &app,
        rest_send("m-rest-1", "different content", None, json!({ "blocking": false })),
    )
    .await;
    assert_eq!(collision, StatusCode::CONFLICT);

    // Get carries the task once terminal.
    publish_at(&p, &tid, TaskStatus::Completed, 1, &[]).await;
    let (status, body) = response_of(&app, rest_task("GET", &tid)).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    assert_eq!(body.unwrap()["status"]["state"], "TASK_STATE_COMPLETED");

    // Malicious self-reported metadata never becomes identity and never
    // rides the normalized request: the claim stays out of the durable
    // admission and out of every answer, and another principal's caller
    // cannot read the task whatever the body claimed.
    let mut sneaky = Message::new(Role::User, vec![Part::text("claimed goods")]);
    sneaky.message_id = "m-sneaky".into();
    sneaky.metadata = Some(
        [
            ("x-claimed-principal".to_owned(), json!("bot:attacker")),
            ("x-claimed-tenant".to_owned(), json!("tenant-other")),
        ]
        .into_iter()
        .collect(),
    );
    let sneaky_request = Request::post(format!("{REST}/{GROUP}/message:send"))
        .header("content-type", "application/json")
        .body(Body::from(
            json!({
                "message": serde_json::to_value(&sneaky).unwrap(),
                "configuration": { "blocking": false }
            })
            .to_string(),
        ))
        .unwrap();
    let (status, body) = response_of(&app, sneaky_request).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let sneaky_view = body.unwrap();
    let sneaky_tid = sneaky_view["id"].as_str().unwrap().to_owned();
    let sneaky_sid = sneaky_view["contextId"].as_str().unwrap().to_owned();
    // Durable strip: the admitted input of the sneaky session carries no
    // byte of the self-reported identity claims.
    let claims_stay = "x-claimed-principal".as_bytes();
    let shard = sneaky_sid.split_once('.').unwrap().0;
    let key = format!("bcn:v1:c01:main:{shard}:inbox:{sneaky_sid}");
    for entry in StreamStorePlugin::range(
        p.model.as_ref(),
        &key,
        std::ops::Bound::Unbounded,
        std::ops::Bound::Unbounded,
        NonZeroU32::new(1000).unwrap(),
        false,
    )
    .await
    .unwrap()
    .entries
    {
        for field in entry.fields.values() {
            assert!(
                !field.windows(claims_stay.len()).any(|window| window == claims_stay),
                "self-reported claims must not survive normalization"
            );
        }
    }
    // Wire strip: no answer of the honest caller mentions the claims either.
    let (status, got) = response_of(&app, rest_task("GET", &sneaky_tid)).await;
    assert_eq!(status, StatusCode::OK);
    let got = got.unwrap();
    let text = serde_json::to_string(&got).unwrap();
    assert!(!text.contains("x-claimed-principal"), "{got:?}");
    assert!(!text.contains("bot:attacker"), "{got:?}");
    // And the claimed identities never let the attacker read the task.
    let other = test_router_for(&stack, rest_caller("pa-attacker"));
    let (status, body) = response_of(&other, rest_task("GET", &sneaky_tid)).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "{body:?}");

    // Without a resolved caller the REST entry refuses.
    let bare = router(
        stack.gateway.clone(),
        stack.discovery.clone(),
        GatewayHttpConfig::new("https://gw.example"),
    );
    let (status, _) = response_of(
        &bare,
        rest_send("m-rest-2", "hi", None, json!({ "blocking": false })),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

/// The blocking wait budget: a send that waits past its own budget answers
/// 504 with the current task, and the task keeps running (no cancel).
#[tokio::test]
async fn blocking_send_budget_expiry_returns_504_and_keeps_the_task() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);
    // The default blocking budget (200 ms here) also binds the answer.
    let (status, body) = response_of(
        &app,
        rest_send("m-wait-1", "hello", None, json!({ "blocking": true, "waitTimeoutMillis": 120 })),
    )
    .await;
    assert_eq!(status, StatusCode::GATEWAY_TIMEOUT, "{body:?}");
    let body = body.unwrap();
    assert_eq!(body["status"]["state"], "TASK_STATE_SUBMITTED");
    let tid = body["id"].as_str().unwrap().to_owned();

    // The task continues: still readable, not canceled.
    let (status, body) = response_of(&app, rest_task("GET", &tid)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body.unwrap()["status"]["state"], "TASK_STATE_SUBMITTED");
}

/// Source isolation: one message key used without context from both entrances
/// creates two sessions; neither entrance operates on the other's resources;
/// a cross-entry attempt writes nothing new into any inbox.
#[tokio::test]
async fn rpc_cannot_write_rest_context_and_vice_versa() {
    let p = Arc::new(ports());
    let stack = group_stack(&p);
    let app = test_router(&stack);

    let (status, body) =
        response_of(&app, rest_send("m-iso-1", "rest", None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    let rest_sid = body["contextId"].as_str().unwrap().to_owned();
    let rest_tid = body["id"].as_str().unwrap().to_owned();

    let (status, body) = response_of(&app, rpc_send("m-iso-1", None)).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    let rpc_tid = body["result"]["task"]["id"].as_str().unwrap().to_owned();
    let rpc_sid = body["result"]["task"]["contextId"].as_str().unwrap().to_owned();
    assert_ne!(rest_sid, rpc_sid, "each entrance derives its own session");

    let writes_before = inbox_len(&p, &rpc_sid).await + inbox_len(&p, &rest_sid).await;

    // Cross-entry append, cancel, query and subscribe: all rejected.
    let (status, _) = response_of(
        &app,
        rest_send("m-rest-x", "into rpc session", Some(&rpc_sid), json!({ "blocking": false })),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _) = response_of(&app, rest_cancel(&rpc_tid, "cancel-x")).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _) = response_of(&app, rest_task("GET", &rpc_tid)).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _) = response_of(&app, rest_task("GET", &format!("{rpc_tid}:subscribe"))).await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    // The legacy entry rejects the REST session's resources too.
    let (status, body) = response_of(&app, rpc_send("m-rpc-x", Some(&rest_sid))).await;
    let body = body.expect("the legacy entry answers with JSON");
    let _ = status;
    assert_eq!(body["error"]["code"], -32001, "a rejected resource stays invisible: {body:?}");
    let (status, body) = response_of(&app, rpc_request("CancelTask", json!({ "id": rest_tid }), "rx")).await;
    let body = body.expect("cancel answers with JSON");
    let _ = status;
    assert_eq!(body["error"]["code"], -32001, "a rejected resource stays invisible: {body:?}");

    // REST entries never disclose the legacy session's resources to the list.
    let (status, body) = response_of(&app, rest_list("pageSize=10")).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let rows = body.unwrap()["tasks"].as_array().unwrap().clone();
    assert!(
        rows.iter().all(|row| row["id"].as_str().unwrap() != rpc_tid),
        "the legacy resource stays out of the public list: {rows:?}"
    );

    // No inbox write happened for either session.
    let writes_after = inbox_len(&p, &rpc_sid).await + inbox_len(&p, &rest_sid).await;
    assert_eq!(writes_after, writes_before, "a cross-entry attempt writes nothing");
}

/// The public list cuts one consistent version and shares it across pages:
/// filter, total and rows all read the same materialized cut; concurrent
/// additions and hot-to-archive transitions between pages never duplicate or
/// drop rows and never rewrite the total; cursors are bound, signed,
/// revalidated and expire.
#[tokio::test]
async fn list_pages_share_cut_across_hot_to_archive() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);

    // Distinct REST contexts of one group: the seed stays Submitted and the
    // other accepted tasks carry distinct status channels. The listing cut
    // is group-scoped; these tasks need not compete for a single context.
    let (status, body) =
        response_of(&app, rest_send("m-seed", "seed", None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    let sid = body["contextId"].as_str().unwrap().to_owned();
    let seed = body["id"].as_str().unwrap().to_owned();
    let mut tid_of = vec![(0, seed)];
    for (n, delta) in [("m-l-1", 1_000), ("m-l-2", 3_000), ("m-l-3", 2_000)] {
        let (status, body) =
            response_of(&app, rest_send(n, n, None, json!({ "blocking": false }))).await;
        assert_eq!(status, StatusCode::OK, "{body:?}");
        tid_of.push((delta, body.unwrap()["id"].as_str().unwrap().to_owned()));
    }
    let tid_of: std::collections::HashMap<i64, String> = tid_of.into_iter().collect();
    publish_at(&p, &tid_of[&1_000], TaskStatus::Working, 1_000, &[]).await;
    publish_at(&p, &tid_of[&3_000], TaskStatus::InputRequired, 3_000, &["i-1"]).await;
    publish_at(&p, &tid_of[&2_000], TaskStatus::Completed, 2_000, &[]).await;

    // First page: the filtered actual total, the effective page size, and the
    // fixed order (status_updated_at DESC, tid ASC).
    let (status, body) = response_of(&app, rest_list("pageSize=2")).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    assert_eq!(body["totalSize"], 4, "{body:?}");
    let rows = body["tasks"].as_array().unwrap();
    assert_eq!(rows.len(), 2, "{body:?}");
    assert_eq!(rows[0]["id"], tid_of[&3_000].as_str(), "newest status channel first: {body:?}");
    let next = body["nextPageCursor"].as_str().unwrap().to_owned();
    assert!(!next.is_empty());

    // A prepared-but-never-accepted record is invisible: the task document
    // exists, but the accepted index never carried it.
    publish_at(&p, &format!("{sid}.orphan"), TaskStatus::Working, 99_000, &[]).await;

    // Between pages: one row ages out to its archive record and a new round
    // arrives. The cut of the first query stays consistent.
    p.results.put(final_record_of(&tid_of[&1_000]));
    p.worker.expire(&tid_of[&1_000]).await;
    let (status, body) =
        response_of(&app, rest_send("m-l-new", "late", None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let late = body.unwrap()["id"].as_str().unwrap().to_owned();

    let (status, body) = response_of(&app, rest_list(&format!("pageSize=2&pageCursor={next}"))).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    assert_eq!(body["totalSize"], 4, "the total is part of the shared cut: {body:?}");
    let rows: Vec<String> = body["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["id"].as_str().unwrap().to_owned())
        .collect();
    assert_eq!(rows, [tid_of[&1_000].clone(), tid_of[&0].clone()], "the original cut retains its order and excludes the new row");
    assert!(body["nextPageCursor"].is_null() || body["nextPageCursor"] == Value::Null, "the cut is exhausted: {body:?}");

    // A fresh cut sees the new round, the archived row and no orphan.
    let (status, body) = response_of(&app, rest_list("pageSize=10")).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let body = body.unwrap();
    assert_eq!(body["totalSize"], 5, "accepted rows only: {body:?}");
    let rows: Vec<String> = body["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["id"].as_str().unwrap().to_owned())
        .collect();
    assert!(rows.contains(&tid_of[&1_000]), "archived rows stay listed: {rows:?}");
    assert!(rows.contains(&late), "new tasks appear in a fresh cut: {rows:?}");
    assert!(!rows.iter().any(|tid| tid.ends_with(".orphan")), "unaccepted stays invisible");

    // A foreign subject, a tampered cursor, a revoked grant, an expired
    // cursor and an over-budget page size are all refused: no live fallback.
    let fresh = response_of(&app, rest_list("pageSize=2")).await.1.unwrap();
    let next = fresh["nextPageCursor"].as_str().unwrap().to_owned();

    let other = test_router_for(&stack, rest_caller("pa-other"));
    let (status, body) = response_of(&other, rest_list(&format!("pageSize=2&pageCursor={next}"))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body:?}");
    assert_eq!(code_of(&body), "CURSOR_SUBJECT_MISMATCH");

    let (status, body) = response_of(&app, rest_list(&format!("pageSize=2&pageCursor={next}x"))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body:?}");
    assert_eq!(code_of(&body), "CURSOR_FORMAT_INVALID");

    // The cut and the cursor expire together with their deadline.
    stack.clock.0.fetch_add(300_001, Ordering::SeqCst);
    let fresh = response_of(&app, rest_list("pageSize=2")).await.1.unwrap();
    let renewed = fresh["nextPageCursor"].as_str().unwrap();
    let (status, body) = response_of(&app, rest_list(&format!("pageSize=2&pageCursor={renewed}"))).await;
    assert_eq!(status, StatusCode::OK, "fresh pagination after expiry: {body:?}");
    let (status, body) = response_of(&app, rest_list(&format!("pageSize=2&pageCursor={next}"))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body:?}");
    assert_eq!(code_of(&body), "CURSOR_EXPIRED");
    // A revoked grant stops the next page even with a still-valid cursor.
    stack.grants.read.store(false, Ordering::SeqCst);
    let (status, body) = response_of(&app, rest_list(&format!("pageSize=2&pageCursor={renewed}"))).await;
    assert_eq!(status, StatusCode::FORBIDDEN, "{body:?}");
    assert_eq!(code_of(&body), "PERMISSION_DENIED");
    stack.grants.read.store(true, Ordering::SeqCst);

    // The page-size profile bound is a maximum that refuses above it.
    let (status, body) = response_of(&app, rest_list("pageSize=1000")).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body:?}");
    assert_eq!(code_of(&body), "INVALID_ARGUMENT");
}

/// Subscriptions recover to the authorized snapshot, end boundedly on a
/// terminal task, and a dropped connection never cancels; a cancel answered
/// after its lost response recovers idempotently.
#[tokio::test]
async fn rest_subscribe_recovers_boundedly_and_never_cancels() {
    let p = Arc::new(ports());
    let stack = group_stack(&p);
    let app = test_router(&stack);
    let (_, body) =
        response_of(&app, rest_send("m-sub-1", "hello", None, json!({ "blocking": false }))).await;
    let tid = body.unwrap()["id"].as_str().unwrap().to_owned();
    let sid = tid.rsplit_once('.').unwrap().0.to_owned();

    // The stream lives while the task works; dropping it changes nothing.
    {
        let response = app
            .clone()
            .oneshot(rest_task("GET", &format!("{tid}:subscribe")))
            .await
            .unwrap();
        let frames = read_frames(response, 1).await;
        assert_eq!(frames.len(), 1, "{frames:?}");
        assert_eq!(frames[0].data["id"], tid.as_str());
        // Every event frame carries the cursor id a client resumes with.
        assert!(frames[0].id.is_some(), "{frames:?}");
    }
    let (_, body) = response_of(&app, rest_task("GET", &tid)).await;
    assert_eq!(
        body.unwrap()["status"]["state"],
        "TASK_STATE_SUBMITTED",
        "dropping a stream never cancels"
    );

    // Cancel: the first answer times out with the current task; after the
    // round reaches Canceled, repeating the same request id recovers the
    // final state idempotently (the lost-response case).
    let (status, body) = response_of(&app, rest_cancel(&tid, "constant-cancel")).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    assert_eq!(body.unwrap()["status"]["state"], "TASK_STATE_SUBMITTED");
    publish_at(&p, &tid, TaskStatus::Canceled, 1, &[]).await;
    let (status, body) = response_of(&app, rest_cancel(&tid, "constant-cancel")).await;
    assert_eq!(status, StatusCode::OK, "a repeated cancel recovers idempotently: {body:?}");
    assert_eq!(body.unwrap()["status"]["state"], "TASK_STATE_CANCELED");

    // A terminal task answers a subscriber with one snapshot frame and
    // closes; a later round of the same session is a fresh task.
    let (_, body) =
        response_of(&app, rest_send("m-sub-2", "done", Some(&sid), json!({ "blocking": false }))).await;
    let done = body.unwrap()["id"].as_str().unwrap().to_owned();
    publish_at(&p, &done, TaskStatus::Completed, 2, &[]).await;
    let response = app
        .clone()
        .oneshot(rest_task("GET", &format!("{done}:subscribe")))
        .await
        .unwrap();
    let frames = read_frames(response, 1).await;
    assert_eq!(frames.len(), 1, "{frames:?}");
    assert_eq!(frames[0].data["status"]["state"], "TASK_STATE_COMPLETED");

    // A finished task refuses a new cancel with 409.
    let (status, body) = response_of(&app, rest_cancel(&done, "late-cancel")).await;
    assert_eq!(status, StatusCode::CONFLICT, "{body:?}");
    assert_eq!(code_of(&body), "TASK_NOT_CANCELABLE");
}

/// The card declares exactly what this boundary implements: no push, no
/// unaudited extension URIs, one streaming group surface.
#[tokio::test]
async fn rest_card_declares_only_implemented_capabilities() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);
    let request = Request::get(format!("{GROUPS_PREFIX}/{GROUP}/agent-card.json"))
        .body(Body::empty())
        .unwrap();
    let (status, body) = response_of(&app, request).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    let card = body.unwrap();
    assert_eq!(card["capabilities"]["streaming"], true);
    assert_eq!(card["capabilities"]["pushNotifications"], false);
    assert_eq!(card["capabilities"]["extensions"], json!([]));
    assert_eq!(
        card["supportedInterfaces"][0]["url"],
        format!("https://gw.example{GROUPS_PREFIX}/{GROUP}")
    );
}

/// A page or a frame beyond the public budget is refused, never silently
/// truncated.
#[tokio::test]
async fn oversized_page_and_frame_are_refused() {
    let p = ports();
    let stack = group_stack(&p);
    let app = test_router(&stack);

    // Within the frame budget: accepted.
    let (status, body) =
        response_of(&app, rest_send("m-ok", "hello", None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::OK, "{body:?}");

    // One byte beyond the public frame budget: refused with 413.
    let huge = "x".repeat(8 * 1024 * 1024 + 1);
    let (status, _) =
        response_of(&app, rest_send("m-huge", &huge, None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
}

/// Quarantined context: a new REST request answers 400 CONTEXT_UNAVAILABLE,
/// never an anonymous fallback to live state.
#[tokio::test]
async fn quarantined_context_is_unavailable_not_anonymous() {
    struct Failing(GatewayError);
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
        ) -> Result<bcs_service_api::port::repo::distributed::group_listing::GroupIndexPage, GatewayError> {
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
    let app = router(
        Arc::new(Failing(GatewayError::Quarantined)),
        Arc::new(NoDiscovery),
        config,
    )
    .layer(Extension(caller()));
    let (status, body) =
        response_of(&app, rest_send("m-quarantine", "hello", None, json!({ "blocking": false }))).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{body:?}");
    assert_eq!(code_of(&body), "CONTEXT_UNAVAILABLE");
}
