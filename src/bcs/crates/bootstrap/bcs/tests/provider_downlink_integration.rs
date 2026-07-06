mod helpers;

use std::{net::SocketAddr, sync::Arc, time::Duration};

use axum::{
    Json, Router,
    extract::State,
    http::{HeaderMap, header},
    routing::post,
};
use bcs_domain::SystemMessageEvent;
use helpers::{MockBot, create_temp_bots_dir, start_test_server, start_test_server_with_state};
use serde_json::{Value, json};
use tokio::sync::Mutex;

#[tokio::test]
async fn provider_bot_registers_and_is_routable_without_ws_connection() {
    let provider = start_provider_webhook().await;
    let bots_dir = create_temp_bots_dir();
    let (bcs_addr, _bcs_server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let mut driver = MockBot::connect(bcs_addr).await;
    driver.register("Driver", &["drive"], bcs_addr).await;
    let registered = register_provider_bot(
        &client,
        bcs_addr,
        provider.url(),
        "provider-routing",
        "reviewer-v2",
    )
    .await;

    let group_id = create_group(
        &client,
        bcs_addr,
        &driver.token,
        &driver.bot_id,
        &registered.bot_uuid,
    )
    .await;
    provider.capture.wait_for_method("chat.inject").await;
    provider.capture.clear().await;
    send_group_message(
        &client,
        bcs_addr,
        &driver.token,
        &group_id,
        &driver.bot_id,
        &format!("@{} please review", registered.bot_uuid),
    )
    .await;

    let chat_send = provider.capture.wait_for_method("chat.send").await;
    let expected_auth = format!("Bearer {}", registered.bcs_to_provider_token);
    assert_eq!(
        chat_send.authorization.as_deref(),
        Some(expected_auth.as_str())
    );
    assert_eq!(chat_send.body["bcn_group_id"], group_id);
    assert!(chat_send.body.get("bcs_group_id").is_none());
    assert_eq!(chat_send.body["from"]["kind"], "bot");
    assert_eq!(chat_send.body["from"]["name"], "Driver");
    assert_eq!(chat_send.body["to_bot"]["provider_id"], registered.provider_id);
    assert_eq!(chat_send.body["to_bot"]["provider_bot_ref"], "reviewer-v2");
    assert!(
        chat_send.body["message"].to_string().contains("please review"),
        "provider should receive the routed user message: {}",
        chat_send.body
    );
}

#[tokio::test]
async fn provider_history_converts_to_group_messages_shape() {
    let provider = start_provider_webhook().await;
    let bots_dir = create_temp_bots_dir();
    let (bcs_addr, _bcs_server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let registered = register_provider_bot(
        &client,
        bcs_addr,
        provider.url(),
        "provider-history",
        "history-v1",
    )
    .await;
    let group_id = create_single_provider_group(
        &client,
        bcs_addr,
        &registered.bot_runtime_token,
        &registered.bot_uuid,
    )
    .await;

    let response = client
        .get(format!(
            "http://{}/groups/{}/messages?view_bot_id={}",
            bcs_addr, group_id, registered.bot_uuid
        ))
        .header("X-Mock-User-Id", "11111111")
        .send()
        .await
        .expect("history request");
    let status = response.status();
    let body_text = response.text().await.expect("history response body");
    assert!(status.is_success(), "history request failed: {status} {body_text}");
    let messages: Value = serde_json::from_str(&body_text).expect("history response");
    assert_eq!(messages[0]["sender"], registered.bot_uuid);
    assert_eq!(messages[0]["role"], "assistant");
    assert_eq!(messages[0]["content"], "done");

    let history = provider.capture.wait_for_method("chat.history").await;
    assert_eq!(history.body["bcn_group_id"], group_id);
    assert!(history.body.get("bcs_group_id").is_none());
    assert_eq!(history.body["session_id"], group_id);
    assert!(history.body.get("session_key").is_none());
}

#[tokio::test]
async fn provider_final_callback_uses_run_context_not_request_group() {
    let provider = start_provider_webhook().await;
    let bots_dir = create_temp_bots_dir();
    let (bcs_addr, _bcs_server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let mut driver = MockBot::connect(bcs_addr).await;
    driver.register("Driver", &["drive"], bcs_addr).await;
    let registered = register_provider_bot(
        &client,
        bcs_addr,
        provider.url(),
        "provider-callback",
        "callback-v1",
    )
    .await;
    let group_id = create_group(
        &client,
        bcs_addr,
        &driver.token,
        &driver.bot_id,
        &registered.bot_uuid,
    )
    .await;
    provider.capture.wait_for_method("chat.inject").await;
    provider.capture.clear().await;
    send_group_message(
        &client,
        bcs_addr,
        &driver.token,
        &group_id,
        &driver.bot_id,
        &format!("@{} please review", registered.bot_uuid),
    )
    .await;
    let chat_send = provider.capture.wait_for_method("chat.send").await;
    assert!(chat_send.body.get("run_id").is_none());
    let run_id = chat_send.body["id"]
        .as_str()
        .expect("provider chat.send id")
        .to_string();

    let response = client
        .post(format!("http://{}/bot/events", bcs_addr))
        .header("X-BCN-Provider-Id", registered.provider_id.as_str())
        .header(
            "Authorization",
            format!("Bearer {}", registered.bot_runtime_token),
        )
        .json(&json!({
            "run_id": run_id,
            "state": "final",
            "message": { "text": "provider final" }
        }))
        .send()
        .await
        .expect("bot event callback");
    assert!(
        response.status().is_success(),
        "callback failed: {}",
        response.status()
    );
    let body: Value = response.json().await.expect("callback response");
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let frame = wait_for_bot_frame_containing(&mut driver, "provider final").await;
    assert_eq!(frame["method"], "chat.send");
    assert_eq!(frame["params"]["bcs_group_id"], group_id);
}

#[tokio::test]
async fn provider_admin_final_callback_uses_admin_token_and_bot_ref() {
    let provider = start_provider_webhook().await;
    let bots_dir = create_temp_bots_dir();
    let (bcs_addr, _bcs_server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let mut driver = MockBot::connect(bcs_addr).await;
    driver.register("Driver", &["drive"], bcs_addr).await;
    let registered = register_provider_bot_with_auth_mode(
        &client,
        bcs_addr,
        provider.url(),
        "provider-admin-callback",
        "admin-callback-v1",
        "provider_admin",
    )
    .await;
    let group_id = create_group(
        &client,
        bcs_addr,
        &driver.token,
        &driver.bot_id,
        &registered.bot_uuid,
    )
    .await;
    provider.capture.wait_for_method("chat.inject").await;
    provider.capture.clear().await;
    send_group_message(
        &client,
        bcs_addr,
        &driver.token,
        &group_id,
        &driver.bot_id,
        &format!("@{} please review", registered.bot_uuid),
    )
    .await;
    let chat_send = provider.capture.wait_for_method("chat.send").await;
    assert_eq!(chat_send.body["to_bot"]["provider_bot_ref"], "admin-callback-v1");
    let run_id = chat_send.body["id"]
        .as_str()
        .expect("provider chat.send id")
        .to_string();

    let response = client
        .post(format!("http://{}/bot/events", bcs_addr))
        .header("X-BCN-Provider-Id", registered.provider_id.as_str())
        .header("X-BCN-Provider-Bot-Ref", "admin-callback-v1")
        .header(
            "Authorization",
            format!("Bearer {}", registered.provider_admin_token),
        )
        .json(&json!({
            "run_id": run_id,
            "state": "final",
            "message": { "text": "provider_admin final" }
        }))
        .send()
        .await
        .expect("provider_admin bot event callback");
    let status = response.status();
    let body_text = response.text().await.expect("callback response body");
    assert!(
        status.is_success(),
        "provider_admin callback failed: {status} {body_text}"
    );
    let body: Value = serde_json::from_str(&body_text).expect("callback response");
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let frame = wait_for_bot_frame_containing(&mut driver, "provider_admin final").await;
    assert_eq!(frame["method"], "chat.send");
    assert_eq!(frame["params"]["bcs_group_id"], group_id);
}

#[tokio::test]
async fn system_message_provider_chat_send_final_callback_is_processed() {
    let provider = start_provider_webhook().await;
    let bots_dir = create_temp_bots_dir();
    let (bcs_addr, _bcs_server, state) =
        start_test_server_with_state(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let mut driver = MockBot::connect(bcs_addr).await;
    driver.register("Driver", &["drive"], bcs_addr).await;
    let registered = register_provider_bot(
        &client,
        bcs_addr,
        provider.url(),
        "provider-system-message",
        "system-v1",
    )
    .await;
    let group_id = create_group(
        &client,
        bcs_addr,
        &driver.token,
        &driver.bot_id,
        &registered.bot_uuid,
    )
    .await;
    provider.capture.wait_for_method("chat.inject").await;
    provider.capture.clear().await;

    let group = state
        .services
        .group
        .get(&group_id)
        .await
        .expect("group should exist");

    state
        .services
        .system_message
        .notify(
            &group_id,
            SystemMessageEvent::BotHiddenNotice {
                group_id: group_id.clone(),
                mentioner_bot_id: registered.bot_uuid.clone(),
                hidden_bot_name: "system maintenance notice".to_string(),
            },
            "session-system-message",
            &group.participants,
        )
        .await
        .expect("dispatch system message");

    let chat_send = provider.capture.wait_for_method("chat.send").await;
    assert_eq!(chat_send.body["method"], "chat.send");
    // Protocol >= 3: the provider downlink body keys the group on `bcn_group_id`
    // and carries the session scope on `session_id` (from `bcs_session_id`).
    assert_eq!(chat_send.body["bcn_group_id"], group_id);
    assert_eq!(chat_send.body["session_id"], "session-system-message");
    assert!(chat_send.body.get("bcs_group_id").is_none());
    assert_eq!(chat_send.body["from"]["kind"], "bot");
    assert_eq!(chat_send.body["from"]["name"], "bcs-system-message");
    assert!(
        chat_send
            .body["message"]
            .to_string()
            .contains("system maintenance notice"),
        "provider should receive system message: {}",
        chat_send.body
    );
    let run_id = chat_send.body["id"]
        .as_str()
        .expect("system provider chat.send id")
        .to_string();

    let response = client
        .post(format!("http://{}/bot/events", bcs_addr))
        .header("X-BCN-Provider-Id", registered.provider_id.as_str())
        .header(
            "Authorization",
            format!("Bearer {}", registered.bot_runtime_token),
        )
        .json(&json!({
            "run_id": run_id,
            "state": "final",
            "message": { "text": "provider replied to system message" }
        }))
        .send()
        .await
        .expect("system bot event callback");
    assert!(
        response.status().is_success(),
        "callback failed: {}",
        response.status()
    );
    let body: Value = response.json().await.expect("callback response");
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let frame =
        wait_for_bot_frame_containing(&mut driver, "provider replied to system message").await;
    assert_eq!(frame["method"], "chat.send");
    assert_eq!(frame["params"]["bcs_group_id"], "session-system-message");
}

#[tokio::test]
async fn state_machine_dispatches_provider_bot_and_accepts_final_callback() {
    let provider = start_provider_webhook().await;
    let bots_dir = create_temp_bots_dir();
    let (bcs_addr, _bcs_server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let mut driver = MockBot::connect(bcs_addr).await;
    driver.register("Driver", &["drive"], bcs_addr).await;
    let registered = register_provider_bot(
        &client,
        bcs_addr,
        provider.url(),
        "provider-state-machine",
        "state-machine-worker",
    )
    .await;

    let group_id = create_state_machine_group(
        &client,
        bcs_addr,
        &driver.token,
        &driver.bot_id,
        &registered.bot_uuid,
    )
    .await;
    provider.capture.clear().await;

    let response = client
        .post(format!(
            "http://{}/groups/{}/state-machine-runs",
            bcs_addr, group_id
        ))
        .json(&json!({
            "input": { "question": "review provider downlink state machine" }
        }))
        .send()
        .await
        .expect("start state-machine run");
    let status = response.status();
    let body_text = response.text().await.expect("state-machine run body");
    assert!(
        status.is_success(),
        "start state-machine run failed: {status} {body_text}"
    );
    let run: Value = serde_json::from_str(&body_text).expect("state-machine run response");
    let state_machine_run_id = run["run"]["run_id"]
        .as_str()
        .expect("state-machine run id")
        .to_string();

    let chat_send = provider.capture.wait_for_method("chat.send").await;
    assert_eq!(chat_send.body["method"], "chat.send");
    assert_eq!(chat_send.body["to_bot"]["provider_id"], registered.provider_id);
    assert_eq!(chat_send.body["to_bot"]["provider_bot_ref"], "state-machine-worker");
    let provider_run_id = chat_send.body["id"]
        .as_str()
        .expect("state-machine provider chat.send id")
        .to_string();
    assert!(
        provider_run_id.starts_with("smnode-"),
        "state-machine dispatch should use smnode delivery id, got {provider_run_id}"
    );

    let response = client
        .post(format!("http://{}/bot/events", bcs_addr))
        .header("X-BCN-Provider-Id", registered.provider_id.as_str())
        .header(
            "Authorization",
            format!("Bearer {}", registered.bot_runtime_token),
        )
        .json(&json!({
            "run_id": provider_run_id,
            "state": "final",
            "message": { "text": "state-machine provider final" }
        }))
        .send()
        .await
        .expect("state-machine provider callback");
    let status = response.status();
    let body_text = response.text().await.expect("callback body");
    assert!(
        status.is_success(),
        "state-machine provider callback failed: {status} {body_text}"
    );

    let response = client
        .get(format!(
            "http://{}/state-machine-runs/{}",
            bcs_addr, state_machine_run_id
        ))
        .send()
        .await
        .expect("get state-machine run");
    let status = response.status();
    let body_text = response.text().await.expect("state-machine view body");
    assert!(
        status.is_success(),
        "get state-machine run failed: {status} {body_text}"
    );
    let view: Value = serde_json::from_str(&body_text).expect("state-machine run view");
    assert_eq!(view["run"]["status"], "completed");
    assert_eq!(view["nodes"][0]["status"], "completed");
    assert_eq!(view["nodes"][0]["artifact_text"], "state-machine provider final");
}

#[derive(Clone, Default)]
struct ProviderCapture {
    requests: Arc<Mutex<Vec<CapturedProviderRequest>>>,
}

impl ProviderCapture {
    async fn wait_for_method(&self, method: &str) -> CapturedProviderRequest {
        for _ in 0..100 {
            if let Some(request) = self
                .requests
                .lock()
                .await
                .iter()
                .find(|request| request.body["method"] == method)
                .cloned()
            {
                return request;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let seen: Vec<Value> = self
            .requests
            .lock()
            .await
            .iter()
            .map(|request| request.body.clone())
            .collect();
        panic!("provider did not receive {method}; seen requests: {seen:#?}");
    }

    async fn clear(&self) {
        self.requests.lock().await.clear();
    }
}

#[derive(Clone, Debug)]
struct CapturedProviderRequest {
    authorization: Option<String>,
    body: Value,
}

struct ProviderServer {
    capture: ProviderCapture,
    addr: SocketAddr,
    _handle: tokio::task::JoinHandle<()>,
}

impl ProviderServer {
    fn url(&self) -> String {
        format!("http://{}/webhook", self.addr)
    }
}

async fn start_provider_webhook() -> ProviderServer {
    let capture = ProviderCapture::default();
    let app = Router::new()
        .route("/webhook", post(provider_webhook))
        .with_state(capture.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind provider webhook");
    let addr = listener.local_addr().expect("provider addr");
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    ProviderServer {
        capture,
        addr,
        _handle: handle,
    }
}

async fn provider_webhook(
    State(capture): State<ProviderCapture>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Json<Value> {
    let authorization = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    capture.requests.lock().await.push(CapturedProviderRequest {
        authorization,
        body: body.clone(),
    });

    if body["method"] == "chat.history" {
        return Json(json!({
            "ok": true,
            "session_id": body["session_id"].as_str().unwrap_or_default(),
            "messages": [
                {
                    "id": "hist-1",
                    "role": "assistant",
                    "content": "done",
                    "timestamp": 1710960050000_u64
                }
            ],
            "has_more": false
        }));
    }

    Json(json!({ "ok": true }))
}

struct RegisteredProviderBot {
    provider_id: String,
    bot_uuid: String,
    bot_runtime_token: String,
    provider_admin_token: String,
    bcs_to_provider_token: String,
}

async fn register_provider_bot(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    webhook_url: String,
    provider_name: &str,
    provider_bot_ref: &str,
) -> RegisteredProviderBot {
    register_provider_bot_with_auth_mode(
        client,
        bcs_addr,
        webhook_url,
        provider_name,
        provider_bot_ref,
        "static_bearer",
    )
    .await
}

async fn register_provider_bot_with_auth_mode(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    webhook_url: String,
    provider_name: &str,
    provider_bot_ref: &str,
    auth_mode: &str,
) -> RegisteredProviderBot {
    let provider: Value = client
        .post(format!("http://{}/providers", bcs_addr))
        .header("X-Mock-User-Id", "11111111")
        .json(&json!({
            "name": provider_name,
            "webhook_url": webhook_url,
            "auth": { "mode": auth_mode }
        }))
        .send()
        .await
        .expect("register provider")
        .json()
        .await
        .expect("provider response");
    let provider_id = provider["provider_id"]
        .as_str()
        .expect("provider id")
        .to_string();
    let admin_token = provider["provider_admin_token"]
        .as_str()
        .expect("provider admin token")
        .to_string();
    let bcs_to_provider_token = provider["bcs_to_provider_token"]
        .as_str()
        .expect("bcs to provider token")
        .to_string();

    let bot_response = client
        .post(format!(
            "http://{}/providers/{}/bots",
            bcs_addr,
            provider_id.as_str()
        ))
        .header("Authorization", format!("Bearer {admin_token}"))
        .json(&json!({
            "name": "Provider Bot",
            "summary": "Provider-managed bot",
            "owners": ["11111111"],
            "provider_bot_ref": provider_bot_ref
        }))
        .send()
        .await
        .expect("register provider bot");
    let bot_status = bot_response.status();
    let bot_body = bot_response.text().await.expect("provider bot response body");
    assert!(
        bot_status.is_success(),
        "register provider bot failed: {bot_status} {bot_body}"
    );
    let bot: Value = serde_json::from_str(&bot_body).expect("provider bot response");

    let registered = RegisteredProviderBot {
        provider_id,
        bot_uuid: bot["bot_uuid"].as_str().expect("bot uuid").to_string(),
        bot_runtime_token: bot["bot_runtime_token"]
            .as_str()
            .expect("bot runtime token")
            .to_string(),
        provider_admin_token: admin_token,
        bcs_to_provider_token,
    };
    set_bot_visibility_public(
        client,
        bcs_addr,
        &registered.bot_runtime_token,
        &registered.bot_uuid,
    )
    .await;
    registered
}

async fn set_bot_visibility_public(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    token: &str,
    bot_uuid: &str,
) {
    let response = client
        .put(format!("http://{}/bots/{}/visibility", bcs_addr, bot_uuid))
        .header("Authorization", format!("Bearer {token}"))
        .json(&json!({ "visibility": "public" }))
        .send()
        .await
        .expect("set provider bot visibility");
    let status = response.status();
    let body_text = response.text().await.expect("visibility response body");
    assert!(
        status.is_success(),
        "set provider bot visibility failed: {status} {body_text}"
    );
}

async fn create_group(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    token: &str,
    driver_bot: &str,
    provider_bot: &str,
) -> String {
    let mut sender_routes = serde_json::Map::new();
    sender_routes.insert(driver_bot.to_string(), json!([provider_bot]));
    let response = client
        .post(format!("http://{}/groups", bcs_addr))
        .header("Authorization", format!("Bearer {token}"))
        .json(&json!({
            "driver_bot": driver_bot,
            "routing_policy": {
                "mode": "hybrid",
                "sender_routes": sender_routes
            },
            "participants": [
                { "bot_uuid": driver_bot, "role": "driver" },
                { "bot_uuid": provider_bot, "role": "consultant" }
            ]
        }))
        .send()
        .await
        .expect("create group");
    let status = response.status();
    let body_text = response.text().await.expect("group response body");
    assert!(status.is_success(), "create group failed: {status} {body_text}");
    let group: Value = serde_json::from_str(&body_text).expect("group response");
    group["id"].as_str().expect("group id").to_string()
}

async fn create_single_provider_group(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    provider_token: &str,
    provider_bot: &str,
) -> String {
    let response = client
        .post(format!("http://{}/groups", bcs_addr))
        .header("Authorization", format!("Bearer {provider_token}"))
        .json(&json!({
            "driver_bot": provider_bot,
            "participants": [
                { "bot_uuid": provider_bot, "role": "driver" }
            ]
        }))
        .send()
        .await
        .expect("create provider group");
    let status = response.status();
    let body_text = response.text().await.expect("provider group response body");
    assert!(
        status.is_success(),
        "create provider group failed: {status} {body_text}"
    );
    let group: Value = serde_json::from_str(&body_text).expect("provider group response");
    group["id"].as_str().expect("provider group id").to_string()
}

async fn create_state_machine_group(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    token: &str,
    driver_bot: &str,
    provider_bot: &str,
) -> String {
    let definition_yaml = r#"
api_version: bcs.collaboration/v1
name: Provider State Machine
participants:
  worker:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    nodes:
      answer:
        kind: bot_task
        display_name: Answer
        assignee:
          type: bot_binding
          binding: worker
        instruction: Answer the user request.
        final_output: true
"#;
    let response = client
        .post(format!("http://{}/groups", bcs_addr))
        .header("Authorization", format!("Bearer {token}"))
        .json(&json!({
            "driver_bot": driver_bot,
            "group_strategy": "state_machine",
            "participant_bindings": {
                "worker": {
                    "source": "manual",
                    "bot_ids": [provider_bot]
                }
            },
            "participants": [
                { "bot_uuid": driver_bot },
                { "bot_uuid": provider_bot }
            ],
            "collaboration_definition_yaml": definition_yaml
        }))
        .send()
        .await
        .expect("create state-machine group");
    let status = response.status();
    let body_text = response.text().await.expect("state-machine group body");
    assert!(
        status.is_success(),
        "create state-machine group failed: {status} {body_text}"
    );
    let group: Value = serde_json::from_str(&body_text).expect("state-machine group response");
    group["id"].as_str().expect("state-machine group id").to_string()
}

async fn send_group_message(
    client: &reqwest::Client,
    bcs_addr: SocketAddr,
    token: &str,
    group_id: &str,
    sender: &str,
    message: &str,
) {
    let response = client
        .post(format!("http://{}/groups/{}/chat", bcs_addr, group_id))
        .header("Authorization", format!("Bearer {token}"))
        .json(&json!({
            "message": message,
            "from": sender
        }))
        .send()
        .await
        .expect("send group message");
    let status = response.status();
    let body_text = response.text().await.expect("group chat response body");
    assert!(status.is_success(), "group chat failed: {status} {body_text}");
    let body: Value = serde_json::from_str(&body_text).expect("group chat response");
    assert!(
        body["delivered_count"].as_u64().unwrap_or(0) > 0,
        "group chat did not deliver to any bot: {body}"
    );
}

async fn wait_for_bot_frame_containing(bot: &mut MockBot, needle: &str) -> Value {
    for _ in 0..40 {
        if let Some(frame) = bot.recv_frame_short().await {
            if frame.to_string().contains(needle) {
                return frame;
            }
        }
    }
    panic!("bot did not receive frame containing {needle}");
}
