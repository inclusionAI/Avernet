//! Task 3 wiring proof: the legacy `/friends/*` routes must reach the
//! edge-permission `ConnectService` on the in-memory server path too.
//!
//! Before this wiring, the in-memory server answered the legacy routes from
//! `bcs_friend::Friend` (its own `bcs_friendships` / friend-request tables), so
//! `state.connect_service` saw nothing. The assertion below fails unless the
//! legacy route persists its pending request into the store the connect
//! service reads.

mod helpers;

use std::path::PathBuf;
use std::time::Duration;

use bcs_domain::edge_permission::{RequestKind, RequestStatus};
use serde_json::json;

#[tokio::test]
async fn in_memory_server_friend_request_flow_reaches_connect_store() {
    let temp_dir = helpers::create_temp_bots_dir();
    let bots_dir: PathBuf = temp_dir.path().to_path_buf();
    let (addr, _handle, state) = helpers::start_test_server_with_state(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut caller = helpers::MockBot::connect(addr).await;
    caller.register("connect-caller", &[], addr).await;
    let mut target = helpers::MockBot::connect(addr).await;
    target.register("connect-target", &[], addr).await;
    // A protected target takes the manual-approval branch, i.e. a persisted
    // pending connect request.
    target
        .http_client(addr)
        .set_visibility(&target.bot_id, "protected")
        .await
        .expect("set target visibility");

    let response = reqwest::Client::new()
        .post(format!("http://{addr}/friends/request"))
        .bearer_auth(&caller.token)
        .json(&json!({ "to_bot": target.bot_id }))
        .send()
        .await
        .expect("send friend request");
    let status = response.status();
    let body: serde_json::Value = response.json().await.expect("json body");
    assert_eq!(status, reqwest::StatusCode::CREATED, "body: {body}");

    let request_id = body["data"]["id"]
        .as_str()
        .expect("created request carries an id")
        .to_string();
    let request = state
        .connect_service
        .get_request(&request_id)
        .await
        .expect("legacy route must persist the connect request in the edge-permission store");
    assert_eq!(request.request_kind, RequestKind::Connect);
    assert_eq!(request.status, RequestStatus::Pending);
    assert_eq!(request.from_id, caller.bot_id);
    assert_eq!(request.to_id, target.bot_id);
}