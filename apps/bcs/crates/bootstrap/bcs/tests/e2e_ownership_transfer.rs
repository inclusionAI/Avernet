//! Task 20 E2E — the confirmed ownership-transfer story, executed
//! sequentially against the REAL production assembly (memory lane,
//! mounted v1 routers + the WS protected-delivery writer).
//!
//! Formal Human credential: the Gateway-Principal JWT (`x-avernet-principal`),
//! exactly as the deployed gateway issues it. The mock-user headers appear
//! only in the trusted registration lane (`created_by` binding) — after
//! the transfer they may NOT resurrect any authority (spec §12.2): the
//! RED variable `former_owner_after_last_source_revoke_status == 403`
//! proves the old creator is denied even though `created_by` still
//! matches, and a re-onboard cannot claim ownership back.
//!
//! Story (plan Task 20 Step 1, file 2 of 2):
//!   1. A registers X (owner edge, version 1); A grants B manager on X;
//!      mine labels pin the pre-transfer facts;
//!   2. A initiates the transfer A -> B with the current expected version;
//!      B accepts through the mounted decision route;
//!   3. `a_mine_after_transfer["access_relation"] == "manager"` and
//!      `b_mine_after_transfer["access_relation"] == "owner"` (the
//!      accepted transfer flips the roles; spec §7.3/OT03);
//!   4. `pending_rows_for_bot == 0` — both parties' pending-filtered
//!      inboxes are empty and the persistent receipt is terminal;
//!   5. `active_owner_rows_for_bot == 1` — the strict ownership read
//!      (which structurally FAILS on zero or multiple approved owner
//!      edges) answers with exactly B as the owner and a bumped version;
//!   6. A's protected Workbench frames keep flowing while A is a
//!      manager, then the WS dequeue re-authorization stops them after B
//!      revokes A's last non-team manager source (spec §14/OT19);
//!   7. `former_owner_after_last_source_revoke_status == 403` on the
//!      control-plane PATCH lane; the created_by claim, Bot-ID and the
//!      legacy onboarding all fail to restore any authority;
//!   8. `runtime_friend_edges_after == runtime_friend_edges_before`.
//!
//! Run with:
//! ```bash
//! cargo test --manifest-path apps/bcs/Cargo.toml -p bcs \
//!   --test e2e_ownership_transfer -- --test-threads=1
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

const GATEWAY_PRINCIPAL_SIGNING_KEY: &[u8] = b"test-only-gateway-principal-signing-key";

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

async fn start_transfer_story_server(
    data_dir: &std::path::Path,
) -> (
    std::net::SocketAddr,
    tokio::task::JoinHandle<Result<(), bcs::BcsError>>,
    Arc<bcs::server::BcsServerState>,
) {
    // SAFETY: applied before any server in this test binary starts.
    unsafe {
        std::env::set_var("BCS_AUTH_MOCK", "1");
        std::env::set_var("BCS_MOCK_USER_ID", "");
    }
    let mut config: BcsConfig = create_test_config(&data_dir.to_path_buf());
    config.metrics.enabled = false;
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    server
        .run_on_random_port_with_state()
        .await
        .expect("transfer story server starts")
}

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

fn mine_relation(items: &Value, bot_id: &str) -> Option<String> {
    items
        .as_array()?
        .iter()
        .find(|item| item["bot_id"] == json!(bot_id))
        .and_then(|item| item["access_relation"].as_str().map(str::to_string))
}

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
// Workbench WS client (protected enqueue/dequeue delivery, Tasks 15/16)
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
async fn full_ownership_transfer_story() {
    let dir = create_temp_bots_dir();
    let (addr, _handle, _state) = start_transfer_story_server(dir.path()).await;

    // -- 1. Registration + manager grant: A owns X, B manages X. --
    let staff_a = "e2etransfera";
    let staff_b = "e2etransferb";
    ensure_human(addr, staff_b).await;
    ensure_human(addr, staff_a).await;

    let bot_x = MockBot::connect(addr).await;
    onboard_bot_as_user(addr, &bot_x.token, "TransferBotX", staff_a).await;
    let bot_x_id = bot_x.bot_id.clone();

    let (grant_status, grant_body) = request_json(
        reqwest::Method::PUT,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/managers/{staff_b}"),
        Some(&principal_token(staff_a)),
        Some(&json!({})),
    )
    .await;
    assert!(
        grant_status.is_success(),
        "A grants B manager on X: {grant_status} {grant_body}"
    );

    let (status, a_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_a)),
    )
    .await;
    assert!(status.is_success());
    assert_eq!(
        mine_relation(&a_mine.clone()["data"]["items"], &bot_x_id).as_deref(),
        Some("owner"),
        "pre-transfer: A owns X: {a_mine}"
    );
    let (status, b_mine) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert!(status.is_success());
    assert_eq!(
        mine_relation(&b_mine.clone()["data"]["items"], &bot_x_id).as_deref(),
        Some("manager"),
        "pre-transfer: B manages X: {b_mine}"
    );

    let runtime_friend_edges_before = friend_edge_snapshot(addr, &bot_x_id, &bot_x.token).await;

    // A group + Workbench binding established BEFORE the transfer, on A's
    // owner authority (the protected-delivery lanes observe the flip and
    // the final revoke below).
    let (group_status, group_body) = request_json(
        reqwest::Method::POST,
        &format!("http://{addr}/openapi/v1/collaboration/groups"),
        Some(&principal_token(staff_a)),
        Some(&json!({
            "group_kind": "normal",
            "name": "Transfer protected delivery group",
            "driver_bot_uuid": bot_x_id,
            "participants": [ { "actor_id": bot_x_id, "role": "driver" } ],
            "collaboration": { "strategy": "chat" },
            "originator": format!("human_{staff_a}")
        })),
    )
    .await;
    assert!(
        group_status.is_success(),
        "A sponsors the story group over owned X: {group_status} {group_body}"
    );
    let group_id = group_body["data"]["group_id"]
        .as_str()
        .expect("group id")
        .to_string();

    let mut former_owner_connection = ViewWorkbenchClient::connect(addr, staff_a).await;
    let subscribed = former_owner_connection.subscribe_with_view(&group_id, &bot_x_id).await;
    assert!(
        subscribed["ok"].as_bool().unwrap_or(false),
        "A binds view X while owner: {subscribed}"
    );
    let mut new_owner_connection = ViewWorkbenchClient::connect(addr, staff_b).await;
    let subscribed = new_owner_connection.subscribe_with_view(&group_id, &bot_x_id).await;
    assert!(
        subscribed["ok"].as_bool().unwrap_or(false),
        "B binds view X while manager: {subscribed}"
    );

    // -- 2. The confirmed transfer A -> B. --
    let (ownership_status, ownership_body) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/ownership"),
        Some(&principal_token(staff_a)),
    )
    .await;
    assert!(
        ownership_status.is_success(),
        "A reads the ownership snapshot: {ownership_status} {ownership_body}"
    );
    let ownership_version = ownership_body["data"]["ownership_version"]
        .as_u64()
        .expect("ownership version");

    let (create_status, create_body) = request_json(
        reqwest::Method::POST,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/ownership-transfers"),
        Some(&principal_token(staff_a)),
        Some(&json!({
            "to_user_id": staff_b,
            "expected_owner_version": ownership_version,
            "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1"
        })),
    )
    .await;
    assert!(
        create_status == reqwest::StatusCode::CREATED,
        "A initiates the transfer to B: {create_status} {create_body}"
    );
    let transfer_id = create_body["data"]["transfer_id"]
        .as_str()
        .expect("transfer id")
        .to_string();

    // Only the designated recipient may accept (spec §11.2/OT10): the
    // initiator's accept is a plain 403, never a role change.
    let (initiator_accept_status, _) = request_json(
        reqwest::Method::POST,
        &format!(
            "http://{addr}/openapi/v1/collaboration/ownership-transfers/{transfer_id}/accept"
        ),
        Some(&principal_token(staff_a)),
        Some(&json!({})),
    )
    .await;
    assert_eq!(
        initiator_accept_status,
        reqwest::StatusCode::FORBIDDEN,
        "the initiator must not be able to accept its own pending transfer"
    );

    let (accept_status, accept_body) = request_json(
        reqwest::Method::POST,
        &format!(
            "http://{addr}/openapi/v1/collaboration/ownership-transfers/{transfer_id}/accept"
        ),
        Some(&principal_token(staff_b)),
        Some(&json!({})),
    )
    .await;
    assert!(
        accept_status.is_success(),
        "B accepts the transfer: {accept_status} {accept_body}"
    );

    // -- 3. The RED variables: the roles flipped (spec §7.3/OT03). --
    let (status, a_mine_after_transfer) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_a)),
    )
    .await;
    assert!(status.is_success());
    assert_eq!(
        a_mine_after_transfer["data"]["items"]
            .as_array()
            .expect("A mine items")
            .iter()
            .find(|item| item["bot_id"] == json!(bot_x_id))
            .expect("X remains visible to the former owner through the residual manager edge")
            ["access_relation"]
            .as_str(),
        Some("manager"),
        "the former owner reads X as manager"
    );
    let (status, b_mine_after_transfer) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/mine"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert!(status.is_success());
    assert_eq!(
        b_mine_after_transfer["data"]["items"]
            .as_array()
            .expect("B mine items")
            .iter()
            .find(|item| item["bot_id"] == json!(bot_x_id))
            .expect("X must appear in B's mine")
            ["access_relation"]
            .as_str(),
        Some("owner"),
        "the confirmed recipient reads X as owner"
    );

    // The former owner keeps managing but loses the owner-only capability
    // (spec §9/OT14): a second transfer initiation is a plain 403.
    let staff_c = "e2etransferc";
    ensure_human(addr, staff_c).await;
    let (second_transfer_status, second_transfer_body) = request_json(
        reqwest::Method::POST,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/ownership-transfers"),
        Some(&principal_token(staff_a)),
        Some(&json!({
            "to_user_id": staff_c,
            "expected_owner_version": ownership_version + 1,
            "client_request_id": "7f0a8b2b-ce29-48a1-9d56-0f2a21b6c001"
        })),
    )
    .await;
    assert_eq!(
        second_transfer_status,
        reqwest::StatusCode::FORBIDDEN,
        "the former owner (manager) cannot initiate a transfer: {second_transfer_body}"
    );

    // -- 4. pending_rows_for_bot == 0: the pending slot was released by the
    //       accepted terminal state; both parties' status-filtered
    //       listings are empty of pending rows. --
    let (status, received_pending) = get_json(
        &format!(
            "http://{addr}/openapi/v1/collaboration/ownership-transfers?direction=received&status=pending"
        ),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert!(status.is_success(), "B's pending-filtered inbox: {status}");
    let pending_rows_for_bot = received_pending["data"]["total"]
        .as_u64()
        .unwrap_or(0)
        + {
            let (status, sent_pending) = get_json(
                &format!(
                    "http://{addr}/openapi/v1/collaboration/ownership-transfers?direction=sent&status=pending"
                ),
                Some(&principal_token(staff_a)),
            )
            .await;
            assert!(status.is_success());
            sent_pending["data"]["total"].as_u64().unwrap_or(0)
        };
    assert_eq!(
        pending_rows_for_bot, 0,
        "no pending ownership-transfer row remains for the bot"
    );

    // The persistent receipt is terminal and queryable by both parties.
    let (status, receipt) = get_json(
        &format!(
            "http://{addr}/openapi/v1/collaboration/ownership-transfers/{transfer_id}"
        ),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert!(status.is_success());
    assert_eq!(
        receipt["data"]["status"].as_str(),
        Some("accepted"),
        "the committed receipt is terminal accepted: {receipt}"
    );

    // -- 5. active_owner_rows_for_bot == 1: the strict ownership read
    //       answers with exactly one owner. The store's strict read
    //       returns Err on zero or multiple approved owner edges, so a
    //       successful read with owner=B IS the single-active-owner-row
    //       evidence (spec §5.2 unique owner slot). --
    let (ownership_status, new_ownership) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/ownership"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert_eq!(
        ownership_status,
        reqwest::StatusCode::OK,
        "the strict single-owner read must succeed: {new_ownership}"
    );
    assert_eq!(
        new_ownership["data"]["owner_user_id"].as_str(),
        Some(staff_b),
        "the unique active owner is now B"
    );
    let new_version = new_ownership["data"]["ownership_version"]
        .as_u64()
        .expect("post-transfer version");
    assert_eq!(
        new_version,
        ownership_version + 1,
        "the transfer CAS-bumped the ownership version"
    );

    // -- 6. The retained-manager window on the protected WS lane: A
    //       (manager) still receives protected frames after the transfer
    //       (spec §12.3/OT19). --
    let chat_ok = new_owner_connection
        .send_chat(&group_id, &bot_x_id, "post-transfer manager window")
        .await;
    assert!(chat_ok["ok"].as_bool().unwrap_or(false), "{chat_ok}");
    let retained_manager_frame = former_owner_connection
        .wait_for(|frame| {
            frame["type"] == "event"
                && frame["event"] == "chat"
                && frame["payload"]["state"] == json!("final")
                && frame["payload"]["message"]["role"] == json!("user")
        })
        .await
        .is_some();
    assert!(
        retained_manager_frame,
        "the former owner (manager) keeps its protected delivery after the transfer"
    );

    // -- 7. B (the new owner) revokes A's last NON-TEAM manager source
    //       (the ownership_transfer/<id> edge the accept installed). --
    let (revoke_status, revoke_body) = request_json(
        reqwest::Method::DELETE,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/managers/{staff_a}"),
        Some(&principal_token(staff_b)),
        None,
    )
    .await;
    assert!(
        revoke_status.is_success(),
        "the new owner revokes the former owner's last non-team source: {revoke_status} {revoke_body}"
    );
    assert_eq!(
        revoke_body["data"]["remaining_team_sources"]
            .as_array()
            .map(|sources| sources.len()),
        Some(0),
        "no remaining team sources for A: {revoke_body}"
    );

    // The OLD CREATOR denial (spec §12.2/e OT15): created_by still
    // matches A, but every control-plane write is a plain 403.
    let (former_owner_after_last_source_revoke_status, deny_body) = request_json(
        reqwest::Method::PATCH,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}"),
        Some(&principal_token(staff_a)),
        Some(&json!({ "name": "illegitimate rename by the old creator" })),
    )
    .await;
    assert_eq!(
        former_owner_after_last_source_revoke_status,
        reqwest::StatusCode::FORBIDDEN,
        "the old creator must not write the bot after the last source revoke: {deny_body}"
    );

    // The manager list read is denied too: the caller has no remaining
    // authority over the bot (anti-relationships-probing, spec §6).
    let (manager_list_status, _) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/managers"),
        Some(&principal_token(staff_a)),
    )
    .await;
    assert_eq!(
        manager_list_status,
        reqwest::StatusCode::FORBIDDEN,
        "the revoked former owner cannot enumerate the managers"
    );

    // X's runtime credentials re-onboarding under A's trusted-human lane
    // cannot claim ownership either (spec §13.3/OT16): registration is
    // first-registration-only.
    let re_onboard = reqwest::Client::new()
        .post(format!("http://{addr}/bots/onboard"))
        .header("Authorization", format!("Bearer {}", bot_x.token))
        .header("X-Mock-User-Id", staff_a)
        .header("X-Mock-Nick-Name", staff_a)
        .json(&json!({
            "name": "TransferBotX",
            "summary": "attempted authority reclaim via re-onboard",
            "skills": [{ "name": "chat" }],
            "domains": [],
            "scopes": [],
        }))
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .expect("re-onboard request");
    // Whatever the outcome (duplicate registration is rejected by the
    // registry), ownership must not flip back — the decisive assertion is
    // the ownership read below.
    let _ = re_onboard.text().await;

    let (ownership_status, ownership_after_reclaim) = get_json(
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}/ownership"),
        Some(&principal_token(staff_b)),
    )
    .await;
    assert_eq!(ownership_status, reqwest::StatusCode::OK);
    assert_eq!(
        ownership_after_reclaim["data"]["owner_user_id"].as_str(),
        Some(staff_b),
        "re-onboard under the old creator must not flip ownership"
    );

    // And the revoked former owner is still denied.
    let (recheck_status, _) = request_json(
        reqwest::Method::PATCH,
        &format!("http://{addr}/openapi/v1/collaboration/bots/{bot_x_id}"),
        Some(&principal_token(staff_a)),
        Some(&json!({ "name": "second illegitimate rename" })),
    )
    .await;
    assert_eq!(
        recheck_status,
        reqwest::StatusCode::FORBIDDEN,
        "no path back — the revoke is sticky as recorded"
    );

    // -- The protected WS lane observes the revoke (spec §14.5): the
    //       dequeue re-authorization must stop A's protected frames, while
    //       an independent valid owner binding still delivers. --
    let mut second_owner_connection = ViewWorkbenchClient::connect(addr, staff_b).await;
    let subscribed = second_owner_connection.subscribe_with_view(&group_id, &bot_x_id).await;
    assert!(
        subscribed["ok"].as_bool().unwrap_or(false),
        "the new owner's second connection binds view X: {subscribed}"
    );

    let chat_ok = new_owner_connection
        .send_chat(&group_id, &bot_x_id, "after the last source revoke")
        .await;
    assert!(chat_ok["ok"].as_bool().unwrap_or(false), "{chat_ok}");

    // B's OTHER owner connection still receives the protected frame (the
    // sender's own connection is excluded from delivery by the broadcast
    // contract) while A's former binding no longer does.
    let new_owner_still_receives = second_owner_connection
        .wait_for(|frame| {
            frame["type"] == "event"
                && frame["event"] == "chat"
                && frame["payload"]["state"] == json!("final")
                && frame["payload"]["message"]["role"] == json!("user")
        })
        .await;
    assert!(
        new_owner_still_receives.is_some(),
        "the new owner's protected delivery continues after A's revoke"
    );
    let revoked_connection_receives = former_owner_connection
        .wait_for(
            |frame| {
                frame["type"] == "event"
                && frame["event"] == "chat"
                    && frame["event"] == "chat"
                    && frame["payload"]["state"] == json!("final")
                    && frame["payload"]["message"]["role"] == json!("user")
            },
        )
        .await;
    assert!(
        revoked_connection_receives.is_none(),
        "the dequeue re-authorization must drop protected frames for the revoked former owner"
    );

    // -- 8. The friendship invariant held across the whole story. --
    let runtime_friend_edges_after =
        friend_edge_snapshot(addr, &bot_x_id, &bot_x.token).await;
    assert_eq!(
        runtime_friend_edges_after, runtime_friend_edges_before,
        "the transfer story must not fabricate or destroy friend edges"
    );
}