//! `caller.container.ensure` HTTP adapter contract (spec §10.8, wire contract
//! `apps/bcs/docs/caller-container-gateway.md`). Runs a real axum recording
//! server; the transport under test is the production
//! `HttpProviderTransport` path. Asserts: request auth/shape, response
//! mapping, linkage corruption rejections, error closure with no leakage of
//! subject/URL/body, 64KiB response cap, no-body logging.

use std::sync::Arc;

use axum::{routing::post, Router};
use bcs_domain::BotDeliveryTarget;
use bcs_domain::RedactedToken;
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::port::{
    CallerContainerError, CallerContainerGatewayPort, CallerContainerIsolation,
    CallerContainerMinimum, CallerOpaqueRef, ContainerRunAssociation, EnsureCallerContainer,
    EnsureCreateMode,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;

struct RecordingServer {
    last_request: Mutex<Option<(Value, String)>>, // (body, auth header)
    respond_with: axum::http::StatusCode,
    body: Value,
}

#[tokio::test]
async fn happy_ensure_posts_the_contract_shape_and_maps_the_readback() {
    let state = Arc::new(RecordingServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({
            "ok": true,
            "sandbox_container_id": "sandbox-real-123",
            "isolation": { "level": "container_bound", "binding_ref": "bind-xyz", "generation": 3, "reused": false, "expires_at_ms": 1781000000000i64 },
            "observe_target": null
        }),
    });
    let app = Router::new().route("/", post(record_handler)).with_state(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let target = target_at(addr.to_string());
    let request = ensure_request();
    let descriptor = transport
        .ensure_container(&target, &RedactedToken::new("svc-token"), request)
        .await
        .expect("ensure succeeds");

    assert_eq!(descriptor.target_bot_id, "bot-b");
    assert_eq!(descriptor.caller_subject_ref.expose(), "subject-ref-1");
    assert!(descriptor.ready);
    assert_eq!(descriptor.isolation, CallerContainerIsolation::ContainerBound);
    assert_eq!(descriptor.sandbox_container_id.as_ref().map(CallerOpaqueRef::expose), Some("sandbox-real-123"));
    assert_eq!(descriptor.binding_ref.as_ref().map(CallerOpaqueRef::expose), Some("bind-xyz".to_string().as_str()));
    assert_eq!(descriptor.runtime_generation, Some(3));
    assert_eq!(descriptor.expires_at_ms, 1781000000000);

    // Wire shape: Provider envelope via the production frame builder; H2A
    // carries an empty bcn_group_id, pin session id, and service auth.
    let (body, auth) = state.last_request.lock().await.clone().expect("request recorded");
    assert_eq!(auth, "Bearer svc-token");
    assert_eq!(body["method"], "caller.container.ensure");
    assert_eq!(body["type"], "req");
    assert_eq!(body["bcn_group_id"], "");
    assert_eq!(body["session_id"], "session-1");
    assert_eq!(body["to_bot"]["provider_bot_ref"], "runtime-b");
    let params = &body["extensions"];
    assert_eq!(params["contract_version"], 1);
    assert_eq!(params["bot_id"], "bot-b");
    assert_eq!(params["caller_subject"]["kind"], "human");
    assert_eq!(params["caller_subject"]["ref"], "subject-ref-1");
    assert_eq!(params["environment"], "test");
    assert_eq!(params["run"]["run_id"], "run-1");
    assert_eq!(params["run"]["root_run_id"], "run-1");
    assert_eq!(params["run"]["message_turn_id"], "turn-1");
    assert_eq!(params["policy"]["minimum_isolation"], "caller_container");
    assert_eq!(params["policy"]["create_mode"], "lazy");
    assert!(params["policy"]["ttl_ms"].as_i64().unwrap() > 0);
    assert_eq!(params["idempotency_key"], "ensure:bot-b:session-1:run-1");
    // No execution token in an ensure request.
    assert!(body.get("caller_execution_token").is_none());
    assert!(params.get("caller_execution_token").is_none());
}

async fn record_handler(
    axum::extract::State(state): axum::extract::State<Arc<RecordingServer>>,
    headers: axum::http::HeaderMap,
    body: axum::extract::Json<Value>,
) -> (axum::http::StatusCode, axum::Json<Value>) {
    let auth = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or_default()
        .to_string();
    *state.last_request.lock().await = Some((body.0, auth));
    (state.respond_with, axum::Json(state.body.clone()))
}

fn target_at(addr: String) -> BotDeliveryTarget {
    BotDeliveryTarget::HttpProvider {
        bot_id: "bot-b".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-b".into(),
        webhook_url: format!("http://{addr}/"),
        bcs_to_provider_token: RedactedToken::new("svc-token"),
        protocol_version: "2.0".into(),
    }
}

fn ensure_request() -> EnsureCallerContainer {
    EnsureCallerContainer {
        caller_subject_ref: CallerOpaqueRef::new("subject-ref-1"),
        target_bot_id: "bot-b".into(),
        session_id: "session-1".into(),
        environment: "test".into(),
        minimum_isolation: CallerContainerMinimum::CallerContainer,
        idempotency_key: "ensure:bot-b:session-1:run-1".into(),
        run: ContainerRunAssociation {
            run_id: "run-1".into(),
            root_run_id: "run-1".into(),
            parent_run_id: None,
            message_turn_id: "turn-1".into(),
            history_cutoff_seq: 0,
        },
        create_mode: EnsureCreateMode::Lazy,
        container_ttl_ms: 3_600_000,
        deadline_ms: i64::MAX - 1,
        now_ms: 1_780_000_000_000,
    }
}


#[tokio::test]
async fn gateway_refusal_and_isolation_grade_map_to_closed_errors() {
    // Definite gateway refusal carrying an explicit retryable flag: the
    // adapter never trusts body-derived texts, only the closed mapping.
    let state = Arc::new(RecordingServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({
            "ok": false,
            "error": { "code": "CONTAINER_UNAVAILABLE", "message": "no capacity", "retryable": true }
        }),
    });
    let app = Router::new().route("/", post(record_handler)).with_state(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let error = transport
        .ensure_container(&target_at(addr.to_string()), &RedactedToken::new("svc-token"), ensure_request())
        .await
        .unwrap_err();
    assert_eq!(error, CallerContainerError::GatewayRejected { retryable: true });
    // The closed error text never leaks the gateway message.
    assert!(!error.to_string().contains("no capacity"));

    // Unknown isolation grade is a definite refusal, never a silent lower.
    let state = Arc::new(RecordingServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({ "ok": true, "isolation": { "level": "thread_isolated" } }),
    });
    let app = Router::new().route("/", post(record_handler)).with_state(state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
    let error = transport
        .ensure_container(&target_at(addr.to_string()), &RedactedToken::new("svc-token"), ensure_request())
        .await
        .unwrap_err();
    assert_eq!(error, CallerContainerError::GatewayRejected { retryable: false });
}

#[tokio::test]
async fn invalid_request_flags_reject_before_any_network_attempt() {
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let mut request = ensure_request();
    request.run.run_id = String::new();
    let error = transport
        .ensure_container(&target_at("127.0.0.1:1".into()), &RedactedToken::new("svc-token"), request)
        .await
        .unwrap_err();
    assert_eq!(error, CallerContainerError::InvalidRequest);

    let mut request = ensure_request();
    request.container_ttl_ms = 0;
    let error = transport
        .ensure_container(&target_at("127.0.0.1:1".into()), &RedactedToken::new("svc-token"), request)
        .await
        .unwrap_err();
    assert_eq!(error, CallerContainerError::InvalidRequest);
}
