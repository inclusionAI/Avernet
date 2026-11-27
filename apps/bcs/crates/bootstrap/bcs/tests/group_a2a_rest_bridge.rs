//! Task 19 C1/I1 bridge vectors: the mounted REST `message:send` of a
//! published FSM group must bridge into the strict round admission (the
//! Worker-side canonical seam) and complete a real strict round — and the
//! public Get/List of the same mounted surface must keep closed rounds
//! resolvable through the strict authority cut once a successor generation
//! took the session.
//!
//! Everything here is the SAME-PROCESS real assembly of the companion
//! target: real store joints, real worker/processor, real connector lane,
//! mounted REST/SSE router. No fake admission seam, no stubbed store.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "bridge assertions")]
#[path = "group_a2a_companion_support/mod.rs"]
mod group_a2a_companion_support;
#[path = "group_a2a_rest_reuse/mod.rs"]
mod group_a2a_rest_reuse;

use bcs_dist_model::Admission;
use bcs_store_api::{WriteOutcome, document::DocumentStorePlugin};
use group_a2a_companion_support::*;
use std::sync::atomic::Ordering;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use bcs_dist_gateway_http::GROUPS_PREFIX;
use serde_json::{Value, json};
use tower::ServiceExt;

/// Bounds every vector's wall time so a wedged joint fails loudly instead
/// of hanging the battery.
async fn bounded<const SECS: u64>(future: impl Future<Output = ()>) {
    tokio::time::timeout(Duration::from_secs(SECS), future)
        .await
        .expect("vector exceeded its wall-time bound");
}

async fn response_of(
    app: &axum::Router,
    request: Request<Body>,
) -> (StatusCode, Option<Value>) {
    let response = app.clone().oneshot(request).await.expect("router answers");
    let status = response.status();
    let bytes = axum::body::to_bytes(response.into_body(), 16 << 20)
        .await
        .expect("body");
    if bytes.is_empty() {
        (status, None)
    } else {
        (status, serde_json::from_slice(&bytes).ok())
    }
}

/// POST `message:send` of one plain-text user message (the T8 wire shape:
/// a `Message` with camelCase ids, `ROLE_USER` and one text part — the exact
/// `a2a` binding the gateway adapter serializes).
fn rest_send(message_id: &str, text: &str, blocking: bool) -> Request<Body> {
    let body = json!({
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [ { "text": text } ],
        },
        "configuration": { "blocking": blocking }
    });
    Request::post(format!("{GROUPS_PREFIX}/{GROUP}/message:send"))
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn until_start(world: &CompanionWorld) {
    let seen = world.journal.executions.load(Ordering::SeqCst);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(90);
    loop {
        world.drive(1).await;
        if world.journal.executions.load(Ordering::SeqCst) > seen {
            return;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "the dispatch never reached the remote (starts={})",
            world.journal.starts_seen.load(Ordering::SeqCst)
        );
    }
}

async fn until_rest_task_state(
    world: &CompanionWorld,
    app: &axum::Router,
    tid: &bcs_dist_model::Tid,
    state: &str,
) -> Value {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(120);
    loop {
        world.drive(2).await;
        let (status, body) = response_of(
            app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", tid.0))
                .body(Body::empty())
                .unwrap(),
        )
        .await;
        assert_eq!(
            status,
            StatusCode::OK,
            "the round's public Get must stay answerable: {body:?}"
        );
        let body = body.expect("task body");
        let answered = body["status"]["state"].as_str().unwrap_or_default();
        if answered == state {
            return body;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "the REST round never reached {state} (last={answered}, starts={}, executions={})",
            world.journal.starts_seen.load(Ordering::SeqCst),
            world.journal.executions.load(Ordering::SeqCst)
        );
    }
}

/// C1: the flagship route through the real assembly — the mounted REST
/// `message:send` of the published group derives its session, admits through
/// the Worker-side strict seam, executes a full strict round (both nodes,
/// real downstream transport) and reaches its authoritative terminal. The
/// strict pipeline is asserted through the licensed rounds repository
/// itself, so a generic (non-strict) task can never satisfy this vector.
#[tokio::test]
async fn rest_message_send_completes_a_strict_round_over_real_joints() {
    bounded::<300>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        let _connector = world.start_connector();
        let app = world
            .assembly
            .router
            .clone()
            .layer(axum::extract::Extension(caller()));

        // Non-blocking send: the receipt answers immediately with the
        // round's public face; execution then proves itself below.
        let (status, body) = response_of(&app, rest_send("bridge-first", "please research", false)).await;
        assert_eq!(
            status,
            StatusCode::OK,
            "the flagship group send must answer with its receipt: {body:?}"
        );
        let body = body.expect("receipt body");
        let tid = bcs_dist_model::Tid(body["id"].as_str().expect("receipt task id").to_owned());
        let sid = bcs_dist_model::Sid(
            body["contextId"].as_str().expect("receipt session id").to_owned(),
        );
        assert!(
            bcs_service_api::application::dist_gateway::CreationSource::of_sid(&sid)
                == Some(bcs_service_api::application::dist_gateway::CreationSource::GroupA2a),
            "the derived session must live in the group-entrance namespace: {sid:?}"
        );

        // The two-node workflow runs through the real downstream lane.
        until_start(&world).await;
        world.journal.finish(NODE_RESEARCH);
        until_start(&world).await;
        // Capture a valid in-progress feed snapshot, then finish the real round.
        // Restoring this lagging projection below simulates a reader that has
        // not yet observed terminal publication; it does not change authority.
        let feed_key = world.assembly.roles.layout.task(&tid).unwrap();
        let working = world.model.read_document(&feed_key).await.unwrap().expect("working feed");
        world.journal.finish(NODE_REVIEW);

        // The round reaches its authoritative terminal through the licensed
        // strict cut and the public Get answers Completed.
        let deadline = tokio::time::Instant::now() + Duration::from_secs(120);
        let key = loop {
            world.drive(2).await;
            let key = bcs_dist_model::RoundKey {
                scope: scope(),
                group: group_id(),
                sid: sid.clone(),
                tid: tid.clone(),
                generation: std::num::NonZeroU64::new(1).unwrap(),
            };
            if world
                .round_view(&key)
                .await
                .is_some_and(|view| view.terminal.is_some())
            {
                break key;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "the REST round never closed through the strict authority"
            );
        };
        let completed =
            until_rest_task_state(&world, &app, &tid, "TASK_STATE_COMPLETED").await;
        let parts: Vec<_> = completed["artifacts"]
            .as_array()
            .expect("artifact array")
            .iter()
            .flat_map(|artifact| artifact["parts"].as_array().expect("part array"))
            .cloned()
            .collect();
        assert_eq!(
            parts,
            [json!({"text": "answer-of-review"})],
            "Completed must disclose the final node's answer, not the research input: {completed}"
        );

        let published = world.model.read_document(&feed_key).await.unwrap().expect("feed");
        assert!(matches!(
            world.model.replace_document(&feed_key, published.version, &working.value).await.unwrap(),
            WriteOutcome::Applied { .. }
        ));
        let (status, lagged) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", tid.0))
                .body(Body::empty())
                .unwrap(),
        ).await;
        assert_eq!(status, StatusCode::OK);
        let lagged = lagged.expect("task despite publication lag");
        assert_eq!(lagged["status"]["state"], "TASK_STATE_COMPLETED", "{lagged}");
        assert_eq!(lagged["artifacts"], completed["artifacts"], "{lagged}");

        // A same-session snapshot is not enough to make an unaccepted task
        // public, even while another task has an accepted strict license.
        let staged_tid = bcs_dist_model::Tid(format!("{}.not-accepted", sid.0));
        let staged_key = world.assembly.roles.layout.task(&staged_tid).unwrap();
        let mut staged = working.value.clone();
        staged["tid"] = json!(staged_tid.0);
        assert!(matches!(
            world.model.create_document(&staged_key, &staged).await.unwrap(),
            WriteOutcome::Applied { .. }
        ));
        let (status, body) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", staged_tid.0))
                .body(Body::empty())
                .unwrap(),
        ).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "staged task must stay private: {body:?}");

        // The strict license seals the round: the checkpoint's runner state
        // names exactly this round's request (the admission that only the
        // strict seam can install), and the checkpoint carries the round's
        // group_admission marker — the authoritative difference between the
        // bridged strict round and the generic FsmGroup task the REST route
        // used to create before the bridge.
        let view = world.round_view(&key).await.expect("final round view");
        assert!(view.terminal.is_some(), "the authority sealed the round");
        assert!(
            view.result.is_some(),
            "the closed round discloses its archived result through the strict cut"
        );

        // A retry of the same message key replays the SAME round (the stable
        // request identity), never admits a second one.
        let (status, body) = response_of(&app, rest_send("bridge-first", "please research", false)).await;
        assert_eq!(status, StatusCode::OK, "retry answers its receipt: {body:?}");
        assert_eq!(
            body.expect("retry body")["id"].as_str(),
            Some(tid.0.as_str()),
            "the retry replays the original round's task"
        );
    })
    .await;
}

/// I1: public reads route through the strict authority cut — a superseded
/// (closed) round of a group-session stays resolvable through the mounted
/// Get and stays a row of the mounted List the same cut serves, hot or cold.
#[tokio::test]
async fn superseded_rounds_stay_public_through_get_and_list() {
    bounded::<300>(async {
        let world = CompanionWorld::assemble(enabled_config()).await;
        let _connector = world.start_connector();
        let app = world
            .assembly
            .router
            .clone()
            .layer(axum::extract::Extension(caller()));

        // Round one of the seeded strict session closes through the whole
        // real pipeline; its successor then takes the session.
        let a = world.round("sup-first", 1);
        let a_tid = a.input.key.tid.clone();
        assert!(matches!(world.admit_round(&a).await, Admission::New { .. }));
        until_start(&world).await;
        world.journal.finish(NODE_RESEARCH);
        until_start(&world).await;
        world.journal.finish(NODE_REVIEW);
        let deadline = tokio::time::Instant::now() + Duration::from_secs(120);
        loop {
            world.drive(2).await;
            if world
                .round_view(&a.input.key)
                .await
                .is_some_and(|view| view.terminal.is_some())
            {
                break;
            }
            assert!(
                tokio::time::Instant::now() < deadline,
                "the first round never closed through the authority"
            );
        }

        // While the round's hot terminal is retained, Get answers it.
        let (status, body) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", a_tid.0))
                .body(Body::empty())
                .unwrap(),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "hot-closed round Get: {body:?}");
        assert_eq!(
            body.expect("hot body")["status"]["state"],
            "TASK_STATE_COMPLETED",
            "the retained authority discloses the closed round"
        );

        // The successor generation installs and compacts the checkpoint.
        let b = world.round("sup-second", 2);
        assert!(matches!(world.admit_round(&b).await, Admission::New { .. }));
        let deadline = tokio::time::Instant::now() + Duration::from_secs(120);
        loop {
            world.drive(2).await;
            if world.round_view(&b.input.key).await.is_some() {
                break;
            }
            assert!(tokio::time::Instant::now() < deadline, "B never installed");
        }

        // The superseded round stays resolvable through the authority cut:
        // the cold index serves its terminal, and the mounted Get answers
        // the same closed round — never a NotFound narrowing.
        let (status, body) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks/{}", a_tid.0))
                .body(Body::empty())
                .unwrap(),
        )
        .await;
        assert_eq!(
            status,
            StatusCode::OK,
            "the superseded closed round must stay publicly resolvable: {body:?}"
        );
        let body = body.expect("cold body");
        assert_eq!(
            body["status"]["state"], "TASK_STATE_COMPLETED",
            "the cold-served round keeps its terminal content: {body}"
        );

        // The same cut's List carries the closed round next to its
        // successor with a stable total: hot→cold never makes rows vanish.
        let (status, body) = response_of(
            &app,
            Request::get(format!("{GROUPS_PREFIX}/{GROUP}/tasks")).body(Body::empty()).unwrap(),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "the group listing answered: {body:?}");
        let body = body.expect("list body");
        let listed: Vec<&str> = body["tasks"]
            .as_array()
            .expect("tasks array")
            .iter()
            .filter_map(|task| task["id"].as_str())
            .collect();
        assert!(
            listed.contains(&a_tid.0.as_str()),
            "the closed round stays a row of its group's public index: {body}"
        );
        assert!(
            body["totalSize"].as_u64().unwrap_or(0) >= 2,
            "the cut's total counts the closed round: {body}"
        );
    })
    .await;
}
