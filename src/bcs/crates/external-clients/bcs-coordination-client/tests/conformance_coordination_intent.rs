use bcs_coordination_client::CoordinationHttpClient;
use bcs_service_api::port::{CoordinationClaim, CoordinationContext, CoordinationIntentPort};
use bcs_test_support::contract::port::coordination_intent::coordination_intent_port_contract_tests;
use reqwest::StatusCode;
use serde_json::{Value, json};
use std::time::{SystemTime, UNIX_EPOCH};
fn now_ms() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64 }

use axum::{Router, Json, routing::{get, post}, extract::State, http::HeaderMap};
use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
const ID: &str = "bcs_intent_0123456789abcdef0123456789abcdef";

struct Store { reads: AtomicUsize, claims: AtomicUsize, finishes: AtomicUsize, lose_claim: bool, mismatch: bool, receipt: std::sync::Mutex<Option<Value>> }
async fn read(State(s): State<Arc<Store>>, headers: HeaderMap) -> (StatusCode, Json<Value>) {
    assert_eq!(headers["authorization"], format!("Bearer {}", "x".repeat(32)));
    if s.reads.fetch_add(1, Ordering::SeqCst) == 0 {
        return (StatusCode::SERVICE_UNAVAILABLE, Json(json!({})));
    }
    (StatusCode::OK, Json(json!({"payload": {"intent_id": ID, "v": 2,
        "tool": if s.mismatch { "bcs_task_complete" } else { "bcs_assign_task" },
        "arguments": {"target_bot": "worker", "message": "中".repeat(4198) + "\n\"🙂"},
        "created_at_ms": now_ms(), "expires_at_ms": now_ms() + 86400000}})))
}
async fn claim(State(s): State<Arc<Store>>, Json(body): Json<Value>) -> (StatusCode, Json<Value>) {
    assert_eq!(body["context"]["bot_id"], "manager");
    let count = s.claims.fetch_add(1, Ordering::SeqCst);
    if s.lose_claim { return (StatusCode::SERVICE_UNAVAILABLE, Json(json!({}))); }
    (StatusCode::OK, Json(json!({"acquired": count == 0,
        "claim_token": if count == 0 { Some("a".repeat(32)) } else { None }, "result": s.receipt.lock().unwrap().clone()})))
}
async fn finish(State(s): State<Arc<Store>>, Json(body): Json<Value>) -> (StatusCode, Json<Value>) {
    assert_eq!(body["result"]["task_id"], "task-real");
    if s.finishes.fetch_add(1, Ordering::SeqCst) == 0 {
        return (StatusCode::SERVICE_UNAVAILABLE, Json(json!({})));
    }
    *s.receipt.lock().unwrap() = Some(body["result"].clone());
    (StatusCode::OK, Json(body["result"].clone()))
}
async fn fixture(lose_claim: bool, mismatch: bool) -> (CoordinationHttpClient, Arc<Store>, tokio::task::JoinHandle<()>) {
    let state = Arc::new(Store { reads: AtomicUsize::new(0), claims: AtomicUsize::new(0),
        finishes: AtomicUsize::new(0), lose_claim, mismatch, receipt: std::sync::Mutex::new(None) });
    let base = format!("/internal/bcs-coordination/v1/intents/{ID}");
    let router = Router::new().route(&base, get(read)).route(&format!("{base}/claim"), post(claim))
        .route(&format!("{base}/finish"), post(finish)).with_state(state.clone());
    let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", socket.local_addr().unwrap());
    let handle = tokio::spawn(async move { axum::serve(socket, router).await.unwrap() });
    (CoordinationHttpClient::new(&url, "x".repeat(32)).unwrap(), state, handle)
}
fn context() -> CoordinationContext {
    CoordinationContext { bot_id: "manager".into(), group_id: "group".into(), session_id: None,
        run_id: "run".into(), tool_call_id: "tool".into() }
}
#[tokio::test]
async fn long_payload_read_retry_claim_once_and_finish_retry() {
    let (client, state, server) = fixture(false, false).await;
    coordination_intent_port_contract_tests(&client, ID, "bcs_assign_task", &context(), now_ms()+10000).await;
    assert_eq!(state.reads.load(Ordering::SeqCst), 3);
    assert_eq!(state.claims.load(Ordering::SeqCst), 2);
    assert_eq!(state.finishes.load(Ordering::SeqCst), 2);
    assert!(matches!(client.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.unwrap(), CoordinationClaim::Duplicate(Some(_))));
    server.abort();
}
#[tokio::test]
async fn lost_claim_ack_is_never_retried_as_execution() {
    let (client, state, server) = fixture(true, false).await;
    assert!(client.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.is_err());
    assert_eq!(state.claims.load(Ordering::SeqCst), 1);
    assert_eq!(state.reads.load(Ordering::SeqCst), 3);
    server.abort();
}
#[tokio::test]
async fn payload_tool_mismatch_and_expired_run_never_claim() {
    let (client, state, server) = fixture(false, true).await;
    assert!(client.resolve_and_claim(ID, "bcs_assign_task", &context(), now_ms()+10000).await.is_err());
    assert_eq!(state.claims.load(Ordering::SeqCst), 0);
    assert!(client.resolve_and_claim(ID, "bcs_assign_task", &context(), 1).await.is_err());
    assert_eq!(state.reads.load(Ordering::SeqCst), 2);
    server.abort();
}
