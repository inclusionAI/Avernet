//! History-backfill control wire contract (spec §6.8 / §10.10.1, review-V
//! P1-3): the chat.inject `kind=history_complete` CONTROL variant over the
//! real axum-recording production transport. Locks: the request shape (no
//! business message, route-only Caller subject, snapshot facts + snapshot-key
//! dedup), exact snapshot/digest as the history_ready confirmation, non-retryable refusal
//! mapping, and the pre-network structural gates.
use std::sync::Arc;

use axum::{routing::post, Router};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::port::{
    HistoryBackfillError, HistoryBackfillPort, HistoryCompleteCommand,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;

struct AckServer {
    last_request: Mutex<Option<Value>>,
    respond_with: axum::http::StatusCode,
    body: Value,
}

async fn record_handler_fixed(
    axum::extract::State(state): axum::extract::State<Arc<AckServer>>,
    _headers: axum::http::HeaderMap,
    body: axum::extract::Json<Value>,
) -> (axum::http::StatusCode, axum::Json<Value>) {
    *state.last_request.lock().await = Some(body.0);
    (state.respond_with, axum::Json(state.body.clone()))
}

async fn serve(state: Arc<AckServer>) -> String {
    let app = Router::new().route("/webhook", post(record_handler_fixed)).with_state(state);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
    addr.to_string()
}

fn target_at(addr: String) -> BotDeliveryTarget {
    BotDeliveryTarget::HttpProvider {
        bot_id: "bot-b".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-b".into(),
        webhook_url: format!("http://{addr}/webhook"),
        bcs_to_provider_token: RedactedToken::new("svc-token"),
        protocol_version: "2.0".into(),
    }
}

fn command() -> HistoryCompleteCommand {
    HistoryCompleteCommand {
        session_id: "session-1".into(),
        caller_subject: bcs_service_api::port::CallerOpaqueRef::new("employee-a"),
        snapshot_id: "snap-1".into(),
        history_cutoff_seq: 41,
        projection_version: 1,
        message_ids: vec!["m-1".into(), "m-2".into(), "m-3".into()],
        manifest_digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".into(),
        now_ms: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64,
    }
}

#[tokio::test]
async fn a_ready_ack_round_trips_the_snapshot_attestation_wire() {
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({"ok": true, "history_ready": {"history_snapshot_id": "snap-1", "manifest_digest": command().manifest_digest}}),
    });
    let addr = serve(state.clone()).await;
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    transport
        .inject_history_complete(&target_at(addr), &RedactedToken::new("svc-token"), command())
        .await
        .expect("history_ready confirmed");

    let body = state.last_request.lock().await.clone().expect("recorded");
    assert_eq!(body["method"], "chat.inject");
    let zone = &body["extensions"];
    assert_eq!(zone["kind"], "history_complete");
    assert_eq!(zone["execution"]["mode"], "history_complete");
    // No business message or credential — route-only subject and attestation.
    assert!(body.get("message").map_or(true, |m| m.is_null() || m.as_object().is_none_or(|o| o.is_empty())));
    assert_eq!(zone["caller_context"]["subject"]["ref"], "employee-a");
    assert_eq!(body["to_bot"]["provider_bot_ref"], "runtime-b");
    assert!(zone.get("runtime_target").is_none());
    assert_eq!(zone["history"]["history_snapshot_id"], "snap-1");
    assert_eq!(zone["history"]["history_cutoff_seq"], 41);
    assert_eq!(zone["history"]["projection_version"], 1);
    assert_eq!(zone["history"]["message_ids"], json!(["m-1", "m-2", "m-3"]));
    assert_eq!(zone["history"]["manifest_digest"], "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
    assert_eq!(zone["message_dedup_key"], "history_complete:snap-1");
}

#[tokio::test]
async fn a_refusal_keeps_the_redrive_parked_and_never_fabricates_ready() {
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({"ok": false, "retryable": true}),
    });
    let addr = serve(state).await;
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let error = transport
        .inject_history_complete(&target_at(addr), &RedactedToken::new("svc-token"), command())
        .await
        .unwrap_err();
    assert_eq!(error, HistoryBackfillError::Unavailable { retryable: true });

    // A 4xx refusal is non-retryable: one more backoff round never helps.
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::FORBIDDEN,
        body: json!({}),
    });
    let addr = serve(state).await;
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let error = transport
        .inject_history_complete(&target_at(addr), &RedactedToken::new("svc-token"), command())
        .await
        .unwrap_err();
    assert_eq!(error, HistoryBackfillError::Unavailable { retryable: false });
}

#[tokio::test]
async fn malformed_attestations_fail_not_sent_before_any_network_attempt() {
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    for breakage in [
        "subject", "session", "snapshot", "digest", "clock",
    ] {
        let mut broken = command();
        match breakage {
            "subject" => broken.caller_subject = bcs_service_api::port::CallerOpaqueRef::new(" "),
            "session" => broken.session_id = " ".into(),
            "snapshot" => broken.snapshot_id.clear(),
            "digest" => broken.manifest_digest.clear(),
            "clock" => broken.now_ms = 0,
            _ => unreachable!(),
        }
        let error = transport
            .inject_history_complete(
                &target_at("127.0.0.1:1".into()),
                &RedactedToken::new("svc-token"),
                broken,
            )
            .await
            .unwrap_err();
        assert_eq!(error, HistoryBackfillError::NotSent, "breakage {breakage}");
    }
}

#[tokio::test]
async fn ordinary_inject_ack_is_not_history_readiness() {
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({"ok": true}),
    });
    let addr = serve(state).await;
    let result = HttpProviderTransport::allowing_private_networks_for_tests()
        .inject_history_complete(&target_at(addr), &RedactedToken::new("svc-token"), command()).await;
    assert!(result.is_err(), "transport acceptance cannot attest applied history");
}

#[tokio::test]
async fn readiness_for_another_snapshot_or_digest_is_refused() {
    for ready in [
        json!({"history_snapshot_id":"another-snapshot", "manifest_digest":command().manifest_digest}),
        json!({"history_snapshot_id":"snap-1", "manifest_digest":"another-digest"}),
    ] {
        let state = Arc::new(AckServer { last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK,
            body: json!({"ok":true,"history_ready":ready}) });
        let addr = serve(state).await;
        let result = HttpProviderTransport::allowing_private_networks_for_tests()
            .inject_history_complete(&target_at(addr), &RedactedToken::new("svc-token"), command()).await;
        assert_eq!(result, Err(HistoryBackfillError::Unavailable { retryable:false }));
    }
}

#[tokio::test]
async fn history_bodies_use_observe_only_and_route_each_subject_without_credentials() {
    let state = Arc::new(AckServer { last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK,
        body: json!({"ok":true}) });
    let target = target_at(serve(state.clone()).await);
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    for subject in ["employee-a", "employee-b"] {
        transport.inject_history_message(&target, &RedactedToken::new("svc-token"),
            bcs_service_api::port::ObserveInjectCommand {
                target_bot_id: "bot-b".into(), session_id: "session-1".into(),
                caller_subject: bcs_service_api::port::CallerOpaqueRef::new(subject),
                runtime_target_ref: None, runtime_target_epoch: None,
                message_id: "m-1".into(), text: "历史正文".into(), now_ms: command().now_ms,
            }).await.unwrap();
        let body = state.last_request.lock().await.clone().unwrap();
        assert_eq!(body["method"], "chat.inject");
        assert_eq!(body["message"]["text"], "历史正文");
        assert_eq!(body["session_id"], "session-1");
        assert_eq!(body["to_bot"]["provider_bot_ref"], "runtime-b");
        assert_eq!(body["extensions"]["caller_context"], json!({"subject":{"kind":"human","ref":subject}}));
        assert_eq!(body["extensions"]["execution"]["mode"], "observe_only");
        assert_eq!(body["extensions"]["message_dedup_key"], "m-1");
        assert!(body["extensions"].get("caller_execution_token").is_none());
    }
}
