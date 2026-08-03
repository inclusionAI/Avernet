use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};

use axum::{
    Json, Router,
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::post,
};
use bcs_config_api::BcsFuseConfig;
use bcs_fuse_client::FuseClient;
use bcs_fusion::FuseVisibilitySyncPort;
use bcs_service_api::port::NoopVisibilitySyncPort;
use serde_json::{Value, json};
use tokio::net::TcpListener;

#[derive(Clone, Default)]
struct FakeFuseState {
    requests: Arc<Mutex<Vec<(String, Value)>>>,
    failures_remaining: Arc<AtomicUsize>,
}

impl FakeFuseState {
    fn failing_first(count: usize) -> Self {
        Self {
            requests: Arc::new(Mutex::new(Vec::new())),
            failures_remaining: Arc::new(AtomicUsize::new(count)),
        }
    }
}

async fn capture_sync_request(
    Path(worker_id): Path<String>,
    State(state): State<FakeFuseState>,
    Json(body): Json<Value>,
) -> Response {
    state
        .requests
        .lock()
        .unwrap()
        .push((worker_id.clone(), body));
    if state
        .failures_remaining
        .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |remaining| {
            remaining.checked_sub(1)
        })
        .is_ok()
    {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error": "temporary failure"})),
        )
            .into_response();
    }
    Json(json!({
        "worker_id": worker_id,
        "created": true,
        "runtime_state": "online",
        "profile_id": "contract-profile",
        "profile_activated": true
    }))
    .into_response()
}

#[tokio::test]
async fn conformance_fuse_visibility_sync_port() {
    let state = FakeFuseState::default();
    let app = Router::new()
        .route("/v1/workers/{worker_id}/sync", post(capture_sync_request))
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind fake bcsfuse server");
    let address = listener.local_addr().expect("read fake server address");
    let server = tokio::spawn(async move {
        axum::serve(listener, app)
            .await
            .expect("fake bcsfuse server");
    });

    let client = Arc::new(
        FuseClient::for_test_with_url(format!("http://{address}"))
            .expect("construct test bcsfuse client"),
    );
    let config = BcsFuseConfig {
        sync_max_attempts: 1,
        sync_retry_base_delay_ms: 10,
        profile_id: "contract-profile".to_string(),
        ..BcsFuseConfig::default()
    };
    let temp_dir = tempfile::tempdir().expect("create empty bot context directory");
    let port = FuseVisibilitySyncPort::new(client, config, temp_dir.path().to_path_buf());

    bcs_test_support::contract::port::visibility_sync_port_contract_tests(&port, async {
        let requests = state.requests.lock().unwrap();
        let Some((worker_id, body)) = requests.first() else {
            return false;
        };
        worker_id == "contract-bot"
            && body["type"] == "bot"
            && body["name"] == "Contract Bot"
            && body["description"] == "Visibility sync contract"
            && body["domains"] == json!(["testing"])
            && body["availability"] == "private"
            && body["profile"]["profile_id"] == "contract-profile"
            && body["profile"]["activate"] == true
    })
    .await;

    server.abort();
    let _ = server.await;
}

#[tokio::test]
async fn fuse_visibility_sync_port_uses_configured_retry_count() {
    let state = FakeFuseState::failing_first(2);
    let app = Router::new()
        .route("/v1/workers/{worker_id}/sync", post(capture_sync_request))
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind fake bcsfuse server");
    let address = listener.local_addr().expect("read fake server address");
    let server = tokio::spawn(async move {
        axum::serve(listener, app)
            .await
            .expect("fake bcsfuse server");
    });
    let client = Arc::new(
        FuseClient::for_test_with_url(format!("http://{address}"))
            .expect("construct test bcsfuse client"),
    );
    let config = BcsFuseConfig {
        sync_max_attempts: 3,
        sync_retry_base_delay_ms: 10,
        ..BcsFuseConfig::default()
    };
    let temp_dir = tempfile::tempdir().expect("create empty bot context directory");
    let port = FuseVisibilitySyncPort::new(client, config, temp_dir.path().to_path_buf());

    bcs_test_support::contract::port::visibility_sync_port_contract_tests(&port, async {
        state.requests.lock().unwrap().len() == 3
    })
    .await;

    server.abort();
    let _ = server.await;
}

#[tokio::test]
async fn conformance_noop_visibility_sync_port() {
    bcs_test_support::contract::port::visibility_sync_port_contract_tests(
        &NoopVisibilitySyncPort,
        async { true },
    )
    .await;
}
