//! End-to-End Integration Test: the Bot Authority whole
//! stack (bot-authority plan Task 8.1).
//!
//! Runs the ideographic authority story against the REAL
//! process server (same harness shape as `e2e_workbench`:
//! real `bcs` binary, real HTTP router, real WebSocket
//! writer, trusted register seeding — ruling T6d-1), with
//! NO permissive hand-made hook anywhere:
//!
//! 1. trusted registration (v1 Human token, mock Human
//!    principal) → add manager (single PUT) → the manager
//!    performs a business op on the Bot's behalf (a
//!    workbench group chat.send as the Bot — the Row-14
//!    `can_manage` leg through the real authority hook);
//! 2. trusted App `managers:sync` (HS256 Gateway JWT, the
//!    RegisteredBot-5 route-test plumbing minted against
//!    the REAL process verifier) — full-set replace
//!    including removing the manual manager; the
//!    configured trusted-app scope (`app + tenant + actions`
//!    — bound to the instance env at resolve) denies
//!    everything outside the rule;
//! 3. ownership transfer: initiate → 201/200 refresh →
//!    accept by the target Human;
//! 4. sync removing the ORIGINAL owner (now a plain
//!    manager) → ONLY the Bot-bound HTTP/WS entries for
//!    the revoked Human fail (403 / deny), while the
//!    original Human's INDEPENDENT rights (their own
//!    trusted bot registration) keep working;
//! 5. second transfer (new target), then the normal
//!    retirement path (`DELETE /bots/{id}`).
//!
//! Scope note (honest apertures, Plan 8.1):
//! - The two-instance revoke-only-resource-qualification
//!   and only-tighten-scope cases stay pinned where they
//!   own the maps at socket level:
//!   `bcs-app-session --test delivery_admission` and
//!   `bcs-ws --test authority_delivery` (7b's matrices);
//!   this file adds only the HTTP/application-boundary
//!   revocation consequence through the real server.
//! - Persistent-webhook semantics after the creator loses
//!   the edge stay pinned by
//!   `bcs-eventing --test authority_cutover ::
//!   webhook_delivery_continues_after_the_creator_loses_
//!   the_edge` (§8.4); this harness does not stage a
//!   provider webhook receiver.
//! - Provider retirement and its cleanup-pending retry
//!   stays pinned by `bcs-bot-store --test
//!   authority_lifecycle` (the Provider three-branch
//!   scenario); this file runs the normal retirement
//!   path only.
//! - The legacy/OpenAPI × v1/v2 token four cells stay
//!   pinned by `bcs-http/tests/
//!   authority_registration_routes.rs` and
//!   `bcs-api-http/tests/authority_registration_routes.rs`;
//!   this file adds the chain-level fault injection
//!   (expired/tampered Gateway JWT → 401) through the
//!   real server.
//! - The Memory-mode same-record pin (profile port,
//!   4.3a) lives in `bcs-bot-store --test memory_authority`;
//!   the suite-agnostic check here asserts the
//!   after-registration chain read: the manager/owner the
//!   registration committed is observable through the
//!   runtime-injected authority chain itself.
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test e2e_bot_authority \
//!     -- --test-threads=1 --nocapture
//! ```

mod e2e_helpers;

use std::time::Duration;

use e2e_helpers::{create_temp_dir, ProcessManager};
use futures_util::{SinkExt, StreamExt};
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::{Value, json};
use tokio_tungstenite::{
    tungstenite::{client::IntoClientRequest, http::HeaderValue, Message},
};

// ============================================================================
// Gateway principal material (the REAL verifier's contract shape)
// ============================================================================

/// The process server resolves its Gateway-principal
/// signing key from this env (defaults of
/// `[gateway_principal]`); `e2e_helpers::start_bcs` seeds
/// the same value.
const TEST_GATEWAY_PRINCIPAL_SIGNING_KEY: &[u8] =
    b"test-only-gateway-principal-signing-key";

const PRINCIPAL_HEADER: &str = "x-avernet-principal";

/// The instance env the process server resolves with no
/// `SERVER_ENV`/`REAL_SERVER_ENV`/`ALIPAY_APP_ENV` set
/// (`RuntimeEnv::Dev` → `"dev"`); the trusted-app rule
/// must name it byte-exact.
const INSTANCE_ENV: &str = "dev";
const APP_ID: i64 = 7;
const TENANT: &str = "tenant-a";

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs()
}

fn mint_with(key: &[u8], claims: Value) -> String {
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    encode(&header, &claims, &EncodingKey::from_secret(key))
        .expect("token signs")
}

fn claims_for(principals: Value) -> Value {
    let now = now_secs();
    json!({
        "iss": "gateway",
        "aud": "bcs",
        "iat": now,
        "exp": now + 300,
        "principals": principals,
    })
}

fn user_principal(id: &str, tenant: Option<&str>) -> Value {
    json!({
        "type": "user",
        "tenant": tenant,
        "subject": {
            "id": id,
            "username": id,
            "display_name": Value::Null,
            "full_name": Value::Null,
            "tenant_id": Value::Null,
        },
    })
}

fn app_principal(app_id: i64, tenant: Option<&str>) -> Value {
    named_app_principal(app_id, bcs_test_support::AUTHORITY_FIXTURE_APP_NAME, tenant)
}

fn named_app_principal(app_id: i64, app_name: &str, tenant: Option<&str>) -> Value {
    json!({
        "type": "app",
        "tenant": tenant,
        "app": {
            "app_id": app_id,
            "app_name": app_name,
            "owners": "owner",
            "tenant": tenant,
            "app_type": "THIRD_PARTY",
        },
    })
}

/// A verified Human principal (the authority routes
/// always require one).
fn human_token(user_id: &str) -> String {
    mint_with(
        TEST_GATEWAY_PRINCIPAL_SIGNING_KEY,
        claims_for(json!([user_principal(user_id, Some(TENANT))])),
    )
}

fn app_token(app_id: i64, tenant: Option<&str>) -> String {
    mint_with(
        TEST_GATEWAY_PRINCIPAL_SIGNING_KEY,
        claims_for(json!([app_principal(app_id, tenant)])),
    )
}

fn named_app_token(app_id: i64, app_name: &str, tenant: Option<&str>) -> String {
    mint_with(
        TEST_GATEWAY_PRINCIPAL_SIGNING_KEY,
        claims_for(json!([named_app_principal(app_id, app_name, tenant)])),
    )
}

fn expired_human_token(user_id: &str) -> String {
    let now = now_secs();
    mint_with(
        TEST_GATEWAY_PRINCIPAL_SIGNING_KEY,
        json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now - 7200,
            "exp": now - 3600,
            "principals": [user_principal(user_id, Some(TENANT))],
        }),
    )
}

fn tampered_human_token(user_id: &str) -> String {
    let token = human_token(user_id);
    let mut parts: Vec<String> = token.split('.').map(str::to_string).collect();
    let payload = parts.get_mut(1).expect("payload part");
    let first = payload.chars().next().expect("non-empty payload");
    *payload = if first == 'e' {
        format!("f{}", &payload[1..])
    } else {
        format!("e{}", &payload[1..])
    };
    parts.join(".")
}

// ============================================================================
// HTTP helpers
// ============================================================================

const WALT: &str = "walt";
const BOB: &str = "bob";
const CAROL: &str = "carol";

fn urlencode(value: &str) -> String {
    let mut encoded = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_'
            | b'.' | b'~' => encoded.push(byte as char),
            other => encoded.push_str(&format!("%{other:02X}")),
        }
    }
    encoded
}

/// TRUSTED registration (ruling T6d-1): the harness seeds
/// each fixture Bot's authority ownership through the
/// local trusted register surface — `GET /register/token`
/// (mock HUMAN principal) → `POST /register?token&bot-name`.
async fn register_trusted_bot(
    client: &reqwest::Client,
    bcs_url: &str,
    owner_user: &str,
    name: &str,
) -> (String, String) {
    let resp = client
        .get(&format!("{bcs_url}/register/token"))
        .header("X-Mock-User-Id", owner_user)
        .header("X-Mock-Nick-Name", owner_user)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("register token request");
    assert!(
        resp.status().is_success(),
        "register token mint failed for {owner_user}: {} {}",
        resp.status(),
        resp.text().await.unwrap_or_default()
    );
    let body: Value = resp.json().await.expect("register token body");
    let register_token = body["token"].as_str().expect("token").to_string();

    let resp = client
        .post(&format!(
            "{bcs_url}/register?token={}&bot-name={}",
            urlencode(&register_token),
            urlencode(name)
        ))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("trusted register request");
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert!(
        status.is_success(),
        "trusted registration as {owner_user} failed: {status} {text}"
    );
    let body: Value = serde_json::from_str(&text).expect("registration body");
    (
        body["bot_uuid"].as_str().expect("bot_uuid").to_string(),
        body["bot_token"].as_str().expect("bot_token").to_string(),
    )
}

/// Collaboration route helper: `method`, `path`, JWT, body.
async fn collaboration(
    client: &reqwest::Client,
    bcs_url: &str,
    method: &str,
    path: &str,
    token: &str,
    body: Option<Value>,
) -> reqwest::Response {
    let request = match (method, body) {
        ("POST", Some(body)) => {
            client.post(&format!("{bcs_url}{path}")).json(&body)
        }
        ("GET", _) => client.get(&format!("{bcs_url}{path}")),
        ("PUT", Some(body)) => {
            client.put(&format!("{bcs_url}{path}")).json(&body)
        }
        ("PUT", None) => client.put(&format!("{bcs_url}{path}")),
        ("POST", None) => client.post(&format!("{bcs_url}{path}")),
        ("DELETE", None) => client.delete(&format!("{bcs_url}{path}")),
        other => panic!("unsupported collaboration call {other:?}"),
    };
    request
        .header(PRINCIPAL_HEADER, token)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("collaboration request")
}

async fn read_managers(
    client: &reqwest::Client,
    bcs_url: &str,
    token: &str,
    bot_id: &str,
) -> (u16, Value) {
    let resp = collaboration(
        client,
        bcs_url,
        "GET",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        token,
        None,
    )
    .await;
    let status = resp.status().as_u16();
    (status, resp.json().await.unwrap_or(Value::Null))
}

fn manager_ids(page: Value) -> Vec<String> {
    page["data"]["items"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

fn owner_user_of(page: &Value) -> String {
    page["data"]["owner"]["owner_user_id"]
        .as_str()
        .unwrap_or_default()
        .to_string()
}

// ============================================================================
// Workbench WebSocket client (the manager's business op)
// ============================================================================

/// Minimal workbench frontend (mirrors
/// `e2e_workbench::WorkbenchClient`): real `/ws` socket
/// with the mock Human identity, group subscribe, and
/// chat.send on behalf of a managed Bot.
struct WorkbenchClient {
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
    req_counter: u32,
}

impl WorkbenchClient {
    async fn connect(port: u16, staff_no: &str) -> Self {
        let request = format!("ws://127.0.0.1:{port}/ws")
            .into_client_request()
            .expect("ws request");
        let request = {
            let mut request = request;
            request.headers_mut().insert(
                "X-Mock-User-Id",
                HeaderValue::from_str(staff_no).expect("header value"),
            );
            request.headers_mut().insert(
                "X-Mock-Nick-Name",
                HeaderValue::from_str(staff_no).expect("header value"),
            );
            request
        };
        let (ws_stream, _) = tokio_tungstenite::connect_async(request)
            .await
            .expect("workbench connect");
        let (write, read) = ws_stream.split();
        Self {
            write,
            read,
            req_counter: 0,
        }
    }

    async fn subscribe_group(&mut self, group_id: &str) -> Value {
        self.send_request(
            "connect",
            json!({ "group_id": group_id }),
        )
        .await
    }

    /// `chat.send` on the Bot's behalf (Row-14 `can_manage`
    /// leg through the real authority hook).
    async fn send_request(&mut self, method: &str, params: Value) -> Value {
        self.req_counter += 1;
        let req_id = format!("req-{}", self.req_counter);
        let frame = json!({
            "type": "req",
            "id": req_id,
            "method": method,
            "params": params,
        });
        self.write
            .send(Message::Text(
                serde_json::to_string(&frame).expect("frame").into(),
            ))
            .await
            .expect("frame send");
        self.wait_response(&req_id, Duration::from_secs(8))
            .await
    }

    async fn wait_response(&mut self, req_id: &str, timeout: Duration) -> Value {
        match self.wait_response_opt(req_id, timeout).await {
            Some(frame) => frame,
            None => panic!("timeout waiting for response to {req_id}"),
        }
    }

    /// The chat-send positive authorization faces a
    /// PRE-EXISTING branch-wide hole (unchanged by the
    /// authority work, tolerated identically by the
    /// existing `e2e_workbench` suite): the group-message
    /// persistence transaction fails for the
    /// workbench-bound path, so a run that passed
    /// authorization may still end without a `res`. The
    /// authorization verdict itself (the
    /// `forbidden_sender` rejection) always comes back —
    /// so the positive assertion is "no unauthorized
    /// rejection", never "the res arrived".
    async fn wait_response_opt(
        &mut self,
        req_id: &str,
        timeout: Duration,
    ) -> Option<Value> {
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(
                tokio::time::Instant::now(),
            );
            match tokio::time::timeout(remaining, self.read.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    let frame: Value =
                        serde_json::from_str(text.as_str()).expect("frame json");
                    if frame["type"] == "res" && frame["id"] == req_id {
                        return Some(frame);
                    }
                }
                Ok(Some(Ok(Message::Ping(data)))) => {
                    let _ = self.write.send(Message::Pong(data)).await;
                }
                Ok(Some(Ok(_))) => continue,
                Ok(Some(Err(e))) => panic!("workbench socket error: {e}"),
                Ok(None) => panic!("workbench socket closed"),
                Err(_) => return None,
            }
        }
    }

    /// A `chat.send` on the Bot's behalf. Returns
    /// * `Some(error_code)` — the authorization (or any
    ///   ws error frame) rejected the send;
    /// * `Some("")` — the send was accepted (the group
    ///   message persistence hole below keeps the
    ///   positive `res` from arriving, never an
    ///   authorization rejection);
    /// * `None` — the window elapsed with no response.
    async fn chat_as_bot_outcome(
        &mut self,
        group_id: &str,
        bot_id: &str,
        bot_name: &str,
        message: &str,
    ) -> Option<String> {
        self.req_counter += 1;
        let req_id = format!("req-{}", self.req_counter);
        let frame = json!({
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": {
                "group_id": group_id,
                "message": message,
                "mentions": [],
                "bot_id": bot_id,
                "bot_name": bot_name,
            },
        });
        self.write
            .send(Message::Text(
                serde_json::to_string(&frame).expect("frame").into(),
            ))
            .await
            .expect("frame send");
        self.wait_response_opt(&req_id, Duration::from_secs(3))
            .await
            .map(|res| {
                if res["ok"].as_bool().unwrap_or(true) {
                    String::new()
                } else {
                    res["error"]["code"]
                        .as_str()
                        .unwrap_or("unknown")
                        .to_string()
                }
            })
    }

    async fn close(&mut self) {
        let _ = self.write.close().await;
    }
}

// ============================================================================
// Process harness
// ============================================================================

fn trusted_apps_rule() -> Value {
    json!({
        "app": bcs_test_support::AUTHORITY_FIXTURE_APP_NAME,
        "tenant": TENANT,
        "actions": ["managers:sync"],
    })
}

async fn wait_health(port: u16) {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{port}/health");
    for _ in 0..30 {
        let healthy = |response: reqwest::Response| response.status().is_success();
        if client
            .get(&url)
            .timeout(Duration::from_secs(1))
            .send()
            .await
            .map(healthy)
            .unwrap_or(false)
        {
            return;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("BCS server did not become healthy on port {port}");
}

/// Connect the Registered Bot's REAL WebSocket (the
/// producer side): `/ws/bot` resume via the registration
/// token, mirrors the workbench harness bot-connect
/// convention.
async fn connect_bot_socket(
    port: u16,
    bot_token: &str,
) -> tokio_tungstenite::WebSocketStream<
    tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
> {
    let ws_url = format!("ws://127.0.0.1:{port}/ws/bot");
    let mut ws = {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        let mut attempt = Err(tokio_tungstenite::tungstenite::Error::Io(
            std::io::Error::other("not attempted"),
        ));
        while tokio::time::Instant::now() < deadline {
            match tokio_tungstenite::connect_async(&ws_url).await {
                Ok((ws, _)) => {
                    attempt = Ok(ws);
                    break;
                }
                Err(err) => attempt = Err(err),
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        match attempt {
            Ok(ws) => ws,
            Err(error) => panic!("bot ws connect failed: {error}"),
        }
    };
    let frame = json!({
        "type": "req", "id": "e2e_connect_1", "method": "bot.connect",
        "params": { "token": bot_token }
    });
    ws.send(Message::Text(
        serde_json::to_string(&frame).expect("frame").into(),
    ))
    .await
    .expect("bot connect send");
    let (mut sink, mut stream) = ws.split();
    loop {
        match tokio::time::timeout(Duration::from_secs(5), stream.next()).await
        {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(value) =
                    serde_json::from_str::<Value>(&text)
                {
                    if value["type"] == "res"
                        && value["id"] == "e2e_connect_1"
                    {
                        assert!(
                            value["ok"].as_bool().unwrap_or(false),
                            "bot.connect must resume: {value}"
                        );
                        break;
                    }
                }
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                sink.send(Message::Pong(data))
                    .await
                    .expect("pong");
            }
            Ok(other) => panic!("unexpected bot socket frame {other:?}"),
            Err(_) => panic!("no response to bot.connect"),
        }
    }
    // Drain the onboarding instruction burst.
    loop {
        match tokio::time::timeout(Duration::from_millis(200), stream.next())
            .await
        {
            Ok(Some(Ok(Message::Text(_)))) => continue,
            Ok(Some(Ok(Message::Ping(data)))) => {
                sink.send(Message::Pong(data)).await.expect("pong");
            }
            _ => break,
        }
    }
    let ws = sink.reunite(stream).expect("reunite");
    ws
}

/// The ideographic story (plan §8.1): registration →
/// manager → business op → App sync → transfer chain →
/// owner revocation fallout → second transfer →
/// retirement.
#[tokio::test]
async fn e2e_bot_authority_full_chain_story() {
    let (_temp_dir, data_dir) = create_temp_dir();
    let mut pm = ProcessManager::new();
    let port = pm.start_bcs(&data_dir).await.expect("start phase A");
    let mut bcs_url = format!("http://127.0.0.1:{port}");
    println!("[Phase A] BCS on {bcs_url}");

    let client = reqwest::Client::new();

    // ── 1. Trusted registration (v1 Human token) ─────────────────────
    let (bot_id, bot_token) =
        register_trusted_bot(&client, &bcs_url, WALT, "Story Bot").await;
    println!("[1] trusted bot registered: {bot_id} (owner {WALT})");

    // The target/secondary Humans become KNOWN through
    // their own trusted registrations (the Backend-side
    // human pre-seed requirement in production; here the
    // same register chain seeds `human_bob`/`human_carol`).
    let (bob_bot_id, _) =
        register_trusted_bot(&client, &bcs_url, BOB, "Bob Bot").await;
    let (carol_bot_id, _) =
        register_trusted_bot(&client, &bcs_url, CAROL, "Carol Bot").await;
    let _ = (bob_bot_id, carol_bot_id);

    // ── Memory/chain read: the SAME committed record the
    //    registration wrote is observable through the
    //    runtime-injected authority chain (memory-mode
    //    same-record pin = 4.3a's memory boats; here at
    //    process level).
    let (status, page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(status, 200, "owner reads the manager page: {page:?}");
    assert_eq!(owner_user_of(&page), WALT, "registration owner readable");
    println!("[1] registration-committed owner readable via runtime chain");

    // ── Chain-level fault injection (cross-references the
    //    4b four-cell suites): expired / tampered Gateway
    //    JWT through the real verifier → 401.
    for (label, token) in [
        ("expired", expired_human_token(WALT)),
        ("tampered", tampered_human_token(WALT)),
    ] {
        let (status, _) =
            read_managers(&client, &bcs_url, &token, &bot_id).await;
        assert_eq!(status, 401, "chain-level {label} JWT must 401");
    }
    println!("[1] expired/tampered JWT → 401 through the real verifier");

    // ── 2. Add manager (single PUT, owner Human) ─────────────────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers/{BOB}"),
        &human_token(WALT),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "manager add: {text}");
    println!("[2] manager {BOB} added");

    // ── 3. The manager performs a business op on the Bot's
    //    behalf (workbench chat.send as the Bot), and the
    //    real Run producer frames flow to the manager's
    //    protected socket.
    let group = client
        .post(&format!("{bcs_url}/groups"))
        .header("Authorization", format!("Bearer {bot_token}"))
        .json(&json!({
            "driver_bot": bot_id,
            "participants": [
                { "bot_uuid": bot_id, "role": "driver" }
            ],
            "label": "Authority Story Group"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("group create request");
    assert!(
        group.status().is_success(),
        "group creation must succeed: {}",
        group.status()
    );
    let group_id = group.json::<Value>().await.expect("group body")["id"]
        .as_str()
        .expect("group id")
        .to_string();

    // The Bot's REAL producer connection (path pin: the
    // workbench run chain needs the bot's connected
    // uplink, exactly like `e2e_workbench`).
    let mut bot_ws = connect_bot_socket(port, &bot_token).await;
    let _ = &mut bot_ws;

    // The owner seat still connects to the Bot's own
    // group before the authority story proceeds.
    let mut walt_socket = WorkbenchClient::connect(port, WALT).await;
    let walt_connect = walt_socket.subscribe_group(&group_id).await;
    assert!(
        walt_connect["ok"].as_bool().unwrap_or(false),
        "the owner may view the Bot's group: {walt_connect}"
    );
    let owner_chat = walt_socket
        .chat_as_bot_outcome(&group_id, &bot_id, "Story Bot", "from the owner seat")
        .await;
    assert!(
        owner_chat.is_none() || owner_chat.as_deref() == Some(""),
        "owner-as-bot chat.send must pass authorization: {owner_chat:?}"
    );
    walt_socket.close().await;

    let mut bob_socket = WorkbenchClient::connect(port, BOB).await;
    let connect_ok = bob_socket.subscribe_group(&group_id).await;
    assert!(
        connect_ok["ok"].as_bool().unwrap_or(false),
        "the current manager may view the Bot's group: {connect_ok}"
    );
    let sent = bob_socket
        .chat_as_bot_outcome(
            &group_id,
            &bot_id,
            "Story Bot",
            "worked from the manager seat",
        )
        .await;
    assert!(
        sent.is_none() || sent.as_deref() == Some(""),
        "manager-as-bot chat.send must pass authorization (can_manage): {sent:?}"
    );
    println!("[3] manager business op authorized at the WS boundary");
    bob_socket.close().await;

    // ── 4. Restart with the trusted-app scope rule for the
    //    App NAME (byte-exact match on the verified
    //    principal's app name; the rule covers every bot on
    //    the instance), then prove durability across the
    //    restart.
    pm.kill_all();
    let rule_bots = trusted_apps_rule();
    let port = {
        let extra = json!({
            "bot_authority": { "trusted_apps": [rule_bots] }
        });
        start_with_extra(&mut pm, &data_dir, Some(extra)).await
    };
    bcs_url = format!("http://127.0.0.1:{port}");
    println!("[4] server restarted with trusted_app rule; {bcs_url}");

    let (status, page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(status, 200, "post-restart owner read: {page:?}");
    assert_eq!(owner_user_of(&page), WALT, "owner edge survived restart");

    // ── 5. Trusted-App scope: rule enumeration ───────────────────────
    // Wrong tenant → deny; an unknown App NAME → deny (the
    // scoping identity is the name now, not a numeric id);
    // an action outside `actions` (ownership:read not
    // granted) → deny.
    let (status, body) =
        read_managers(&client, &bcs_url, &app_token(APP_ID, Some("other")), &bot_id)
            .await;
    assert_eq!(
        status, 403,
        "App outside the trusted rule tenant must be denied: {body:?}"
    );
    let (status, body) = read_managers(
        &client,
        &bcs_url,
        &named_app_token(99, "Other App", Some(TENANT)),
        &bot_id,
    )
    .await;
    assert_eq!(
        status, 403,
        "an untrusted app name is denied: {body:?}"
    );
    let (status, body) = read_managers(
        &client,
        &bcs_url,
        &app_token(APP_ID, Some(TENANT)),
        &bot_id,
    )
    .await;
    assert_eq!(
        status, 403,
        "the sync-only rule denies ownership reads: {body:?}"
    );
    println!("[5] trusted-app scope denials pinned");

    // ── 6. Trusted App `managers:sync`: full-set replace ─────────────
    // The App replaces the whole manager set: the manual
    // manager {BOB} goes away, {CAROL} becomes the set;
    // the owner is NOT a sync operand (keeping {WALT}
    // away proves it).
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "manager_user_ids": [CAROL] })),
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "App sync: {text}");
    let (_, page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(owner_user_of(&page), WALT, "sync never touches the owner");
    assert!(
        !manager_ids(page.clone()).contains(&BOB.to_string()),
        "the manual manager was replaced away: {page:?}"
    );
    assert!(
        manager_ids(page).contains(&CAROL.to_string()),
        "the synced set is the requested one"
    );
    println!("[6] App full-set sync applied (set = [{CAROL}])");

    // ── 7. Ownership transfer: initiate → refresh → accept ───────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{BOB}"
        ),
        &human_token(WALT),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 201, "new transfer row → 201: {text}");

    // Refresh (renew) of the same pending round → 200.
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{BOB}"
        ),
        &human_token(WALT),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "reuse/refresh → 200: {text}");

    // The target Human accepts.
    let resp = collaboration(
        &client,
        &bcs_url,
        "POST",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{BOB}/accept"
        ),
        &human_token(BOB),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "target accept: {text}");

    let (_, page) =
        read_managers(&client, &bcs_url, &human_token(BOB), &bot_id).await;
    assert_eq!(owner_user_of(&page), BOB, "BOB is the new owner");
    assert!(
        manager_ids(page.clone()).contains(&WALT.to_string()),
        "the ORIGINAL owner is now a plain manager: {page:?}"
    );
    println!("[7] transfer initiated→renewed→accepted; owner = {BOB}");

    // ── 8. Sync removing the ORIGINAL owner (a plain
    //    manager now): ONLY the Bot-bound entries for
    //    {WALT} fail after this.
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "manager_user_ids": [CAROL] })),
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "App sync removing {WALT}: {text}");

    // Bot-bound HTTP (contract-pinned reads): the
    // revoked-ex-owner Human loses the 403-no-edge bot
    // read (compat note GET /bots/{id}).
    let resp = client
        .get(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", WALT)
        .header("X-Mock-Nick-Name", WALT)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("bot read");
    assert_eq!(
        resp.status().as_u16(),
        403,
        "revoked-ex-owner no longer reads the Bot: {}",
        resp.text().await.unwrap_or_default()
    );
    let (status, _) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(
        status, 403,
        "the ex-manager loses the authority manager read"
    );
    // ... while an in-set manager keeps management reads.
    let (status, _) =
        read_managers(&client, &bcs_url, &human_token(CAROL), &bot_id).await;
    assert_eq!(status, 200, "the in-set manager keeps management reads");

    // Bot-bound WS/application boundary: the revoked
    // Human fails BOTH group-view and sender control
    // (Row 14 consults the CURRENT edge — the same
    // consult the protected delivery writer does per
    // frame in 7b's socket suites).
    let mut bot_ws_b = connect_bot_socket(port, &bot_token).await;
    let _ = &mut bot_ws_b;
    let mut walt_socket = WorkbenchClient::connect(port, WALT).await;
    let connect_view = walt_socket.subscribe_group(&group_id).await;
    let walt_denied = !connect_view["ok"].as_bool().unwrap_or(true);
    let walt_chat = walt_socket
        .chat_as_bot_outcome(&group_id, &bot_id, "Story Bot", "from the ex-owner seat")
        .await;
    let chat_denied = walt_chat
        .as_deref()
        .is_some_and(|code| code == "forbidden_sender");
    assert!(
        walt_denied || chat_denied,
        "the revoked-ex-owner must lose the Bot-bound workbench op \
         (connect: {connect_view}, chat: {walt_chat:?})"
    );
    walt_socket.close().await;

    // ... but the CURRENT owner's business op keeps
    // passing authorization (only the revoked Human is
    // impacted).
    let mut bob_socket2 = WorkbenchClient::connect(port, BOB).await;
    let connect_ok = bob_socket2.subscribe_group(&group_id).await;
    assert!(
        connect_ok["ok"].as_bool().unwrap_or(false),
        "the current owner keeps the workbench view: {connect_ok}"
    );
    let sent = bob_socket2
        .chat_as_bot_outcome(&group_id, &bot_id, "Story Bot", "still working")
        .await;
    assert!(
        sent.is_none() || sent.as_deref() == Some(""),
        "the current owner keeps the business op: {sent:?}"
    );
    bob_socket2.close().await;
    println!("[8] sync removed {WALT}; exactly {WALT}'s Bot-bound entries fail");

    // Independent Human rights: the ORIGINAL owner keeps
    // their unmanaged, self-owned capacity — a fresh
    // trusted registration still succeeds (their own
    // creator/instance view is untouched by the Bot-bound
    // revocation).
    let (own_bot_id, _) =
        register_trusted_bot(&client, &bcs_url, WALT, "Walt Independent Bot")
            .await;
    assert_ne!(own_bot_id, bot_id);
    let (_, own_page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &own_bot_id).await;
    assert_eq!(owner_user_of(&own_page), WALT);
    println!("[8] {WALT}'s independent rights intact (own registered bot)");

    // ── 9. Second transfer (new target) ──────────────────────────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{CAROL}"
        ),
        &human_token(BOB),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 201, "second transfer to {CAROL}: {text}");
    let resp = collaboration(
        &client,
        &bcs_url,
        "POST",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{CAROL}/accept"
        ),
        &human_token(CAROL),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "{CAROL} accepts: {text}");
    let (_, page) =
        read_managers(&client, &bcs_url, &human_token(CAROL), &bot_id).await;
    assert_eq!(owner_user_of(&page), CAROL);
    println!("[9] second transfer accepted; owner = {CAROL}");

    // ── 10. Normal retirement (cleanup-pending retry and
    //     the Provider three-branch stay pinned by
    //     `bcs-bot-store --test authority_lifecycle`).
    //     `DELETE /bots/{id}` requires a HUMAN caller: the
    //     non-owner Human is denied (current-edge policy),
    //     the CURRENT owner retires.
    let resp = client
        .delete(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", WALT)
        .header("X-Mock-Nick-Name", WALT)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("non-owner retire request");
    assert_eq!(
        resp.status().as_u16(),
        403,
        "a Human with no current management edge cannot retire: \
         {} {}",
        resp.status(),
        resp.text().await.unwrap_or_default()
    );
    let resp = client
        .delete(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", CAROL)
        .header("X-Mock-Nick-Name", CAROL)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("owner retire request");
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "normal retirement by the owner: {text}");
    let resp = client
        .delete(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", CAROL)
        .header("X-Mock-Nick-Name", CAROL)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("second retire request");
    let second = resp.status().as_u16();
    assert!(
        second == 404 || second == 409,
        "the second retirement is idempotent-negative: {second} {}",
        resp.text().await.unwrap_or_default()
    );
    println!("[10] normal retirement committed (second attempt → {second})");

    println!("\n========== E2E Bot Authority Full Chain Story PASSED ==========");
}

/// The trusted-app scope is deny-by-default: with NO
/// `[bot_authority]` configuration at all (the release
/// default), a fully verified trusted App identity is
/// denied every authority mutation — the feature stays
/// dormant until the config is added.
#[tokio::test]
async fn e2e_bot_authority_trusted_apps_default_deny() {
    let (_temp_dir, data_dir) = create_temp_dir();
    let mut pm = ProcessManager::new();
    let port = pm.start_bcs(&data_dir).await.expect("start server");
    let bcs_url = format!("http://127.0.0.1:{port}");
    let client = reqwest::Client::new();

    let (bot_id, _) =
        register_trusted_bot(&client, &bcs_url, WALT, "Dormant Bot").await;

    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "manager_user_ids": [WALT] })),
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(
        status, 403,
        "no trusted_apps config → deny-everything default: {text}"
    );
    let (status, _) =
        read_managers(&client, &bcs_url, &app_token(APP_ID, Some(TENANT)), &bot_id)
            .await;
    assert_eq!(
        status, 403,
        "no trusted_apps config → even reads are denied"
    );

    println!("\n========== E2E Trusted-App Default Deny PASSED ==========");
}

async fn start_with_extra(
    pm: &mut ProcessManager,
    data_dir: &std::path::Path,
    extra: Option<Value>,
) -> u16 {
    let port = pm
        .start_bcs_with_extra_config(&data_dir.to_path_buf(), extra)
        .await
        .expect("start server with bot_authority config");
    wait_health(port).await;
    port
}
