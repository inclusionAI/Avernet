//! Task 3 wiring proof: the legacy `/friends/*` routes must reach the
//! edge-permission `ConnectService` on the in-memory server path too.
//!
//! Before this wiring, the in-memory server answered the legacy routes from
//! `bcs_friend::Friend` (its own `bcs_friendships` / friend-request tables), so
//! `state.connect_service` saw nothing. The assertion below fails unless the
//! legacy route persists its pending request into the store the connect
//! service reads.
//!
//! Task 4 adds the mirror requirement on the OpenAPI v1 surface: the in-memory
//! composition root must hand the V1 invitation/friendship facade the SAME
//! connect service, otherwise v1 `DELETE .../friendships/{friend}` revokes
//! nothing while v1 `GET .../friendships` still reports the edge.

mod helpers;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use bcs_domain::edge_permission::{RequestKind, RequestStatus};
use serde_json::json;

const TEST_GATEWAY_PRINCIPAL_SIGNING_KEY: &[u8] = b"test-only-gateway-principal-signing-key";

/// Mint the real process verifier's Gateway-principal shape for a Human.
fn principal(user: &str) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock is after the Unix epoch")
        .as_secs();
    let mut header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    jsonwebtoken::encode(
        &header,
        &json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now.saturating_sub(1),
            "exp": now + 120,
            "principals": [{
                "type": "user",
                "tenant": "tenant-a",
                "subject": {
                    "id": user,
                    "username": user,
                    "tenant_id": "tenant-a"
                }
            }]
        }),
        &jsonwebtoken::EncodingKey::from_secret(TEST_GATEWAY_PRINCIPAL_SIGNING_KEY),
    )
    .expect("test principal token signs")
}

/// Register a Bot owned by `owner` through the trusted v1 registration path,
/// so the authority chain carries an approved owner edge for that Human (and
/// the Bot is `protected`, i.e. a friend request targeting it stays pending).
async fn register_owned_bot(addr: SocketAddr, owner: &str, name: &str) -> (String, String) {
    let client = reqwest::Client::new();
    let token_response = client
        .get(format!(
            "http://{addr}/openapi/v1/collaboration/register/token"
        ))
        .header("x-avernet-principal", principal(owner))
        .send()
        .await
        .expect("register token request");
    let status = token_response.status();
    let token_body: serde_json::Value = token_response.json().await.expect("register token body");
    assert_eq!(status, reqwest::StatusCode::OK, "body: {token_body}");
    let register_token = token_body["data"]["token"]
        .as_str()
        .expect("register token")
        .to_string();

    let registration = client
        .post(format!("http://{addr}/openapi/v1/collaboration/register"))
        .query(&[("token", register_token.as_str()), ("bot-name", name)])
        .send()
        .await
        .expect("registration request");
    let status = registration.status();
    let body: serde_json::Value = registration.json().await.expect("registration body");
    assert_eq!(status, reqwest::StatusCode::CREATED, "body: {body}");
    (
        body["data"]["bot_uuid"]
            .as_str()
            .expect("bot uuid")
            .to_string(),
        body["data"]["bot_token"]
            .as_str()
            .expect("bot token")
            .to_string(),
    )
}

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
/// Task 4: the in-memory OpenAPI v1 friendship surface must run on the SAME
/// connect store as the legacy routes. `DELETE .../friendships/{friend}` has to
/// revoke the edge (and report it), and the v1 listing has to agree.
#[tokio::test]
async fn in_memory_v1_delete_friendship_revokes_the_connect_edge() {
    let temp_dir = helpers::create_temp_bots_dir();
    let bots_dir: PathBuf = temp_dir.path().to_path_buf();
    let (addr, _handle, state) = helpers::start_test_server_with_state(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Two DIFFERENT Human owners: a same-owner Bot↔Bot request auto-approves,
    // and this case needs a real pending request to accept.
    let (caller_uuid, caller_token) =
        register_owned_bot(addr, "owner-delete-a", "v1-delete-caller").await;
    let (target_uuid, target_token) =
        register_owned_bot(addr, "owner-delete-b", "v1-delete-target").await;

    // Establish the friendship through the legacy bot↔bot flow.
    let http = reqwest::Client::new();
    let created = http
        .post(format!("http://{addr}/friends/request"))
        .bearer_auth(&caller_token)
        .json(&json!({ "to_bot": target_uuid }))
        .send()
        .await
        .expect("send friend request");
    let status = created.status();
    let body: serde_json::Value = created.json().await.expect("friend request body");
    assert_eq!(status, reqwest::StatusCode::CREATED, "body: {body}");
    let request_id = body["data"]["id"]
        .as_str()
        .expect("created request carries an id")
        .to_string();

    let accepted = http
        .post(format!("http://{addr}/friends/requests/{request_id}/accept"))
        .bearer_auth(&target_token)
        .send()
        .await
        .expect("accept friend request");
    assert_eq!(accepted.status(), reqwest::StatusCode::OK);

    // Pre-condition: the edge lives in the very store the v1 facade must read.
    let friends = state
        .connect_service
        .list_friends(&caller_uuid)
        .await
        .expect("read connect friends");
    assert!(
        friends.iter().any(|entry| entry.actor_id == target_uuid),
        "accepted request must create the connect edge: {friends:?}",
    );

    let list_url = format!(
        "http://{addr}/openapi/v1/collaboration/bots/{caller_uuid}/friendships"
    );
    let listed: serde_json::Value = http
        .get(&list_url)
        .header("x-avernet-principal", principal("owner-delete-a"))
        .send()
        .await
        .expect("v1 list friendships")
        .json()
        .await
        .expect("v1 list body");
    assert_eq!(listed["data"]["total"], 1, "body: {listed}");

    let deleted = http
        .delete(format!(
            "http://{addr}/openapi/v1/collaboration/bots/{caller_uuid}/friendships/{target_uuid}"
        ))
        .header("x-avernet-principal", principal("owner-delete-a"))
        .send()
        .await
        .expect("v1 delete friendship");
    let status = deleted.status();
    let body: serde_json::Value = deleted.json().await.expect("v1 delete body");
    assert_eq!(status, reqwest::StatusCode::OK, "body: {body}");
    assert_eq!(body["code"], 20_000, "body: {body}");
    assert_eq!(
        body["data"],
        json!({ "deleted": true }),
        "the in-memory v1 state must revoke through the real connect service: {body}",
    );

    // The edge is gone from the connect store and from the v1 projection.
    let remaining = state
        .connect_service
        .list_friends(&caller_uuid)
        .await
        .expect("read connect friends after delete");
    assert!(
        !remaining.iter().any(|entry| entry.actor_id == target_uuid),
        "connect edge must be revoked: {remaining:?}",
    );
    let listed: serde_json::Value = http
        .get(&list_url)
        .header("x-avernet-principal", principal("owner-delete-a"))
        .send()
        .await
        .expect("v1 list friendships after delete")
        .json()
        .await
        .expect("v1 list body after delete");
    assert_eq!(listed["data"]["total"], 0, "body: {listed}");
}

/// F9: the OpenAPI v1 create path reaches the same connect store as the legacy
/// route, and the created projection is the durable pending row.
#[tokio::test]
async fn v1_friend_request_pending_reaches_connect_store() {
    let temp_dir = helpers::create_temp_bots_dir();
    let bots_dir: PathBuf = temp_dir.path().to_path_buf();
    let (addr, _handle, state) = helpers::start_test_server_with_state(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Different owners so the request stays pending (a same-owner Bot↔Bot
    // request auto-approves).
    let (caller_uuid, _caller_token) =
        register_owned_bot(addr, "owner-v1-pending-a", "v1-pending-caller").await;
    let (target_uuid, _target_token) =
        register_owned_bot(addr, "owner-v1-pending-b", "v1-pending-target").await;

    let response = reqwest::Client::new()
        .post(format!(
            "http://{addr}/openapi/v1/collaboration/bots/{caller_uuid}/friend-requests"
        ))
        .header("x-avernet-principal", principal("owner-v1-pending-a"))
        .json(&json!({ "to_bot_uuid": target_uuid }))
        .send()
        .await
        .expect("v1 create friend request");
    let status = response.status();
    let body: serde_json::Value = response.json().await.expect("v1 create body");
    assert_eq!(status, reqwest::StatusCode::CREATED, "body: {body}");
    assert_eq!(body["code"], 20_100, "body: {body}");
    assert_eq!(body["data"]["status"], "pending", "body: {body}");

    let request_id = body["data"]["request_id"]
        .as_str()
        .expect("v1 created request carries an id")
        .to_string();
    let request = state
        .connect_service
        .get_request(&request_id)
        .await
        .expect("the v1 create path must persist the connect request");
    assert_eq!(request.request_kind, RequestKind::Connect);
    assert_eq!(request.status, RequestStatus::Pending);
    assert_eq!(request.from_id, caller_uuid);
    assert_eq!(request.to_id, target_uuid);
}

/// F9: accepting through v1 answers with the decided projection rebuilt from
/// the connect store (F4), so timestamps and status are the durable row's.
#[tokio::test]
async fn v1_accept_returns_decided_request_projection() {
    let temp_dir = helpers::create_temp_bots_dir();
    let bots_dir: PathBuf = temp_dir.path().to_path_buf();
    let (addr, _handle, state) = helpers::start_test_server_with_state(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let (caller_uuid, _caller_token) =
        register_owned_bot(addr, "owner-v1-accept-a", "v1-accept-caller").await;
    let (target_uuid, _target_token) =
        register_owned_bot(addr, "owner-v1-accept-b", "v1-accept-target").await;

    let http = reqwest::Client::new();
    let created = http
        .post(format!(
            "http://{addr}/openapi/v1/collaboration/bots/{caller_uuid}/friend-requests"
        ))
        .header("x-avernet-principal", principal("owner-v1-accept-a"))
        .json(&json!({ "to_bot_uuid": target_uuid }))
        .send()
        .await
        .expect("v1 create friend request");
    let status = created.status();
    let body: serde_json::Value = created.json().await.expect("v1 create body");
    assert_eq!(status, reqwest::StatusCode::CREATED, "body: {body}");
    let request_id = body["data"]["request_id"]
        .as_str()
        .expect("v1 created request carries an id")
        .to_string();

    let accepted = http
        .post(format!(
            "http://{addr}/openapi/v1/collaboration/friend-requests/{request_id}/accept"
        ))
        .header("x-avernet-principal", principal("owner-v1-accept-b"))
        .send()
        .await
        .expect("v1 accept friend request");
    let status = accepted.status();
    let body: serde_json::Value = accepted.json().await.expect("v1 accept body");
    assert_eq!(status, reqwest::StatusCode::OK, "body: {body}");
    assert_eq!(body["code"], 20_000, "body: {body}");
    assert_eq!(body["data"]["request_id"], request_id, "body: {body}");
    assert_eq!(body["data"]["status"], "accepted", "body: {body}");
    assert_eq!(body["data"]["from_bot_uuid"], caller_uuid, "body: {body}");
    assert_eq!(body["data"]["to_bot_uuid"], target_uuid, "body: {body}");
    let created_at = body["data"]["created_at"]
        .as_u64()
        .expect("created_at is a number");
    let updated_at = body["data"]["updated_at"]
        .as_u64()
        .expect("updated_at is a number");
    assert!(created_at > 0, "the decided projection carries real timestamps");
    assert!(updated_at > 0, "the decided projection carries real timestamps");

    // The mutation is durable and reflected in the connect store.
    let request = state
        .connect_service
        .get_request(&request_id)
        .await
        .expect("the decided request stays readable");
    assert_eq!(request.status, RequestStatus::Approved);
}
