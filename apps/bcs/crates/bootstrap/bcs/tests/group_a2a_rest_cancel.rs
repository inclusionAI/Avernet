//! Mounted Group A2A cancellation over the real Worker and Inbox joints.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "bridge assertions")]
#[path = "group_a2a_companion_support/mod.rs"]
mod group_a2a_companion_support;

use axum::{Router, body::Body, extract::Extension, http::{Request, StatusCode}};
use bcs_dist_gateway_http::GROUPS_PREFIX;
use bcs_dist_model::{CancelObservation, Sid, Tid};
use bcs_service_api::port::repo::distributed::dispatch::DispatchControlPort as _;
use bcs_store_api::stream::{StreamEntry, StreamStorePlugin};
use group_a2a_companion_support::*;
use serde_json::{Value, json};
use std::{num::NonZeroU32, ops::Bound, sync::atomic::Ordering, time::Duration};
use tower::ServiceExt;

async fn request(app: &Router, request: Request<Body>) -> (StatusCode, Value) {
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 1 << 20).await.unwrap();
    (status, serde_json::from_slice(&bytes).expect("JSON response"))
}

fn post(uri: String, body: Value) -> Request<Body> {
    Request::post(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

fn cancel(tid: &str) -> Request<Body> {
    post(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{tid}:cancel"), json!({}))
}

fn get_task(tid: &str) -> Request<Body> {
    Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{tid}"))
        .body(Body::empty())
        .unwrap()
}

async fn start_round(world: &CompanionWorld, app: &Router) -> (Sid, String) {
    let (status, body) = request(
        app,
        post(
            format!("{GROUPS_PREFIX}/{GROUP}/message:send"),
            json!({
                "message": {
                    "messageId": "strict-cancel-target",
                    "role": "ROLE_USER",
                    "parts": [{"text": "research"}],
                },
                "configuration": {"blocking": false},
            }),
        ),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "send: {body}");
    let tid = body["id"].as_str().unwrap().to_owned();
    let sid = Sid(body["contextId"].as_str().unwrap().to_owned());
    tokio::time::timeout(Duration::from_secs(90), async {
        while world.journal.executions.load(Ordering::SeqCst) == 0 {
            world.drive(1).await;
        }
    })
    .await
    .expect("the strict round reached the downstream node");
    (sid, tid)
}

async fn inbox(world: &CompanionWorld, sid: &Sid) -> Vec<StreamEntry> {
    world.model.range(
        &world.assembly.roles.layout.inbox(sid).unwrap(),
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(16).unwrap(),
        false,
    )
    .await
    .unwrap()
    .entries
}

#[tokio::test]
async fn rest_cancel_admits_the_running_strict_task() {
    let world = CompanionWorld::assemble(enabled_config()).await;
    let _connector = world.start_connector();
    let app = world.assembly.router.clone().layer(Extension(caller()));
    let (sid, tid) = start_round(&world, &app).await;

    // The route can answer the current view while a downstream stop is
    // unconfirmed, but success must already have a durable inbox control.
    let (status, body) = request(&app, cancel(&tid)).await;
    assert_eq!(status, StatusCode::OK, "cancel of a running strict task: {body}");
    assert_eq!(body["id"], tid);
    assert!(inbox(&world, &sid).await.iter().any(|entry| {
        let input = bcs_dist_store::codec::decode_inbox_entry(&sid, &entry.fields).unwrap();
        input.tid.0 == tid && matches!(input.body, bcs_dist_model::InputBody::Cancel { .. })
    }), "cancel must be durably enqueued for the licensed task");

    let did = node_did(&Tid(tid.clone()), 1, NODE_RESEARCH);
    let remote = tokio::time::timeout(Duration::from_secs(90), async {
        loop {
            world.drive(1).await;
            let record = world.assembly.roles.dispatch.get(&did).await.unwrap().unwrap();
            if record.view.execution.cancellation == CancelObservation::OutcomeUnknown {
                assert_eq!(record.view.control.cancel.unwrap().reason.as_deref(), Some("CanceledByUser"));
                break record.view.execution.remote.expect("original node's remote task");
            }
        }
    })
    .await
    .expect("the original dispatch received a downstream cancel, with an unknown stop");
    let (status, body) = request(&app, get_task(&tid)).await;
    assert_eq!(status, StatusCode::OK, "get while stop is unconfirmed: {body}");
    assert_eq!(body["status"]["state"], "TASK_STATE_WORKING");

    world.journal.confirm_cancel();
    tokio::time::timeout(Duration::from_secs(90), async {
        loop {
            world.drive(1).await;
            let (status, body) = request(&app, get_task(&tid)).await;
            assert_eq!(status, StatusCode::OK, "get after remote stop: {body}");
            if body["status"]["state"] == "TASK_STATE_CANCELED" {
                break;
            }
        }
    })
    .await
    .expect("a definitive remote stop lets the strict round close as Canceled");
    let record = world.assembly.roles.dispatch.get(&did).await.unwrap().unwrap();
    assert_eq!(record.view.execution.remote, Some(remote));
    assert_eq!(record.view.execution.cancellation, CancelObservation::ConfirmedCanceled);
    assert_eq!(world.journal.executions.load(Ordering::SeqCst), 1, "no successor node started");
}

#[tokio::test]
async fn wrong_source_and_task_do_not_cancel_the_running_strict_task() {
    let world = CompanionWorld::assemble(enabled_config()).await;
    let _connector = world.start_connector();
    let app = world.assembly.router.clone().layer(Extension(caller()));
    let (sid, tid) = start_round(&world, &app).await;
    let before = inbox(&world, &sid).await;

    let (status, body) = request(
        &app,
        post(
            format!("/a2a/{GROUP}"),
            json!({"jsonrpc": "2.0", "id": "legacy-cancel", "method": "CancelTask", "params": {"id": tid}}),
        ),
    ).await;
    assert_eq!(status, StatusCode::OK, "legacy RPC envelope: {body}");
    assert_eq!(body["error"]["code"], -32001, "cross-source task is invisible");

    let (status, body) = request(&app, cancel(&format!("{}.wrong-task", sid.0))).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "another task in this context: {body}");
    assert_eq!(inbox(&world, &sid).await, before, "rejected controls must not write the inbox");
}
