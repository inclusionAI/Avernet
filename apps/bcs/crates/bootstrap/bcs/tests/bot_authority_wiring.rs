//! Task 18 — composition-root wiring of the bot authority model
//! (`bootstrap/bcs/src/server.rs` assembly).
//!
//! Every assertion drives a REAL server constructor (memory or durable
//! SQLite) and a REAL request through the assembled routers/sockets —
//! never a field inspection after `new`:
//!
//! - startup FAILS (Err, not a process panic) when the selected datasource
//!   has no bot-authority store wiring;
//! - startup FAILS when `[team_manager_sync]` declares the lane but no
//!   signing-key material resolves (never an anonymous mount);
//! - the mounted team credential lane exists ONLY when the composition
//!   root armed it (404 before wiring / 401 with a route present after);
//! - the v1 (OpenAPI) and legacy `/bots/my` entrypoints report THE SAME
//!   `access_relation` facts for one fixture (owner + manager), and the
//!   registration path initialized the first owner edge through the
//!   assembled store (the `mine` reads both answer through it);
//! - a Workbench connection WITH an explicitly selected view receives its
//!   protected frames only through the ASSEMBLED delivery-authorization
//!   service (pre-wiring the fail-closed default drops them).
//!
//! Run with:
//! ```bash
//! cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test bot_authority_wiring
//! ```

mod helpers;

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use helpers::{MockBot, create_temp_bots_dir, create_test_config, onboard_bot_as_user, query_my_bots, start_test_server_with_state};
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::{Value, json};
use tokio_tungstenite::tungstenite::Message;

use bcs::{BcsConfig, BcsServer, CachePluginKind, DbPluginKind, InfrastructurePlugins};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

const GATEWAY_PRINCIPAL_SIGNING_KEY: &[u8] = b"test-only-gateway-principal-signing-key";

// ============================================================================
// Gateway principal helpers (the v1 OpenAPI entrypoint identity)
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

/// `GET /openapi/v1/collaboration/bots/mine` for the given verified user.
async fn v1_mine(addr: std::net::SocketAddr, user_id: &str) -> Value {
    let response = reqwest::Client::new()
        .get(format!("http://{addr}/openapi/v1/collaboration/bots/mine"))
        .header("x-avernet-principal", principal_token(user_id))
        .send()
        .await
        .expect("v1 mine request");
    assert!(
        response.status().is_success(),
        "v1 mine must answer for a verified principal: {}",
        response.status()
    );
    let envelope: Value = response.json().await.expect("v1 mine envelope");
    envelope["data"]["items"].clone()
}

/// The legacy `/bots/my` entrypoint for the same user (mock identity lane).
async fn legacy_mine(addr: std::net::SocketAddr, staff_no: &str) -> Value {
    let body = query_my_bots(addr, staff_no).await;
    body["items"].clone()
}

/// The `access_relation` of one bot in a mine items array (`(bot, relation)`
/// pairs collected, so parity failures name the offending entry).
fn relation_of(items: &Value, bot_id: &str) -> String {
    items
        .as_array()
        .unwrap_or_else(|| panic!("mine items must be an array: {items}"))
        .iter()
        .find(|item| {
            item["bot_uuid"] == json!(bot_id)
                || item["bot_id"] == json!(bot_id)
        })
        .unwrap_or_else(|| panic!("bot {bot_id} missing from mine items: {items}"))
        ["access_relation"]
        .as_str()
        .unwrap_or_else(|| {
            panic!("mine item for {bot_id} must carry access_relation: {items}")
        })
        .to_string()
}

// ============================================================================
// Startup configuration gates (durable constructor, real datasource)
// ============================================================================

fn sqlite_plugins(db_kind: DbPluginKind, db: Arc<dyn bcs_db_api::DbPlugin>) -> InfrastructurePlugins {
    let cache: Arc<dyn bcs_cache_api::CachePlugin> = Arc::new(bcs_cache_local::InMemoryCachePlugin::new());
    InfrastructurePlugins::from_parts(CachePluginKind::LocalMemory, db_kind, cache, db)
}

/// Minimal durable-constructor config: the secret provider reads the
/// process environment, so each reference below names a test-dedicated
/// variable (set by the specific test that needs it).
fn durable_test_config(dir: &tempfile::TempDir) -> BcsConfig {
    let mut config = create_test_config(&dir.path().to_path_buf());
    config.secret.provider = "env".into();
    config.gateway_principal.signing_key_secret =
        Some("BCS_BOT_AUTHORITY_WIRING_GATEWAY_KEY".into());
    config.group_session_ws.signing_key_secret = "BCS_BOT_AUTHORITY_WIRING_SESSION_KEY".into();
    config.database.sqlite.path = dir
        .path()
        .join("wiring-durable.sqlite")
        .to_str()
        .unwrap()
        .into();
    config
}

/// Dedicated env-var material for the durable tests (unique names so
/// parallel tests in this binary never race on one variable). The `env`
/// secret provider snapshots the process environment at server
/// construction with the `BCS_SECRET_` prefix.
fn set_durable_test_secrets() {
    unsafe {
        std::env::set_var(
            "BCS_SECRET_BCS_BOT_AUTHORITY_WIRING_GATEWAY_KEY",
            String::from_utf8_lossy(GATEWAY_PRINCIPAL_SIGNING_KEY).into_owned(),
        );
        std::env::set_var(
            "BCS_SECRET_BCS_BOT_AUTHORITY_WIRING_SESSION_KEY",
            "wiring-test-group-session-signing-key",
        );
    }
}

/// An unmatched datasource has NO bot-authority store wiring: the
/// composition root must FAIL the startup with an `Err` (never a panic,
/// never an unmounted authority).
#[tokio::test]
async fn unconfigured_authority_store_fails_startup() {
    let dir = create_temp_bots_dir();
    set_durable_test_secrets();
    let config = durable_test_config(&dir);
    let db: Arc<dyn bcs_db_api::DbPlugin> = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("open local sqlite"),
    );
    let plugins = sqlite_plugins(DbPluginKind::External("authority-unwired-fixture".into()), db);

    let unconfigured_authority_result =
        BcsServer::new_with_infrastructure(config, plugins, bcs::BcsServerExtensions::default())
            .await;

    assert!(
        unconfigured_authority_result.is_err(),
        "a datasource without bot-authority store wiring must fail the startup"
    );
    let message = unconfigured_authority_result.err().unwrap().to_string();
    assert!(
        message.contains("bot authority store wiring"),
        "the failure must name the authority wiring: {message}"
    );
}

/// `[team_manager_sync] enabled` without resolvable signing-key material is
/// a startup configuration error — the lane is never mounted anonymously.
#[tokio::test]
async fn enabled_team_sync_without_credential_fails_startup() {
    let dir = create_temp_bots_dir();
    set_durable_test_secrets();
    let mut config = durable_test_config(&dir);
    config.team_manager_sync.enabled = true;
    let db: Arc<dyn bcs_db_api::DbPlugin> = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("open local sqlite"),
    );
    let plugins = sqlite_plugins(DbPluginKind::LocalSqlite, db);

    let unconfigured_team_credential_result =
        BcsServer::new_with_infrastructure(config, plugins, bcs::BcsServerExtensions::default())
            .await;

    assert!(
        unconfigured_team_credential_result.is_err(),
        "declared team sync without a signing key must fail the startup, got a server"
    );
    let message = unconfigured_team_credential_result.err().unwrap().to_string();
    assert!(
        message.contains("team_manager_sync is enabled"),
        "the failure must surface the Task 13 resolver error chain: {message}"
    );
}

/// The credential-gated team slice exists ONLY when the composition root
/// resolved real key material: the reserved path answers 401 (route
/// mounted, credential missing) — never 404-with-a-route and never an
/// anonymous 2xx.
#[tokio::test]
async fn configured_team_sync_mounts_the_credential_gate() {
    const KEY_REF: &str = "BCS_BOT_AUTHORITY_WIRING_TEAM_KEY";
    // Dedicated env reference (secret provider = "env", `BCS_SECRET_`
    // prefix): a unique name so parallel tests in this process never
    // collide on the variable.
    unsafe {
        std::env::set_var(
            "BCS_SECRET_BCS_BOT_AUTHORITY_WIRING_TEAM_KEY",
            "wiring-test-team-signing-key-material",
        )
    };
    let dir = create_temp_bots_dir();
    set_durable_test_secrets();
    let mut config = durable_test_config(&dir);
    config.team_manager_sync.enabled = true;
    config.team_manager_sync.signing_key_secret = Some(KEY_REF.to_string());

    let server = BcsServer::new_with_storage(config)
        .await
        .expect("configured team sync startup must succeed");
    let (addr, handle) = server
        .run_on_random_port()
        .await
        .expect("start configured server");
    let abort = handle.abort_handle();
    struct Stop(tokio::task::AbortHandle);
    impl Drop for Stop {
        fn drop(&mut self) {
            self.0.abort();
        }
    }
    let _stop = Stop(abort);

    let response = reqwest::Client::new()
        .put(format!(
            "http://{addr}/api/v1/bots/bot-wiring-probe/manager-sources/teams/team-wiring"
        ))
        .header("Idempotency-Key", "wiring-probe-1")
        .json(&json!({"operation": "sync", "manager_user_ids": []}))
        .send()
        .await
        .expect("team route probe");

    assert_eq!(
        response.status(),
        reqwest::StatusCode::UNAUTHORIZED,
        "the mounted team lane must demand a service credential (no anonymous lane)"
    );
}

// ============================================================================
// v1 / legacy entrypoint parity + registration initialization witness
// ============================================================================

/// One fixture, two entrypoints: the v1 OpenAPI mine and the legacy
/// `/bots/my` must report identical `access_relation` facts, and a
/// successful first registration must have initialized the owner edge
/// through the SAME assembled authority store both lanes read.
#[tokio::test]
async fn v1_and_legacy_entrypoints_report_the_same_relations() {
    let dir = create_temp_bots_dir();
    let (addr, _handle, _state) =
        start_test_server_with_state(&dir.path().to_path_buf()).await;

    let staff_no = "wiringuser1";
    let bot = MockBot::connect(addr).await;
    let bot_id = bot.bot_id.clone();

    // Registration through the assembled paths: the legacy onboard with a
    // trusted human initializes the FIRST owner edge (register success ==
    // ownership initialized; no later "repair" pass allowed to claim it).
    onboard_bot_as_user(addr, &bot.token, "WiringBot", staff_no).await;

    let registration_owner_initialization_was_called = {
        // Both mine reads must see the bot AT ALL — that is only true when
        // the owner edge exists in the shared authority store.
        let v1_owner_items = v1_mine(addr, staff_no).await;
        relation_of(&v1_owner_items, &bot_id) == "owner"
    };
    assert!(
        registration_owner_initialization_was_called,
        "registration must have initialized the first owner edge through the assembled store"
    );

    // A manager edge for a second live human, granted through the MOUNTED
    // v1 manager route (the composition root must have injected the Task
    // 13 facade — an unmounted facade answers fail-closed 500).
    let staff_two = "wiringuser2";
    let ensure = reqwest::Client::new()
        .post(format!("http://{addr}/me/ensure-human"))
        .header("X-Mock-User-Id", staff_two)
        .header("X-Mock-Nick-Name", "Wiring User Two")
        .send()
        .await
        .expect("ensure-human request");
    assert!(
        ensure.status().is_success(),
        "the second human must be materialized for the manager grant: {} {}",
        ensure.status(),
        ensure.text().await.unwrap_or_default()
    );
    let grant = reqwest::Client::new()
        .put(format!(
            "http://{addr}/openapi/v1/collaboration/bots/{bot_id}/managers/{staff_two}"
        ))
        .header("x-avernet-principal", principal_token(staff_no))
        .send()
        .await
        .expect("manager grant via v1 route");
    assert!(
        grant.status().is_success(),
        "the v1 manager route must be wired by the composition root: {} {}",
        grant.status(),
        grant.text().await.unwrap_or_default()
    );

    // Parity: same fixture, same facts, both entrypoints.
    let v1_owner_items = v1_mine(addr, staff_no).await;
    let v1_manager_items = v1_mine(addr, staff_two).await;
    let legacy_owner_items = legacy_mine(addr, staff_no).await;
    let legacy_manager_items = legacy_mine(addr, staff_two).await;

    let v1_owner_relation = relation_of(&v1_owner_items, &bot_id);
    let legacy_owner_relation = relation_of(&legacy_owner_items, &bot_id);
    let v1_manager_relation = relation_of(&v1_manager_items, &bot_id);
    let legacy_manager_relation = relation_of(&legacy_manager_items, &bot_id);

    assert_eq!(v1_owner_relation, legacy_owner_relation);
    assert_eq!(v1_manager_relation, legacy_manager_relation);
    assert_eq!(v1_owner_relation, "owner");
    assert_eq!(v1_manager_relation, "manager");
}

// ============================================================================
// Workbench protected delivery through the ASSEMBLED authorization service
// ============================================================================

/// A minimal UserBound Workbench client that selects an EXPLICIT view
/// (`viewActorId`) — the Task 16 protected-lane shape. Frames of type
/// `event` arrive only when the assembled DeliveryAuthorizationService
/// answered the enqueue/dequeue authorization with Deliver.
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
    async fn connect(
        addr: std::net::SocketAddr,
        staff_no: &str,
    ) -> Self {
        let url = format!("ws://{addr}/ws");
        let mut request = url
            .into_client_request()
            .expect("workbench ws request");
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

    /// `connect` (subscribe) with an explicitly selected view actor.
    async fn subscribe_with_view(
        &mut self,
        group_id: &str,
        view_actor_id: &str,
        session_id: Option<&str>,
    ) -> Value {
        let id = format!("wb_connect_{}", self.counter);
        self.counter += 1;
        let mut params = json!({
            "group_id": group_id,
            "viewActorId": view_actor_id,
        });
        if let Some(session_id) = session_id {
            params["session_id"] = json!(session_id);
        }
        self.send(json!({
            "type": "req",
            "id": id,
            "method": "connect",
            "params": params
        }))
        .await;
        self.wait_for(|frame| frame["type"] == "res" && frame["id"] == id)
            .await
            .expect("subscribe response")
    }

    /// `chat.send` bound to one actor (the selected view).
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
                "bot_name": "WiringWsBot",
                "message": text,
                "mentions": []
            }
        }))
        .await;
        self.wait_for(|frame| frame["type"] == "res" && frame["id"] == id)
            .await
            .expect("chat.send response")
    }

    /// Await the first frame matching `predicate` (bounded wait).
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
                    let _ = self
                        .write
                        .send(Message::Pong(data))
                        .await;
                }
                _ => {
                    eprintln!("workbench frames observed before the socket ended: {seen:?}");
                    return None;
                }
            }
        }
    }
}

/// The protected workbench lane: a UserBound connection with an explicitly
/// selected view receives its chat frames only when the composition root
/// assembled the REAL DeliveryAuthorizationService into the WS registry
/// (the pre-wiring fail-closed default answers InvalidateBinding and
/// drops every protected frame; PublicControl acks still flow).
#[tokio::test]
async fn assembled_delivery_authorization_protects_workbench_frames() {
    let dir = create_temp_bots_dir();
    let (addr, _handle, _state) =
        start_test_server_with_state(&dir.path().to_path_buf()).await;

    let staff_no = "wiring_ws_user";
    let bot = MockBot::connect(addr).await;
    let bot_id = bot.bot_id.clone();
    onboard_bot_as_user(addr, &bot.token, "WiringWsBot", staff_no).await;

    // A group whose only member is the owned driver bot.
    let group_response = reqwest::Client::new()
        .post(format!("http://{addr}/groups"))
        .header("Authorization", format!("Bearer {}", bot.token))
        .json(&json!({
            "driver_bot": bot_id,
            "participants": [{ "bot_uuid": bot_id, "role": "driver" }],
            "label": "Wiring Protected Delivery Group"
        }))
        .send()
        .await
        .expect("group creation");
    assert!(group_response.status().is_success());
    let created: Value = group_response.json().await.expect("group creation body");
    let group_id = created["id"].as_str().expect("group id").to_string();

    // TWO Workbench connections for the same owning Human, both with the
    // EXPLICIT bot view selected: this is the enrollment shape that makes
    // every group chat frame a PROTECTED delivery, re-authorized at enqueue
    // and dequeue through the assembled authorization service.
    let mut workbench_sender = ViewWorkbenchClient::connect(addr, staff_no).await;
    let subscribed = workbench_sender.subscribe_with_view(&group_id, &bot_id, None).await;
    assert!(subscribed["ok"].as_bool().unwrap_or(false), "sender view must bind");
    let mut workbench_receiver = ViewWorkbenchClient::connect(addr, staff_no).await;
    let subscribed = workbench_receiver.subscribe_with_view(&group_id, &bot_id, None).await;
    assert!(subscribed["ok"].as_bool().unwrap_or(false), "receiver view must bind");

    // A chat send through the group: the Workbench user-message echo is
    // broadcast to the group's subscribers (the sender's own connection is
    // excluded), so the RECEIVER connection obtains its protected frame
    // only when the assembled DeliveryAuthorizationService answers Deliver
    // for the bound (user, view, group) identity.
    let chat_ok = workbench_sender.send_chat(&group_id, &bot_id, "请检查权限链路").await;
    assert!(
        chat_ok["ok"].as_bool().unwrap_or(false),
        "chat.send must be accepted: {chat_ok}"
    );

    let connection_router_authorization_hook_was_called = workbench_receiver
        .wait_for(|frame| {
            frame["type"] == "event"
                && frame["event"] == "chat"
                && frame["payload"]["state"] == json!("final")
                && frame["payload"]["message"]["role"] == json!("user")
        })
        .await
        .is_some();

    assert!(
        connection_router_authorization_hook_was_called,
        "the assembled delivery-authorization service must Deliver the protected chat frame"
    );
}