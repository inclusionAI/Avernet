mod helpers;

use std::{sync::{Arc, atomic::{AtomicBool, Ordering}}, time::Duration};
use axum::{body::Body, extract::State, http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response}, routing::post, Json, Router};
use helpers::{MockBot, create_temp_bots_dir, start_test_server};
use serde_json::{Value, json};
use tokio::sync::Mutex;

#[derive(Clone, Default)]
struct Capture {
    requests: Arc<Mutex<Vec<Value>>>,
    fail: Arc<AtomicBool>,
}

struct Receiver {
    url: String,
    capture: Capture,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Receiver {
    fn drop(&mut self) { self.task.abort(); }
}

impl Receiver {
    async fn start() -> Self {
        let capture = Capture::default();
        let app = Router::new().route("/webhook", post(receive)).with_state(capture.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/webhook", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
        Self { url, capture, task }
    }

    async fn request(&self, method: &str) -> Value {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if let Some(v) = self.capture.requests.lock().await.iter()
                    .find(|v| v["method"] == method).cloned() { return v; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.expect("expected receiver was not called")
    }
}

async fn receive(State(capture): State<Capture>, headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    body["observed_protocol"] = json!(headers.get("X-BCN-Protocol-Version").unwrap().to_str().unwrap());
    capture.requests.lock().await.push(body.clone());
    if capture.fail.load(Ordering::SeqCst) {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    if body["method"] == "chat.send" && body["observed_protocol"] == "2.0" {
        let event = json!({"runId": body["id"], "seq": 1, "state": "final",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "endpoint final"}]}});
        return Response::builder().header("Content-Type", "text/event-stream")
            .body(Body::from(format!("event: chat\ndata: {event}\n\n"))).unwrap();
    }
    Json(json!({"ok": true})).into_response()
}

async fn json_response(request: reqwest::RequestBuilder) -> Value {
    let response = request.send().await.unwrap();
    let status = response.status();
    let text = response.text().await.unwrap();
    assert!(status.is_success(), "{status}: {text}");
    serde_json::from_str(&text).unwrap()
}

#[tokio::test]
async fn independent_endpoints_route_callback_protocol_without_fallback() { route("1.0").await; }

#[tokio::test]
async fn independent_endpoints_route_sse_protocol_without_fallback() { route("2.0").await; }

async fn route(protocol: &str) {
    let receivers = [Receiver::start().await, Receiver::start().await, Receiver::start().await];
    let bots_dir = create_temp_bots_dir();
    let (addr, server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base = format!("http://{addr}");
    let client = reqwest::Client::new();
    let mut driver = MockBot::connect(addr).await;
    driver.register("Driver", &["drive"], addr).await;
    let provider = json_response(client.post(format!("{base}/providers"))
        .header("X-Mock-User-Id", "11111111")
        .json(&json!({"name": "Independent", "webhook_url": receivers[0].url,
            "protocol_version": protocol, "auth": {"mode": "static_bearer"}}))).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin = provider["provider_admin_token"].as_str().unwrap();
    let bot_base = format!("{base}/providers/{provider_id}/bots");
    let mut bots = Vec::new();
    let mut groups = Vec::new();
    for (i, receiver) in receivers.iter().enumerate() {
        let mut request = json!({"name": format!("Bot {i}"), "owners": ["11111111"],
            "provider_bot_ref": format!("ref-{i}")});
        if i != 0 { request["webhook_url"] = json!(receiver.url); }
        let bot = json_response(client.post(&bot_base).bearer_auth(admin).json(&request)).await;
        let id = bot["bot_uuid"].as_str().unwrap();
        json_response(client.put(format!("{base}/bots/{id}/visibility"))
            .bearer_auth(bot["bot_runtime_token"].as_str().unwrap())
            .json(&json!({"visibility": "public"}))).await;
        let group = json_response(client.post(format!("{base}/groups")).bearer_auth(&driver.token)
            .json(&json!({"driver_bot": driver.bot_id,
                "routing_policy": {"mode": "hybrid", "sender_routes": {&driver.bot_id: [id]}},
                "participants": [{"bot_uuid": driver.bot_id, "role": "driver"},
                    {"bot_uuid": id, "role": "consultant"}]}))).await;
        assert_eq!(receiver.request("chat.inject").await["to_bot"]["provider_bot_ref"], format!("ref-{i}"));
        groups.push(group["id"].as_str().unwrap().to_string());
        bots.push(bot);
    }
    for receiver in &receivers { receiver.capture.requests.lock().await.clear(); }
    for (i, receiver) in receivers.iter().enumerate() {
        send(&client, &base, &driver, &groups[i], bots[i]["bot_uuid"].as_str().unwrap()).await;
        let delivered = receiver.request("chat.send").await;
        assert_eq!(delivered["to_bot"]["provider_bot_ref"], format!("ref-{i}"));
        assert_eq!(delivered["to_bot"]["provider_id"], provider_id);
        assert_eq!(delivered["observed_protocol"], protocol);
        if protocol == "1.0" {
            json_response(client.post(format!("{base}/bot/events"))
                .header("X-BCN-Provider-Id", provider_id)
                .bearer_auth(bots[i]["bot_runtime_token"].as_str().unwrap())
                .json(&json!({"run_id": delivered["id"], "state": "final",
                    "message": {"text": "endpoint final"}}))).await;
        }
        // Drain the completed run before the maintenance-only address update below.
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if let Some(frame) = driver.recv_frame_short().await {
                    if frame.to_string().contains("endpoint final") { break; }
                }
            }
        }).await.expect("driver did not receive terminal response");
    }
    // Every request, including context injection, stays with the selected Bot.
    for (i, receiver) in receivers.iter().enumerate() {
        assert!(receiver.capture.requests.lock().await.iter().all(|request|
            request["to_bot"]["provider_bot_ref"] == format!("ref-{i}")));
        receiver.capture.requests.lock().await.clear();
    }
    // Explicit endpoint failure never sends this Bot's request to the shared URL.
    receivers[1].capture.fail.store(true, Ordering::SeqCst);
    send(&client, &base, &driver, &groups[1], bots[1]["bot_uuid"].as_str().unwrap()).await;
    receivers[1].request("chat.send").await;
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(receivers[0].capture.requests.lock().await.is_empty());
    assert!(receivers[2].capture.requests.lock().await.is_empty());
    // Use the completed Bot B to verify new traffic after an endpoint PATCH.
    json_response(client.patch(format!("{bot_base}/ref-2")).bearer_auth(admin)
        .json(&json!({"webhook_url": receivers[0].url}))).await;
    send(&client, &base, &driver, &groups[2], bots[2]["bot_uuid"].as_str().unwrap()).await;
    assert_eq!(receivers[0].request("chat.send").await["to_bot"]["provider_bot_ref"], "ref-2");
    assert!(receivers[2].capture.requests.lock().await.is_empty());
    server.abort();
}

async fn send(client: &reqwest::Client, base: &str, driver: &MockBot, group: &str, target_bot: &str) {
    json_response(client.post(format!("{base}/groups/{group}/chat")).bearer_auth(&driver.token)
        .json(&json!({"from": driver.bot_id, "message": format!("@{target_bot} please review")}))).await;
}
