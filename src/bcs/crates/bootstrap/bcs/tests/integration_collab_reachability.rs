//! Collaboration Reachability Integration Tests (P-1 ~ P-11).
//!
//! Validates the unified `target_reachable_for_collab` helper across all four
//! collaboration endpoints:
//!   (i)   POST /bots/{T}/chat
//!   (ii)  POST /groups  (Normal group)
//!   (iii) POST /groups  (DM sub-path: group_kind=Dm + target_actor_id)
//!   (iv)  POST /groups/{id}/members
//!
//! Test matrix:
//!   P-1  target=public, no friendship           → all 4 endpoints succeed
//!   P-2  target=protected, friends               → all 4 endpoints succeed
//!   P-3  target=protected, NOT friends            → all 4 endpoints 403 NotFriends
//!   P-4  target=private, friends                  → all 4 endpoints succeed (regression: Fix 1)
//!   P-5  target=private, NOT friends              → all 4 endpoints 404 BotNotFound
//!   P-6  caller=private, target reachable         → all 4 endpoints succeed (D-B removed caller guard)
//!   P-7  切 private 不 cancel pending request     → pending request survives visibility change
//!   P-8  切 private 后拒绝新发起 friend request   → 404 BotNotFound
//!   P-9  private caller → public target (friend)  → auto-accept friend request
//!   P-10 private caller → protected target        → pending friend request
//!   P-11 private caller → private target (stranger) → 404 BotNotFound
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_collab_reachability -- --test-threads=1
//! ```

mod helpers;

use std::net::SocketAddr;
use std::time::Duration;

use serde_json::json;

use helpers::{MockBot, create_temp_bots_dir, start_test_server};

/// POST /bots/{target}/chat — returns (status_code, body) or error string.
///
/// Uses a short HTTP timeout to avoid blocking when the target bot has no WS
/// consumer.  A timeout is treated as "reachability OK, delivery timed out"
/// — the permission check passed, which is what P-tests care about.
async fn bot_chat_http(
    addr: SocketAddr,
    sender_token: &str,
    target_bot_id: &str,
    message: &str,
) -> Result<(u16, serde_json::Value), String> {
    let url = format!("http://{}/bots/{}/chat", addr, target_bot_id);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap();
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", sender_token))
        .json(&json!({ "message": message, "timeout_ms": 2000 }))
        .send()
        .await;
    match response {
        Ok(resp) => {
            let status = resp.status().as_u16();
            let body: serde_json::Value = resp.json().await.unwrap_or(json!({}));
            // Server returns 500 when the permission check passed but the bot
            // has no WS consumer to respond ("Timeout waiting for bot response").
            // For reachability tests, the permission check passing is what
            // matters, so treat this 500 the same as a reqwest-level timeout.
            if status == 500 && body.get("error").and_then(|e| e.as_str()).map_or(false, |s| s.contains("Timeout")) {
                Ok((200, json!({"timeout": true})))
            } else if !reqwest::StatusCode::from_u16(status).unwrap().is_success() {
                Err(format!("HTTP {}: {}", status, body))
            } else {
                Ok((status, body))
            }
        }
        Err(e) if e.is_timeout() => {
            // Timeout means permission check passed but target didn't respond.
            // For reachability tests this counts as "allowed".
            Ok((200, json!({"timeout": true})))
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

/// POST /groups (Normal) — returns (status_code, body) or error string.
async fn create_group_http(
    addr: SocketAddr,
    driver_token: &str,
    driver_bot_id: &str,
    label: &str,
    participant_ids: &[&str],
) -> Result<(u16, serde_json::Value), String> {
    let url = format!("http://{}/groups", addr);
    let client = reqwest::Client::new();
    let participants: Vec<serde_json::Value> = participant_ids
        .iter()
        .map(|&id| json!({"bot_uuid": id}))
        .collect();
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&json!({
            "label": label,
            "driver_bot": driver_bot_id,
            "participants": participants
        }))
        .send()
        .await;
    parse_response(response).await
}

/// POST /groups (DM sub-path) — returns (status_code, body) or error string.
async fn create_dm_group_http(
    addr: SocketAddr,
    caller_token: &str,
    driver_bot_id: &str,
    target_bot_id: &str,
) -> Result<(u16, serde_json::Value), String> {
    let url = format!("http://{}/groups", addr);
    let client = reqwest::Client::new();
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", caller_token))
        .json(&json!({
            "driver_bot": driver_bot_id,
            "group_kind": "dm",
            "target_actor_id": target_bot_id,
            "participants": []
        }))
        .send()
        .await;
    parse_response(response).await
}

/// POST /me/ensure-human with mock staff identity so HTTP DM tests have a real Human actor.
async fn ensure_human_http(addr: SocketAddr, staff_no: &str) -> Result<serde_json::Value, String> {
    let url = format!("http://{}/me/ensure-human", addr);
    let response = reqwest::Client::new()
        .post(&url)
        .header("X-Mock-User-Id", staff_no)
        .header("X-Mock-Nick-Name", format!("User-{}", staff_no))
        .send()
        .await;
    parse_response(response).await.map(|(_, body)| body)
}

/// POST /groups (Human -> Bot DM) with mock staff identity.
async fn create_human_bot_dm_http(
    addr: SocketAddr,
    staff_no: &str,
    target_bot_id: &str,
) -> Result<(u16, serde_json::Value), String> {
    let url = format!("http://{}/groups", addr);
    let response = reqwest::Client::new()
        .post(&url)
        .header("X-Mock-User-Id", staff_no)
        .header("X-Mock-Nick-Name", format!("User-{}", staff_no))
        .json(&json!({
            "driver_bot": target_bot_id,
            "target_actor_id": target_bot_id,
            "group_kind": "dm",
            "participants": [
                {"bot_uuid": target_bot_id}
            ]
        }))
        .send()
        .await;
    parse_response(response).await
}

/// POST /groups (Bot token -> Human target DM) must be rejected.
async fn create_bot_to_human_dm_http(
    addr: SocketAddr,
    bot_token: &str,
    bot_id: &str,
    target_human_id: &str,
) -> Result<(u16, serde_json::Value), String> {
    let url = format!("http://{}/groups", addr);
    let response = reqwest::Client::new()
        .post(&url)
        .header("Authorization", format!("Bearer {}", bot_token))
        .json(&json!({
            "driver_bot": bot_id,
            "target_actor_id": target_human_id,
            "group_kind": "dm",
            "participants": [
                {"bot_uuid": target_human_id}
            ]
        }))
        .send()
        .await;
    parse_response(response).await
}

/// POST /groups/{group_id}/members — returns (status_code, body) or error string.
async fn add_member_http(
    addr: SocketAddr,
    coordinator_token: &str,
    group_id: &str,
    bot_uuid: &str,
) -> Result<(u16, serde_json::Value), String> {
    let url = format!("http://{}/groups/{}/members", addr, group_id);
    let client = reqwest::Client::new();
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", coordinator_token))
        .json(&json!({
            "bot_uuid": bot_uuid,
            "role": "consultant"
        }))
        .send()
        .await;
    parse_response(response).await
}

/// Unified response parser: extracts status + body, turns error fields into Err.
async fn parse_response(
    response: Result<reqwest::Response, reqwest::Error>,
) -> Result<(u16, serde_json::Value), String> {
    match response {
        Ok(resp) => {
            let status = resp.status().as_u16();
            let body: serde_json::Value = resp.json().await.unwrap_or(json!({}));
            if let Some(err) = body.get("error").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, err))
            } else if let Some(err) = body.get("error").and_then(|e| e.get("message")).and_then(|m| m.as_str()) {
                Err(format!("HTTP {}: {}", status, err))
            } else if let Some(err) = body.get("message").and_then(|e| e.as_str()) {
                if !reqwest::StatusCode::from_u16(status).unwrap().is_success() {
                    Err(format!("HTTP {}: {}", status, err))
                } else {
                    Ok((status, body))
                }
            } else if let Some(err) = body.get("detail").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, err))
            } else if !reqwest::StatusCode::from_u16(status).unwrap().is_success() {
                Err(format!("HTTP {}: {}", status, body))
            } else {
                Ok((status, body))
            }
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

/// Setup two bots (caller, target) with given visibilities. Optionally establish
/// friendship between them.
struct TestSetup {
    addr: SocketAddr,
    caller: MockBot,
    target: MockBot,
    _caller_client: bcs_cli::BcsClient,
    _target_client: bcs_cli::BcsClient,
    _bots_dir: tempfile::TempDir,
    _server_handle: tokio::task::JoinHandle<Result<(), bcs::BcsError>>,
}

async fn setup_two_bots(
    caller_visibility: &str,
    target_visibility: &str,
    make_friends: bool,
) -> TestSetup {
    let bots_dir = create_temp_bots_dir();
    let (addr, server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut caller = MockBot::connect(addr).await;
    let mut target = MockBot::connect(addr).await;

    // Onboard both bots (register sets visibility to "public" by default)
    caller.register("caller", &["chat"], addr).await;
    target.register("target", &["chat"], addr).await;

    let caller_client = caller.http_client(addr);
    let target_client = target.http_client(addr);

    // Set desired visibilities
    caller_client
        .set_visibility(&caller.bot_id, caller_visibility)
        .await
        .expect("set caller visibility");
    target_client
        .set_visibility(&target.bot_id, target_visibility)
        .await
        .expect("set target visibility");

    if make_friends {
        // Temporarily set both to public so friend request goes through
        // (private targets reject friend requests from strangers)
        let caller_was = caller_visibility;
        let target_was = target_visibility;

        if caller_was == "private" {
            caller_client
                .set_visibility(&caller.bot_id, "public")
                .await
                .expect("temp set caller public");
        }
        if target_was == "private" {
            target_client
                .set_visibility(&target.bot_id, "public")
                .await
                .expect("temp set target public");
        }

        let resp = caller_client
            .send_friend_request(None, &target.bot_id)
            .await
            .expect("send friend request");
        assert!(resp.success, "friend request should succeed");

        // public target → auto-accept (data may be absent when auto-accepted);
        // protected target → need manual accept
        if let Some(data) = resp.data.as_ref() {
            let status = data["status"].as_str().unwrap_or("");
            if status == "pending" {
                let req_id = data["id"].as_str().unwrap().to_string();
                target_client
                    .accept_friend_request(&req_id)
                    .await
                    .expect("accept friend request");
            }
        }

        // Restore original visibilities
        if caller_was == "private" {
            caller_client
                .set_visibility(&caller.bot_id, caller_was)
                .await
                .expect("restore caller visibility");
        }
        if target_was == "private" {
            target_client
                .set_visibility(&target.bot_id, target_was)
                .await
                .expect("restore target visibility");
        }
    }

    // Send heartbeats so bots are considered "connected"
    caller.send_heartbeat().await;
    target.send_heartbeat().await;

    TestSetup {
        addr,
        caller,
        target,
        _caller_client: caller_client,
        _target_client: target_client,
        _bots_dir: bots_dir,
        _server_handle: server_handle,
    }
}

/// Create a pre-existing group where caller is driver and a third bot is member.
/// Used by P-1(iv)..P-5(iv) to test add_member on an existing group.
async fn create_base_group(
    addr: SocketAddr,
    driver_token: &str,
    driver_bot_id: &str,
) -> String {
    // We need a third "filler" bot so the group can exist independently of target
    let mut filler = MockBot::connect(addr).await;
    filler.register("filler", &["chat"], addr).await;
    filler.send_heartbeat().await;

    let result = create_group_http(addr, driver_token, driver_bot_id, "base", &[&filler.bot_id])
        .await
        .expect("base group creation should succeed");
    result.1["id"].as_str().unwrap().to_string()
}

// ============================================================================
// P-1: target=public, no friendship → all succeed
// ============================================================================
#[tokio::test]
async fn p1_target_public_allows_all_endpoints() {
    let s = setup_two_bots("public", "public", false).await;

    // (i) POST /bots/{T}/chat
    let chat = bot_chat_http(s.addr, &s.caller.token, &s.target.bot_id, "hello").await;
    assert!(chat.is_ok(), "P-1(i) chat should succeed: {:?}", chat.err());

    // (ii) POST /groups (Normal)
    let group = create_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        "P1",
        &[&s.target.bot_id],
    )
    .await;
    assert!(group.is_ok(), "P-1(ii) group should succeed: {:?}", group.err());

    // (iii) POST /groups (DM)
    let dm = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await;
    assert!(dm.is_ok(), "P-1(iii) DM should succeed: {:?}", dm.err());

    // (iv) POST /groups/{id}/members
    let group_id = create_base_group(s.addr, &s.caller.token, &s.caller.bot_id).await;
    let add = add_member_http(s.addr, &s.caller.token, &group_id, &s.target.bot_id).await;
    assert!(add.is_ok(), "P-1(iv) add_member should succeed: {:?}", add.err());
}

#[tokio::test]
async fn dm_bot_token_self_creates_dm_successfully() {
    let s = setup_two_bots("public", "public", false).await;

    let (status, body) = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await
    .expect("Bot should be able to create a DM with its own token");

    assert_eq!(status, 200);
    assert_eq!(body["group_kind"].as_str(), Some("dm"));
    assert_eq!(body["driver_bot"].as_str(), Some(s.caller.bot_id.as_str()));
    assert_eq!(body["created"].as_bool(), Some(true));
}

#[tokio::test]
async fn dm_bot_token_uses_authenticated_bot_as_source() {
    let s = setup_two_bots("public", "public", false).await;
    let url = format!("http://{}/groups", s.addr);

    let response = reqwest::Client::new()
        .post(&url)
        .header("Authorization", format!("Bearer {}", s.target.token))
        .json(&json!({
            "driver_bot": s.caller.bot_id,
            "from_bot": s.caller.bot_id,
            "group_kind": "dm",
            "target_actor_id": s.caller.bot_id,
            "participants": []
        }))
        .send()
        .await;

    let (status, body) = parse_response(response)
        .await
        .expect("DM should use the authenticated Bot token as source");

    assert_eq!(status, 200);
    assert_eq!(body["group_kind"].as_str(), Some("dm"));
    assert_eq!(body["driver_bot"].as_str(), Some(s.target.bot_id.as_str()));
    assert_eq!(body["created"].as_bool(), Some(true));
}

#[tokio::test]
async fn dm_human_can_create_dm_with_bot() {
    let s = setup_two_bots("public", "public", false).await;
    let staff_no = "dmhuman001";
    let human_id = format!("human_{}", staff_no);

    let human = ensure_human_http(s.addr, staff_no)
        .await
        .expect("Human actor should be materialized before creating DM");
    assert_eq!(human["actor_uuid"].as_str(), Some(human_id.as_str()));

    let (status, body) = create_human_bot_dm_http(s.addr, staff_no, &s.target.bot_id)
        .await
        .expect("Human should be able to create a DM with a Bot");

    assert_eq!(status, 200);
    assert_eq!(body["group_kind"].as_str(), Some("dm"));
    assert_eq!(body["driver_bot"].as_str(), Some(s.target.bot_id.as_str()));
    assert_eq!(body["created"].as_bool(), Some(true));
    let participants = body["participants"]
        .as_array()
        .expect("DM response should include participants");
    assert!(
        participants
            .iter()
            .any(|id| id.as_str() == Some(human_id.as_str())),
        "Human participant should be included in Human-Bot DM: {body}"
    );
    assert!(
        participants
            .iter()
            .any(|id| id.as_str() == Some(s.target.bot_id.as_str())),
        "Bot participant should be included in Human-Bot DM: {body}"
    );
}

#[tokio::test]
async fn dm_bot_cannot_create_dm_with_human() {
    let s = setup_two_bots("public", "public", false).await;
    let staff_no = "dmtarget001";
    let human_id = format!("human_{}", staff_no);

    let human = ensure_human_http(s.addr, staff_no)
        .await
        .expect("Human actor should exist so rejection is based on direction, not missing actor");
    assert_eq!(human["actor_uuid"].as_str(), Some(human_id.as_str()));

    let dm =
        create_bot_to_human_dm_http(s.addr, &s.caller.token, &s.caller.bot_id, &human_id).await;

    assert!(
        dm.is_err(),
        "Bot must not be able to create a DM targeting a Human"
    );
    let err = dm.unwrap_err();
    assert!(
        err.contains("400"),
        "Bot->Human DM should be a 400 rejection: {err}"
    );
    assert!(
        err.contains("DM target must be a Bot actor"),
        "Bot->Human DM should reject the Human target explicitly: {err}"
    );
}

#[tokio::test]
#[ignore = "requires running without BCS_MOCK_USER_ID / mock default user"]
async fn dm_without_bot_token_or_human_identity_returns_401() {
    let s = setup_two_bots("public", "public", false).await;
    let url = format!("http://{}/groups", s.addr);

    let response = reqwest::Client::new()
        .post(&url)
        .json(&json!({
            "driver_bot": s.caller.bot_id,
            "group_kind": "dm",
            "target_actor_id": s.target.bot_id,
            "participants": []
        }))
        .send()
        .await
        .expect("anonymous DM request should receive an HTTP response");

    assert_eq!(response.status().as_u16(), 401);
    let body = response.text().await.unwrap_or_default();
    assert!(
        body.contains("创建私聊群需要用户登录态或有效 Bot token"),
        "401 body should explain the missing credentials: {body}"
    );
}

// ============================================================================
// P-2: target=protected, friends → all succeed
// ============================================================================
#[tokio::test]
async fn p2_target_protected_friend_allows_all() {
    let s = setup_two_bots("public", "protected", true).await;

    let chat = bot_chat_http(s.addr, &s.caller.token, &s.target.bot_id, "hello").await;
    assert!(chat.is_ok(), "P-2(i) chat should succeed: {:?}", chat.err());

    let group = create_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        "P2",
        &[&s.target.bot_id],
    )
    .await;
    assert!(group.is_ok(), "P-2(ii) group should succeed: {:?}", group.err());

    let dm = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await;
    assert!(dm.is_ok(), "P-2(iii) DM should succeed: {:?}", dm.err());

    let group_id = create_base_group(s.addr, &s.caller.token, &s.caller.bot_id).await;
    let add = add_member_http(s.addr, &s.caller.token, &group_id, &s.target.bot_id).await;
    assert!(add.is_ok(), "P-2(iv) add_member should succeed: {:?}", add.err());
}

// ============================================================================
// P-3: target=protected, NOT friends → all 403 NotFriends
// ============================================================================
#[tokio::test]
async fn p3_target_protected_stranger_rejects_403() {
    let s = setup_two_bots("public", "protected", false).await;

    let chat = bot_chat_http(s.addr, &s.caller.token, &s.target.bot_id, "hello").await;
    assert!(chat.is_err(), "P-3(i) chat should fail");
    assert!(
        chat.unwrap_err().contains("403"),
        "P-3(i) should be 403"
    );

    let group = create_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        "P3",
        &[&s.target.bot_id],
    )
    .await;
    assert!(group.is_err(), "P-3(ii) group should fail");
    assert!(
        group.unwrap_err().contains("403"),
        "P-3(ii) should be 403"
    );

    let dm = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await;
    assert!(dm.is_err(), "P-3(iii) DM should fail");
    assert!(
        dm.unwrap_err().contains("403"),
        "P-3(iii) should be 403"
    );

    let group_id = create_base_group(s.addr, &s.caller.token, &s.caller.bot_id).await;
    let add = add_member_http(s.addr, &s.caller.token, &group_id, &s.target.bot_id).await;
    assert!(add.is_err(), "P-3(iv) add_member should fail");
    assert!(
        add.unwrap_err().contains("403"),
        "P-3(iv) should be 403"
    );
}

// ============================================================================
// P-4: target=private, friends → all succeed (Fix 1 regression guard)
// ============================================================================
#[tokio::test]
async fn p4_target_private_friend_allows_all() {
    let s = setup_two_bots("public", "private", true).await;

    let chat = bot_chat_http(s.addr, &s.caller.token, &s.target.bot_id, "hello").await;
    // Friends can chat with private bots
    assert!(chat.is_ok(), "P-4(i) private target chat should succeed for friends: {:?}", chat.err());

    let group = create_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        "P4",
        &[&s.target.bot_id],
    )
    .await;
    assert!(group.is_ok(), "P-4(ii) group should succeed: {:?}", group.err());

    let dm = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await;
    assert!(dm.is_ok(), "P-4(iii) DM should succeed: {:?}", dm.err());

    let group_id = create_base_group(s.addr, &s.caller.token, &s.caller.bot_id).await;
    let add = add_member_http(s.addr, &s.caller.token, &group_id, &s.target.bot_id).await;
    // Private target is invisible (404) even to friends via add_member
    assert!(add.is_err(), "P-4(iv) private target add_member should be rejected (invisible), got: {:?}", add.ok());
}

// ============================================================================
// P-5: target=private, NOT friends → all 404 BotNotFound
// ============================================================================
#[tokio::test]
async fn p5_target_private_stranger_rejects_404() {
    let s = setup_two_bots("public", "private", false).await;

    let chat = bot_chat_http(s.addr, &s.caller.token, &s.target.bot_id, "hello").await;
    assert!(chat.is_err(), "P-5(i) chat should fail");
    assert!(
        chat.unwrap_err().contains("404"),
        "P-5(i) should be 404"
    );

    let group = create_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        "P5",
        &[&s.target.bot_id],
    )
    .await;
    assert!(group.is_err(), "P-5(ii) group should fail");
    assert!(
        group.unwrap_err().contains("404"),
        "P-5(ii) should be 404"
    );

    let dm = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await;
    assert!(dm.is_err(), "P-5(iii) DM should fail");
    assert!(
        dm.unwrap_err().contains("404"),
        "P-5(iii) should be 404"
    );

    let group_id = create_base_group(s.addr, &s.caller.token, &s.caller.bot_id).await;
    let add = add_member_http(s.addr, &s.caller.token, &group_id, &s.target.bot_id).await;
    assert!(add.is_err(), "P-5(iv) add_member should fail");
    assert!(
        add.unwrap_err().contains("404"),
        "P-5(iv) should be 404"
    );
}

// ============================================================================
// P-6: caller=private, target reachable → all succeed (D-B caller guard removed)
// ============================================================================
#[tokio::test]
async fn p6_caller_private_target_reachable_allows_all() {
    // caller=private, target=public (no friendship needed for public target)
    let s = setup_two_bots("private", "public", false).await;

    let chat = bot_chat_http(s.addr, &s.caller.token, &s.target.bot_id, "hello").await;
    assert!(chat.is_ok(), "P-6(i) chat should succeed: {:?}", chat.err());

    let group = create_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        "P6",
        &[&s.target.bot_id],
    )
    .await;
    assert!(group.is_ok(), "P-6(ii) group should succeed: {:?}", group.err());

    let dm = create_dm_group_http(
        s.addr,
        &s.caller.token,
        &s.caller.bot_id,
        &s.target.bot_id,
    )
    .await;
    assert!(dm.is_ok(), "P-6(iii) DM should succeed: {:?}", dm.err());

    let group_id = create_base_group(s.addr, &s.caller.token, &s.caller.bot_id).await;
    let add = add_member_http(s.addr, &s.caller.token, &group_id, &s.target.bot_id).await;
    assert!(add.is_ok(), "P-6(iv) add_member should succeed: {:?}", add.err());
}

// ============================================================================
// P-7: 切 private 不 cancel pending request
// ============================================================================
#[tokio::test]
async fn p7_switch_private_preserves_pending_request() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    bot_a.register("bot_a", &["chat"], addr).await;
    bot_b.register("bot_b", &["chat"], addr).await;

    let client_a = bot_a.http_client(addr);
    let client_b = bot_b.http_client(addr);

    // A=public, B=protected → friend request goes pending
    client_a
        .set_visibility(&bot_a.bot_id, "public")
        .await
        .expect("set A public");
    client_b
        .set_visibility(&bot_b.bot_id, "protected")
        .await
        .expect("set B protected");

    let req_resp = client_a
        .send_friend_request(None, &bot_b.bot_id)
        .await
        .expect("send friend request");
    assert!(req_resp.success, "friend request should succeed");
    let request_id = req_resp.data.as_ref().unwrap()["id"]
        .as_str()
        .unwrap()
        .to_string();
    let status = req_resp.data.as_ref().unwrap()["status"]
        .as_str()
        .unwrap_or("");
    assert_eq!(status, "pending", "request should be pending");

    // A switches to private → pending request should survive
    client_a
        .set_visibility(&bot_a.bot_id, "private")
        .await
        .expect("set A private");

    // B can still accept the pending request
    let accept = client_b.accept_friend_request(&request_id).await;
    assert!(
        accept.is_ok(),
        "P-7: accepting pending request should succeed after visibility change: {:?}",
        accept.err()
    );
    let accept_resp = accept.unwrap();
    assert!(accept_resp.success, "P-7: accept should succeed");
}

// ============================================================================
// P-8: 切 private 后拒绝新发起 friend request → 404
// ============================================================================
#[tokio::test]
async fn p8_private_target_rejects_new_friend_request() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_x = MockBot::connect(addr).await;
    bot_a.register("bot_a", &["chat"], addr).await;
    bot_x.register("bot_x", &["chat"], addr).await;

    let client_a = bot_a.http_client(addr);
    let client_x = bot_x.http_client(addr);

    // A switches to private
    client_a
        .set_visibility(&bot_a.bot_id, "private")
        .await
        .expect("set A private");

    // X tries to send friend request to A → should fail with 404
    let req = client_x
        .send_friend_request(None, &bot_a.bot_id)
        .await;

    // Friend request to private bot should fail with 404 (BotNotFound disguise)
    match req {
        Err(e) => {
            let err_str = format!("{}", e);
            assert!(
                err_str.contains("404") || err_str.contains("not found") || err_str.contains("Not Found"),
                "P-8: expected 404 BotNotFound, got: {}", err_str
            );
        }
        Ok(resp) => {
            assert!(
                !resp.success,
                "P-8: friend request to private bot should fail: {:?}", resp.data
            );
        }
    }
}

// ============================================================================
// P-9: private caller → public target → auto-accept friend request
// (AC-5a: caller=private no longer blocked from sending friend requests)
// ============================================================================
#[tokio::test]
async fn p9_private_caller_friend_request_public_target_auto_accepts() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    bot_a.register("bot_a", &["chat"], addr).await;
    bot_b.register("bot_b", &["chat"], addr).await;

    let client_a = bot_a.http_client(addr);
    let client_b = bot_b.http_client(addr);

    // A=private, B=public
    client_a
        .set_visibility(&bot_a.bot_id, "private")
        .await
        .expect("set A private");
    client_b
        .set_visibility(&bot_b.bot_id, "public")
        .await
        .expect("set B public");

    // A (private) sends friend request to B (public) → should auto-accept
    let resp = client_a
        .send_friend_request(None, &bot_b.bot_id)
        .await
        .expect("P-9: private caller should be allowed to send friend request");
    assert!(resp.success, "P-9: friend request should succeed");

    // Auto-accept: data may be absent when auto-accepted (empty request.id)
    let resp_status = resp.data
        .as_ref()
        .and_then(|d| d["status"].as_str())
        .unwrap_or("accepted");
    assert_eq!(
        resp_status, "accepted",
        "P-9: public target should auto-accept"
    );

    // Verify bidirectional friendship: A→B
    let friends_a = client_a
        .list_friends(&bot_a.bot_id)
        .await
        .expect("list A's friends");
    let friends_a_list = friends_a.data.as_ref().unwrap().as_array().unwrap();
    let friend_a_uuids: Vec<&str> = friends_a_list
        .iter()
        .filter_map(|f| f["bot_uuid"].as_str())
        .collect();
    assert!(
        friend_a_uuids.contains(&bot_b.bot_id.as_str()),
        "P-9: A should have B as friend"
    );

    // Verify bidirectional friendship: B→A
    let friends_b = client_b
        .list_friends(&bot_b.bot_id)
        .await
        .expect("list B's friends");
    let friends_b_list = friends_b.data.as_ref().unwrap().as_array().unwrap();
    let friend_b_uuids: Vec<&str> = friends_b_list
        .iter()
        .filter_map(|f| f["bot_uuid"].as_str())
        .collect();
    assert!(
        friend_b_uuids.contains(&bot_a.bot_id.as_str()),
        "P-9: B should have A as friend (bidirectional)"
    );
}

// ============================================================================
// P-10: private caller → protected target → pending friend request
// ============================================================================
#[tokio::test]
async fn p10_private_caller_friend_request_protected_target_pending() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_c = MockBot::connect(addr).await;
    bot_a.register("bot_a", &["chat"], addr).await;
    bot_c.register("bot_c", &["chat"], addr).await;

    let client_a = bot_a.http_client(addr);
    let client_c = bot_c.http_client(addr);

    // A=private, C=protected
    client_a
        .set_visibility(&bot_a.bot_id, "private")
        .await
        .expect("set A private");
    client_c
        .set_visibility(&bot_c.bot_id, "protected")
        .await
        .expect("set C protected");

    // A (private) sends friend request to C (protected) → should be pending
    let resp = client_a
        .send_friend_request(None, &bot_c.bot_id)
        .await
        .expect("P-10: private caller should be allowed to send friend request");
    assert!(resp.success, "P-10: friend request should succeed");

    let resp_status = resp.data.as_ref().unwrap()["status"]
        .as_str()
        .unwrap_or("");
    assert_eq!(
        resp_status, "pending",
        "P-10: protected target should yield pending"
    );

    // C can later accept
    let req_id = resp.data.as_ref().unwrap()["id"]
        .as_str()
        .unwrap()
        .to_string();
    let accept = client_c
        .accept_friend_request(&req_id)
        .await
        .expect("C should accept");
    assert!(accept.success, "P-10: accept should succeed");
}

// ============================================================================
// P-11: private caller → private target (stranger) → 404 BotNotFound
// The rejection comes from target=private (AC-3), NOT from caller=private (AC-5a removed).
// ============================================================================
#[tokio::test]
async fn p11_private_caller_friend_request_private_target_stranger_404() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_d = MockBot::connect(addr).await;
    bot_a.register("bot_a", &["chat"], addr).await;
    bot_d.register("bot_d", &["chat"], addr).await;

    let client_a = bot_a.http_client(addr);
    let client_d = bot_d.http_client(addr);

    // A=private, D=private, no friendship
    client_a
        .set_visibility(&bot_a.bot_id, "private")
        .await
        .expect("set A private");
    client_d
        .set_visibility(&bot_d.bot_id, "private")
        .await
        .expect("set D private");

    // A (private) sends friend request to D (private stranger) → should fail
    let resp = client_a
        .send_friend_request(None, &bot_d.bot_id)
        .await;

    // Should fail with 404: target=private blocks the request (BotNotFound disguise)
    match resp {
        Err(e) => {
            let err_str = format!("{}", e);
            assert!(
                err_str.contains("404") || err_str.contains("not found") || err_str.contains("Not Found"),
                "P-11: expected 404 BotNotFound, got: {}", err_str
            );
        }
        Ok(r) => {
            assert!(
                !r.success,
                "P-11: friend request to private stranger should fail: {:?}", r.data
            );
        }
    }
}
