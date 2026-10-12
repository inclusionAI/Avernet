//! Task 20 E2E — the bot authority user story, executed sequentially
//! against the REAL production assembly (`BcsServer` memory lane: HTTP
//! routers, WS registries, the same v1 OpenAPI surface the gateway calls).
//!
//! Formal credentials only (spec §12.1): Human actions ride the
//! Gateway-Principal JWT (`x-avernet-principal`) exactly as the deployed
//! gateway issues them; the trusted team-manager platform calls carry the
//! dedicated HS256 team-manager service credential. The mock-user headers
//! appear in exactly two legitimate places: the trusted registration lane
//! (`/bots/onboard` binds `created_by`) and Human materialization
//! (`/me/ensure-human`) — never as an authorization shortcut.
//!
//! Story (plan Task 20 Step 1, file 1 of 2):
//!   1. Human A registers bot X (registration initializes the owner edge);
//!   2. A grants B manager on X; B grants A manager on Y (cross owners);
//!   3. mine labels: A sees X=owner / Y=manager, B sees X=manager / Y=owner,
//!      legacy `/bots/my` carries the same labels (Task 9 parity);
//!   4. Without any friend edge, Human A sponsors a PRIVATE group over the
//!      owned (X) + managed (Y) protected bots (spec §8.3) — and the
//!      runtime friend edges stay identical;
//!   5. session + session-file flows run under the owner AND the manager
//!      personas over the same session (spec §8.2 parity);
//!   6. the trusted team platform syncs team manager sources onto X:
//!      snapshot sync grants C a `team/*` manager edge, the move to another
//!      team revokes it, the single-member POST/DELETE repairs add/remove
//!      it again (spec §6.1, AC27/AC28/AC29 lanes);
//!   7. a Workbench WS connection bound to an explicit view receives
//!      protected chat frames through the assembled enqueue/dequeue
//!      delivery-authorization service.
//!
//! A second human's manager view is exercised throughout: the story
//! asserts the MANAGER plane reaches the same resources through the live
//! authority edges (`can_manage`), never through `created_by`.
//!
//! Run with:
//! ```bash
//! cargo test --manifest-path apps/bcs/Cargo.toml -p bcs \
//!   --test e2e_bot_authority -- --test-threads=1
//! ```

mod helpers;

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use helpers::{MockBot, create_temp_bots_dir, create_test_config, onboard_bot_as_user};
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::{Value, json};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

use bcs::{BcsConfig, BcsServer};
use bcs_config_api::TeamManagerSyncConfig;

const GATEWAY_PRINCIPAL_SIGNING_KEY: &[u8] = b"test-only-gateway-principal-signing-key";
const TEAM_SYNC_SIGNING_KEY: &[u8] = b"e2e-test-team-sync-signing-key-material";
const TEAM_SYNC_KEY_ENV: &str = "E2E_BOT_AUTHORITY_TEAM_SYNC_SIGNING_KEY";

/// The binding env of the authority lane in THIS process — the same
/// priority chain `bcs_config::resolve_env_str` uses. The team-manager
/// credential's `env` claim must match the assembled verifier's env.
fn e2e_binding_env() -> String {
    let raw = std::env::var("SERVER_ENV")
        .or_else(|_| std::env::var("REAL_SERVER_ENV"))
        .or_else(|_| std::env::var("ALIPAY_APP_ENV"))
        .unwrap_or_default()
        .to_lowercase();
    match raw.as_str() {
        "prod" => "prod".to_string(),
        "gray" => "gray".to_string(),
        "pre" | "prepub" => "pre".to_string(),
        "local" => "local".to_string(),
        _ => "dev".to_string(),
    }
}

// ============================================================================
// Formal credentials
// ============================================================================

fn principal_token(user_id: &str) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("current time is after the Unix epoch")
        .as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    encode(
        &header,
        &json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now.saturating_sub(1),
            "exp": now + 300,
            "principals": [{
                "type": "user",
                "tenant": "tenant-a",
                "subject": {
                    "id": user_id,
                    "username": user_id,
                    "tenant_id": "tenant-a"
                }
            }]
        }),
        &EncodingKey::from_secret(GATEWAY_PRINCIPAL_SIGNING_KEY),
    )
    .expect("test principal token signs")
}

/// One mixed User+Bot principal: the formal credential shape a Human takes
/// when acting for one of their owned-or-managed bots. The signed
/// `owner_id` claim is deliberately NOT trusted by BCS (spec §12.1(3)) —
/// the application re-proves the live authority edge for every call.
fn mixed_principal_token(user_id: &str, bot_uuid: &str) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("current time is after the Unix epoch")
        .as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    encode(
        &header,
        &json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now.saturating_sub(1),
            "exp": now + 300,
            "principals": [
                {
                    "type": "user",
                    "tenant": "tenant-a",
                    "subject": {
                        "id": user_id,
                        "username": user_id,
                        "tenant_id": "tenant-a"
                    }
                },
                {
                    "type": "bot",
                    "tenant": "tenant-a",
                    "bot": {
                        "bot_uuid": bot_uuid,
                        "owner_id": user_id,
                        "app_id": 1,
                        "agent_code": "e2e-team-story",
                        "tenant": "tenant-a"
                    }
                }
            ]
        }),
        &EncodingKey::from_secret(GATEWAY_PRINCIPAL_SIGNING_KEY),
    )
    .expect("test mixed principal token signs")
}

/// The trusted team-manager service credential (spec §6.1 / Task 13):
/// HS256, dedicated `team_manager_sync` purpose, service identity + env
/// claims; the raw secret never enters any command or audit row.
fn team_manager_credential() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("current time is after the Unix epoch")
        .as_secs();
    let header = jsonwebtoken::Header::new(Algorithm::HS256);
    encode(
        &header,
        &json!({
            "iss": "bcn",
            "aud": "bcn-team-manager-sync",
            "purpose": "team_manager_sync",
            "sub": "e2e-team-platform",
            "env": e2e_binding_env(),
            "iat": now.saturating_sub(1),
            "exp": now + 600,
        }),
        &EncodingKey::from_secret(TEAM_SYNC_SIGNING_KEY),
    )
    .expect("test team-manager credential signs")
}

// ============================================================================
// Fixture: the real production assembly
// ============================================================================

async fn start_authority_story_server(
    data_dir: &std::path::Path,
) -> (
    std::net::SocketAddr,
    tokio::task::JoinHandle<Result<(), bcs::BcsError>>,
    Arc<bcs::server::BcsServerState>,
) {
    // SAFETY: identical to the shared helper's mock-lane init, applied
    // before any server in this test binary starts; both test binaries
    // set their own uniquely-named team-key variable to avoid collisions.
    unsafe {
        std::env::set_var("BCS_AUTH_MOCK", "1");
        std::env::set_var("BCS_MOCK_USER_ID", "");
        std::env::set_var(TEAM_SYNC_KEY_ENV, String::from_utf8_lossy(TEAM_SYNC_SIGNING_KEY).into_owned());
    }
    let mut config: BcsConfig = create_test_config(&data_dir.to_path_buf());
    config.metrics.enabled = false;
    config.team_manager_sync = TeamManagerSyncConfig {
        enabled: true,
        signing_key_env: TEAM_SYNC_KEY_ENV.to_string(),
        signing_key_secret: None,
    };
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    server
        .run_on_random_port_with_state()
        .await
        .expect("story server starts with the team-manager lane armed")
}

/// One trusted Human row (materialization only; every authorization fact
/// in this story comes from live authority edges, not from this lane).
async fn ensure_human(addr: std::net::SocketAddr, staff_no: &str) {
    let response = reqwest::Client::new()
        .post(format!("http://{addr}/me/ensure-human"))
        .header("X-Mock-User-Id", staff_no)
        .header("X-Mock-Nick-Name", staff_no)
        .send()
        .await
        .expect("ensure-human request");
    assert!(
        response.status().is_success(),
        "ensure-human for {staff_no}: {} {}",
        response.status(),
        response.text().await.unwrap_or_default()
    );
}

async fn get_json(url: &str, principal: Option<&str>) -> (reqwest::StatusCode, Value) {
    let mut request = reqwest::Client::new().get(url);
    if let Some(token) = principal {
        request = request.header("x-avernet-principal", token);
    }
    let response = request.timeout(Duration::from_secs(10)).send().await.expect("GET");
    let status = response.status();
    let body = response.json().await.unwrap_or(Value::Null);
    (status, body)
}

async fn request_json(
    method: reqwest::Method,
    url: &str,
    principal: Option<&str>,
    body: Option<&Value>,
) -> (reqwest::StatusCode, Value) {
    let client = reqwest::Client::new();
    let mut request = client.request(method, url).timeout(Duration::from_secs(10));
    if let Some(token) = principal {
        request = request.header("x-avernet-principal", token);
    }
    if let Some(payload) = body {
        request = request.json(payload);
    }
    let response = request.send().await.expect("request");
    let status = response.status();
    let parsed = response.json().await.unwrap_or(Value::Null);
    (status, parsed)
}

/// One request against the credential-gated team-manager slice: the raw
/// credential rides the `Authorization: Bearer` header (the slice is
/// mounted OUTSIDE the principal middleware, spec §6.1).
async fn team_slice_request(
    method: reqwest::Method,
    url: &str,
    credential: &str,
    body: Option<&Value>,
) -> (reqwest::StatusCode, Value) {
    let client = reqwest::Client::new();
    let mut request = client
        .request(method, url)
        .timeout(Duration::from_secs(10))
        .header("Authorization", format!("Bearer {credential}"));
    if let Some(payload) = body {
        request = request.json(payload);
    }
    let response = request.send().await.expect("team slice request");
    let status = response.status();
    let parsed = response.json().await.unwrap_or(Value::Null);
    (status, parsed)
}

/// The `access_relation` of one bot inside a v1 `mine` items array.
fn mine_relation(items: &Value, bot_id: &str) -> Option<String> {
    items
        .as_array()?
        .iter()
        .find(|item| item["bot_id"] == json!(bot_id))
        .and_then(|item| item["access_relation"].as_str().map(str::to_string))
}

/// The friend-edge snapshot a story compares before/after (spec §8.3.2:
/// sponsorship must never create a Bot↔Bot friend edge).
async fn friend_edge_snapshot(addr: std::net::SocketAddr, bot_id: &str, token: &str) -> Value {
    let response = reqwest::Client::new()
        .get(format!("http://{addr}/bots/{bot_id}/friends"))
        .header("Authorization", format!("Bearer {token}"))
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .expect("friend list request");
    assert!(
        response.status().is_success(),
        "friend list for {bot_id}: {}",
        response.status()
    );
    let body: Value = response.json().await.expect("friend list JSON");
    body["data"].clone()
}

// ============================================================================
// Workbench WS client (the protected-delivery lane, Tasks 15/16)
// ============================================================================

struct ViewWorkbenchClient {
    write: futures_util::stream::SplitSink<
        tokio_tungstenite::WebSocketStream<
            tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
        >,
        Message,
    >,
    read: futures_util::stream::SplitStream<
        tokio_tungstenite::WebSocketStream<
            tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
        >,
    >,
    counter: u32,
}

impl ViewWorkbenchClient {
    async fn connect(addr: std::net::SocketAddr, staff_no: &str) -> Self {
        let url = format!("ws://{addr}/ws");
        let mut request = url.into_client_request().expect("workbench ws request");
        request
            .headers_mut()
            .insert("X-Mock-User-Id", staff_no.parse().expect("mock user header"));
        let (ws, _) = tokio_tungstenite::connect_async(request)
            .await
            .expect("workbench websocket connect");
        let (write, read) = ws.split();
        Self { write, read, counter: 0 }
    }

    async fn send(&mut self, frame: Value) {
        self.write
            .send(Message::Text(frame.to_string().into()))
            .await
            .expect("workbench frame send");
    }

    async fn subscribe_with_view(&mut self, group_id: &str, view_actor_id: &str) -> Value {
        let id = format!("wb_connect_{}", self.counter);
        self.counter += 1;
        self.send(json!({
            "type": "req",
            "id": id,
            "method": "connect",
            "params": { "group_id": group_id, "viewActorId": view_actor_id }
        }))
        .await;
        self.wait_for(|frame| frame["type"] == "res" && frame["id"] == id)
            .await
            .expect("subscribe response")
    }

    async fn send_chat(&mut self, group_id: &str, actor_id: &str, text: &str) -> Value {
        let id = format!("wb_chat_{}", self.counter);
        self.counter += 1;
        self.send(json!({
            "type": "req",
            "id": id,
            "method": "chat.send",
            "params": {
                "group_id": group_id,
                "bot_id": actor_id,
                "bot_name": actor_id,
                "message": text,
                "mentions": []
            }
        }))
        .await;
        self.wait_for(|frame| frame["type"] == "res" && frame["id"] == id)
            .await
            .expect("chat.send response")
    }

    async fn wait_for(
        &mut self,
        mut predicate: impl FnMut(&Value) -> bool,
    ) -> Option<Value> {
        let mut seen: Vec<Value> = Vec::new();
        let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                eprintln!("workbench frames observed while waiting: {seen:?}");
                return None;
            }
            match tokio::time::timeout(remaining, self.read.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    let frame: Value =
                        serde_json::from_str(text.as_str()).expect("workbench frame json");
                    if predicate(&frame) {
                        return Some(frame);
                    }
                    seen.push(frame);
                }
                Ok(Some(Ok(Message::Ping(data)))) => {
                    let _ = self.write.send(Message::Pong(data)).await;
                }
                _ => {
                    eprintln!("workbench frames observed before the socket ended: {seen:?}");
                    return None;
                }
            }
        }
    }
}

// ============================================================================
// The story
// ============================================================================

#[tokio::test]
async fn full_bot_authority_story() {
    let dir = create_temp_bots_dir();
    let (addr, _handle, _state) =
        start_authority_story_server(dir.path()).await;

    // -- 1. Registration: A owns X, B owns Y (the trusted registration lane
    //       initializes the unique owner edge + ownership_version = 1). --
    let staff_a = "e2eautha";
    let staff_b = "e2eauthb";
    ensure_human(addr, staff_b).await;

    let bot_x = MockBot::connect(addr).await;
    let bot_y = MockBot::connect(addr).await;
    onboard_bot_as_user(addr, &bot_x.token, "AuthorityX", staff_a).await;
    onboard_bot_as_user(addr, &bot_y.token, "AuthorityY", staff_b).await;
    let bot_x_id = bot_x.bot_id.clone();
    let bot_y_id = bot_y.bot_id.clone();

    // -- 2. Cross-manager grants through the MOUNTED v1 manager route. --
    let (grant_status, grant_body) = request_json(
        reqwest::Method::PUT,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/managers/{staff_b}"),
        Some(&principal_token(staff_a)),
        Some(&json!({})),
    )
    .await;
    assert!(
        grant_status.is_success(),
        "owner A grants B manager on X: {grant_status} {grant_body}"
    );

    let (grant_status, grant_body) = request_json(
        reqwest::Method::PUT,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_y_id}/managers/{staff_a}"),
        Some(&principal_token(staff_b)),
        Some(&json!({})),
    )
    .await;
    assert!(
        grant_status.is_success(),
        "owner B grants A manager on Y: {grant_status} {grant_body}"
    );

    // -- 3. mine labels (the live authority facts, computed per
    //       authenticated user; spec §7): every item serializes a
    //       non-empty access_relation. --
    let (status, a_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_a)),
    )
    .await;
    assert!(status.is_success(), "A mine: {status} {a_mine}");
    let a_mine_items = a_mine["data"]["items"].clone();
    let (status, b_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert!(status.is_success(), "B mine: {status} {b_mine}");
    let b_mine_items = b_mine["data"]["items"].clone();

    assert_eq!(
        mine_relation(&a_mine_items, &bot_x_id).as_deref(),
        Some("owner"),
        "A owns X"
    );
    assert_eq!(
        mine_relation(&a_mine_items, &bot_y_id).as_deref(),
        Some("manager"),
        "A manages Y (a bot created by somebody else)"
    );
    assert_eq!(
        mine_relation(&b_mine_items, &bot_x_id).as_deref(),
        Some("manager"),
        "B manages X"
    );
    assert_eq!(
        mine_relation(&b_mine_items, &bot_y_id).as_deref(),
        Some("owner"),
        "B owns Y"
    );
    for items in [&a_mine_items, &b_mine_items] {
        for item in items.as_array().expect("mine items array") {
            let relation = item["access_relation"].as_str().unwrap_or("");
            assert!(
                relation == "owner" || relation == "manager",
                "every mine item carries a non-empty access_relation: {item}"
            );
        }
    }

    // The legacy entrypoint reports the same labels (Task 9 parity).
    let legacy = helpers::query_my_bots(addr, staff_a).await;
    let legacy_relations: Vec<String> = legacy["items"]
        .as_array()
        .expect("legacy mine items")
        .iter()
        .filter(|item| item["bot_uuid"] == json!(bot_y_id))
        .map(|item| item["access_relation"].as_str().unwrap_or("").to_string())
        .collect();
    assert_eq!(
        legacy_relations.first().map(String::as_str),
        Some("manager"),
        "legacy /bots/my labels A's managed bot Y as manager: {legacy}"
    );

    // The manager list answers through the same authoritative facts.
    let (status, managers_page) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/managers"),
        Some(&principal_token(staff_a)),
    )
    .await;
    assert!(status.is_success(), "managers list: {status} {managers_page}");
    assert_eq!(
        managers_page["data"]["owner_user_id"].as_str(),
        Some(staff_a),
        "the manager page projects the single current owner"
    );
    let manager_ids: Vec<&str> = managers_page["data"]["items"]
        .as_array()
        .expect("manager items")
        .iter()
        .map(|item| item["user_id"].as_str().unwrap_or(""))
        .collect();
    assert!(
        manager_ids.contains(&staff_b),
        "B appears in X's manager list: {managers_page}"
    );

    // -- 4. Human-sponsored PRIVATE group over owned + managed protected
    //       bots, with NO friend edge (spec §8.3 / AC22). --
    let runtime_friend_edges_before = json!([
        friend_edge_snapshot(addr, &bot_x_id, &bot_x.token).await,
        friend_edge_snapshot(addr, &bot_y_id, &bot_y.token).await,
    ]);

    let (group_status, group_body) = request_json(
        reqwest::Method::POST,
        &format!("http://{addr}/openapi/v1/collaboration/groups"),
        Some(&principal_token(staff_a)),
        Some(&json!({
            "group_kind": "normal",
            "name": "Authority sponsorship story",
            "driver_bot_uuid": bot_x_id,
            "participants": [ { "actor_id": bot_y_id, "role": "consultant" } ],
            "collaboration": { "strategy": "chat" },
            "originator": format!("human_{staff_a}")
        })),
    )
    .await;
    assert!(
        group_status.is_success(),
        "Human A sponsors private Group(X, Y): {group_status} {group_body}"
    );
    let group_id = group_body["data"]["group_id"]
        .as_str()
        .expect("created group id")
        .to_string();

    // The manager persona sees the same resource through ITS bounded view
    // (spec §8.1/AC04): B, acting with view X, reads the group.
    let (status, b_groups) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/groups?view_bot_id={bot_x_id}"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert!(status.is_success(), "B reads groups with view X: {status}");
    let b_sees_the_group = b_groups["data"]["items"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .any(|item| item["group_id"] == json!(group_id) || item["id"] == json!(group_id))
        })
        .unwrap_or(false);
    assert!(
        b_sees_the_group,
        "manager B (view X) must see the sponsored private group: {b_groups}"
    );

    // -- 5. Session + session-file flows under the owner and manager
    //       personas (spec §8.2: manager acts with the owner's rights on
    //       the resource, subject to the original participation rules). --
    let (session_status, session_body) = request_json(
        reqwest::Method::POST,
        &format!("http://{addr}/openapi/v1/collaboration/groups/{group_id}/sessions"),
        Some(&principal_token(staff_a)),
        Some(&json!({
            "acting_bot_id": bot_x_id,
            "title": "authority story session"
        })),
    )
    .await;
    assert!(
        session_status.is_success(),
        "A launches a session acting as owned X: {session_status} {session_body}"
    );
    let session_id = session_body["data"]["id"]
        .as_str()
        .or_else(|| session_body["data"]["session"]["id"].as_str())
        .or_else(|| session_body["data"]["session_id"].as_str())
        .expect("session id")
        .to_string();

    // 5a. The owner persona (A acting as X) prepares, uploads, completes.
    let owner_credential = mixed_principal_token(staff_a, &bot_x_id);
    let upload_payload = b"authority story corpus".to_vec();
    let upload_size = upload_payload.len() as u64;
    let (prepare_status, prepare_body) = request_json(
        reqwest::Method::POST,
        &format!("http://{addr}/api/v1/collaboration/sessions/{session_id}/files"),
        Some(&owner_credential),
        Some(&json!({
            "file_name": "authority-owner.txt",
            "size": upload_size,
            "mime_type": "text/plain"
        })),
    )
    .await;
    assert!(
        prepare_status.is_success(),
        "owner A prepares a session file acting as X: {prepare_status} {prepare_body}"
    );
    let file_id = prepare_body["data"]["file_id"]
        .as_str()
        .expect("prepared file id")
        .to_string();

    // 5b. The MANAGER persona (B, managing X) uploads and completes the
    //     very same file — the ownership predicate extends to the live
    //     manager edge (spec §8.2 file lane).
    let manager_credential = mixed_principal_token(staff_b, &bot_x_id);
    let upload_response = reqwest::Client::new()
        .put(format!(
            "http://{addr}/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"
        ))
        .header("x-avernet-principal", &manager_credential)
        .header("Content-Type", "application/octet-stream")
        .body(upload_payload.clone())
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .expect("manager upload request");
    assert!(
        upload_response.status().is_success(),
        "manager B uploads X's file: {} {:?}",
        upload_response.status(),
        upload_response.text().await
    );

    let (complete_status, complete_body) = request_json(
        reqwest::Method::POST,
        &format!(
            "http://{addr}/api/v1/collaboration/sessions/{session_id}/files/{file_id}/complete"
        ),
        Some(&owner_credential),
        Some(&json!({})),
    )
    .await;
    assert!(
        complete_status.is_success(),
        "owner A completes the file: {complete_status} {complete_body}"
    );

    let (list_status, list_body) = request_json(
        reqwest::Method::GET,
        &format!("http://{addr}/api/v1/collaboration/sessions/{session_id}/files"),
        Some(&owner_credential),
        None,
    )
    .await;
    assert!(list_status.is_success(), "owner lists files: {list_status}");
    let owner_sees_file = list_body["data"]["items"]
        .as_array()
        .or_else(|| list_body["data"].as_array())
        .map(|items| items.iter().any(|item| item["file_id"] == json!(file_id)))
        .unwrap_or_else(|| list_body["data"].to_string().contains(&file_id));
    assert!(
        owner_sees_file,
        "the completed file is in the session file list: {list_body}"
    );

    let download_response = reqwest::Client::new()
        .get(format!(
            "http://{addr}/api/v1/collaboration/sessions/{session_id}/files/{file_id}/content"
        ))
        .header("x-avernet-principal", &owner_credential)
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .expect("owner download request");
    assert!(download_response.status().is_success());
    let downloaded = download_response.bytes().await.expect("downloaded bytes");
    assert_eq!(
        downloaded.to_vec(),
        upload_payload,
        "downloaded content must match the uploaded bytes"
    );

    // -- 6. The trusted team platform governs team-sourced manager edges
    //       (spec §6.1 / §5.4): snapshot sync, the move to another team,
    //       then both single-member repairs — every step through the
    //       credential-gated slice with a real verified service. --
    let staff_c = "e2eauthc";
    ensure_human(addr, staff_c).await;
    let credential = team_manager_credential();
    let team_url = |team: &str| {
        format!("http://{addr}/api/v1/bots/{bot_x_id}/manager-sources/teams/{team}")
    };

    // 6a. Normal snapshot sync: team-one's manager snapshot is [C].
    let (sync_status, sync_body) = team_slice_request(
        reqwest::Method::PUT,
        &team_url("team-one"),
        &credential,
        Some(&json!({
            "operation": "sync",
            "manager_user_ids": [ staff_c ],
            "idempotency_key": "e2e-sync-team-one-1"
        })),
    )
    .await;
    assert!(
        sync_status.is_success(),
        "team sync grants C a team-sourced manager edge: {sync_status} {sync_body}"
    );
    let (status, c_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_c)),
    )
    .await;
    assert!(status.is_success(), "C mine: {status}");
    assert_eq!(
        mine_relation(&c_mine.clone()["data"]["items"], &bot_x_id).as_deref(),
        Some("manager"),
        "the team snapshot alone makes C a manager of X: {c_mine}"
    );

    // 6b. team move: X moves team-one -> team-two carrying an EMPTY
    //     snapshot for the new team; C is not in the new snapshot, so the
    //     stopped team's edge is revoked (AC27).
    let (move_status, move_body) = team_slice_request(
        reqwest::Method::PUT,
        &team_url("team-one"),
        &credential,
        Some(&json!({
            "operation": "move",
            "new_team_id": "team-two",
            "manager_user_ids": [],
            "idempotency_key": "e2e-move-team-two-1"
        })),
    )
    .await;
    assert!(
        move_status.is_success(),
        "team move with an empty new snapshot: {move_status} {move_body}"
    );
    let (status, c_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_c)),
    )
    .await;
    assert!(status.is_success(), "C mine after move: {status}");
    assert_eq!(
        mine_relation(&c_mine.clone()["data"]["items"], &bot_x_id),
        None,
        "C lost the manager edge when the moved team's snapshot dropped them: {c_mine}"
    );

    // 6c. Internal single-member repair POST: C joins team-two's snapshot.
    let (repair_status, repair_body) = team_slice_request(
        reqwest::Method::POST,
        &format!("{}/members", team_url("team-two")),
        &credential,
        Some(&json!({
            "user_id": staff_c,
            "idempotency_key": "e2e-repair-add-c-1"
        })),
    )
    .await;
    assert!(
        repair_status.is_success(),
        "member repair adds C back: {repair_status} {repair_body}"
    );
    let (status, c_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_c)),
    )
    .await;
    assert_eq!(
        mine_relation(&c_mine.clone()["data"]["items"], &bot_x_id).as_deref(),
        Some("manager"),
        "the add repair restores C's team-sourced manager edge: {c_mine}"
    );

    // 6d. Internal single-member repair DELETE removes exactly C again,
    //     without touching any other source.
    let delete_repair_response = reqwest::Client::new()
        .delete(format!(
            "{}/members?user_id={staff_c}",
            team_url("team-two")
        ))
        .header("Authorization", format!("Bearer {credential}"))
        .header("Idempotency-Key", "e2e-repair-remove-c-1")
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .expect("member repair delete request");
    assert!(
        delete_repair_response.status().is_success(),
        "member repair removes C: {}",
        delete_repair_response.status()
    );
    let (status, c_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_c)),
    )
    .await;
    assert_eq!(
        mine_relation(&c_mine.clone()["data"]["items"], &bot_x_id),
        None,
        "the remove repair drops C's team-sourced edge: {c_mine}"
    );
    // The direct manager edges survived every team operation.
    let (status, b_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert_eq!(
        mine_relation(&b_mine.clone()["data"]["items"], &bot_x_id).as_deref(),
        Some("manager"),
        "B's DIRECT manager edge is untouched by the team lane: {b_mine}"
    );

    // -- 7. Protected Workbench delivery: A (owner) subscribes with view X,
    //       B (manager) subscribes with view X; B's chat frame reaches A's
    //       connection only through the assembled enqueue/dequeue
    //       delivery-authorization service. --
    let mut connection_a = ViewWorkbenchClient::connect(addr, staff_a).await;
    let subscribed_a =
        connection_a.subscribe_with_view(&group_id, &bot_x_id).await;
    assert!(
        subscribed_a["ok"].as_bool().unwrap_or(false),
        "owner connection binds view X: {subscribed_a}"
    );
    let mut connection_b = ViewWorkbenchClient::connect(addr, staff_b).await;
    let subscribed_b =
        connection_b.subscribe_with_view(&group_id, &bot_x_id).await;
    assert!(
        subscribed_b["ok"].as_bool().unwrap_or(false),
        "manager connection binds view X: {subscribed_b}"
    );

    let chat_ok = connection_b.send_chat(&group_id, &bot_x_id, "manager plane message").await;
    assert!(
        chat_ok["ok"].as_bool().unwrap_or(false),
        "manager chat.send accepted: {chat_ok}"
    );
    let manager_frame_delivered_to_owner = connection_a
        .wait_for(|frame| {
            frame["type"] == "event"
                && frame["event"] == "chat"
                && frame["payload"]["state"] == json!("final")
                && frame["payload"]["message"]["role"] == json!("user")
        })
        .await
        .is_some();
    assert!(
        manager_frame_delivered_to_owner,
        "the protected frame crosses users through the assembled authorization service"
    );

    // -- The sponsorship invariant: the whole story (grants, group
    //    sponsorship, session/file traffic, team sync, WS delivery) must
    //    not have created a single Bot-Bot friend edge. --
    let runtime_friend_edges_after = json!([
        friend_edge_snapshot(addr, &bot_x_id, &bot_x.token).await,
        friend_edge_snapshot(addr, &bot_y_id, &bot_y.token).await,
    ]);
    assert_eq!(
        runtime_friend_edges_after, runtime_friend_edges_before,
        "authority operations must not fabricate friend edges"
    );
}