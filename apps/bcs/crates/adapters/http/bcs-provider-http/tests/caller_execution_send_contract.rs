//! Protected caller chat.send v1 wire contract (spec §10.9; wire doc).
//! Real axum recording server; the transport under test is the production
//! `HttpProviderTransport` protected-send path. Asserts request shape,
//! minimal ACK parsing, local request association, closed error
//! taxonomy, and the runtime-only extension zone carrying the caller
//! material.

use std::sync::Arc;

use axum::{routing::post, Router};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::port::{
    CallerDispatchAck, CallerExecutionSendPort, CallerOpaqueRef, CallerSendError,
    CallerDispatchAssociation, SendCallerExecutionCommand,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}
const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

struct AckServer {
    last_request: Mutex<Option<Value>>,
    respond_with: axum::http::StatusCode,
    body: Value,
}

async fn record_handler(
    axum::extract::State(state): axum::extract::State<Arc<AckServer>>,
    _headers: axum::http::HeaderMap,
    body: axum::extract::Json<Value>,
) -> (axum::http::StatusCode, axum::Json<Value>) {
    *state.last_request.lock().await = Some(body.0);
    (state.respond_with, axum::Json(state.body.clone()))
}

fn default_ack_server() -> Arc<AckServer> {
    Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({"ok": true}),
    })
}

async fn serve(state: Arc<AckServer>) -> String {
    let app = Router::new()
        .route("/webhook", post(record_handler))
        .with_state(state);
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

fn command(now: i64) -> SendCallerExecutionCommand {
    SendCallerExecutionCommand {
        association: CallerDispatchAssociation {
            environment: "test".into(),
            target_bot_id: "bot-b".into(),
            provider_id: "provider-a".into(),
            provider_bot_ref: "runtime-b".into(),
            session_id: "session-1".into(),
            run_id: "run-1".into(),
            attempt: 1,
            dispatch_id: "dispatch-1".into(),
        },
        group_id: None,
        text: "请处理这部分任务".into(),
        caller_task: "root h2a caller task".into(),
        message_turn_id: "turn-1".into(),
        root_run_id: "run-1".into(),
        caller_subject_ref: CallerOpaqueRef::new("subject-1"),
        subject_kind: "human".into(),
        caller_context_expires_at_ms: now + 300_000,
        execution_token: CallerOpaqueRef::new("execution-token-1"),
        // P2-5: the proof legally expires SHORTER than the caller context.
        execution_token_expires_at_ms: now + 285_000,
        proof_format: "jwt".into(),
        proof_type: "jwt".into(),
        control_revision: 0,
        not_after_ms: now + 280_000,
        request_digest: DIGEST.to_string(),
        cross_group_lineage: None,
        callback_timeout_ms: 3_000,
        now_ms: now,
        history: None,
    }
}

#[tokio::test]
async fn accepted_ack_round_trips_and_the_wire_carries_the_protected_zone() {
    let state = default_ack_server();
    let addr = serve(state.clone()).await;
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let t = now();
    let ack = transport
        .send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(t))
        .await
        .expect("ack");
    assert_eq!(
        ack,
        CallerDispatchAck::Accepted {
            control_revision: 0,
            request_digest: DIGEST.to_string()
        }
    );

    let body = state.last_request.lock().await.clone().expect("recorded");
    assert_eq!(body["method"], "chat.send");
    assert_eq!(body["id"], "run-1");
    assert_eq!(body["bcn_group_id"], "");
    assert_eq!(body["session_id"], "session-1");
    assert_eq!(body["to_bot"]["provider_bot_ref"], "runtime-b");
    assert_eq!(body["message"]["text"], "请处理这部分任务");
    let zone = &body["extensions"];
    assert_eq!(zone["execution"], json!({"not_after_ms": t + 280_000}));
    assert_eq!(zone["caller_context"]["subject"]["kind"], "human");
    assert_eq!(zone["caller_context"]["subject"]["ref"], "subject-1");
    assert_eq!(zone["caller_context"]["expires_at_ms"], t as u64 + 300_000);
    assert_eq!(zone["caller_execution_token"]["format"], "jwt");
    assert_eq!(zone["caller_execution_token"]["type"], "jwt");
    assert_eq!(zone["caller_execution_token"]["token"], "execution-token-1");
    // Review-V P2-5: the token zone carries the PROOF's own (shorter)
    // lifetime — never the caller-context window.
    assert_eq!(zone["caller_execution_token"]["expires_at_ms"], t as u64 + 285_000);
    // One source per association field; no parallel Caller dispatch envelope.
    assert!(zone.get("binding").is_none());
    assert!(zone.get("runtime_target").is_none());
    assert!(zone["execution"].get("history").is_none());
    assert_eq!(body["to_bot"]["provider_id"], "provider-a");
    for key in ["provider_id", "provider_bot_ref", "session_id", "run_id", "attempt", "dispatch_id"] {
        assert!(zone["execution"].get(key).is_none(), "duplicate execution field: {key}");
    }
}

#[tokio::test]
async fn rejected_ack_maps_closed_codes_and_only_history_not_ready_retries() {
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({
            "ok": false,
            "error": { "code": "CANCELLED", "retryable": false }
        }),
    });
    let addr = serve(state).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let ack = transport
        .send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now()))
        .await
        .expect("rejection is a completed ack, not an error");
    assert_eq!(
        ack,
        CallerDispatchAck::Rejected { error_code: "CANCELLED".into(), retryable: false }
    );

    // HISTORY_NOT_READY keeps its retryable=true from the response verbatim.
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({
            "ok": false,
            "error": { "code": "HISTORY_NOT_READY", "retryable": true }
        }),
    });
    let addr = serve(state).await;

    let ack = transport
        .send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now()))
        .await
        .expect("history rejection also completes");
    assert_eq!(
        ack,
        CallerDispatchAck::Rejected { error_code: "HISTORY_NOT_READY".into(), retryable: true }
    );
}

#[tokio::test]
async fn legacy_echo_fields_do_not_override_the_in_flight_request() {
    // Legacy response echoes are not authoritative; use the local request facts.
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({
            "ok": true,
            "contract_version": 1,
            "dispatch": {
                "environment": "test",
                "target_bot_id": "bot-OTHER",
                "provider_id": "provider-a",
                "provider_bot_ref": "runtime-b",
                "session_id": "session-1",
                "run_id": "run-1",
                "attempt": 1,
                "dispatch_id": "dispatch-1"
            },
            "control_revision": 0,
            "request_digest": DIGEST,
            "state": { "state": "accepted" }
        }),
    });
    let addr = serve(state).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let ack = transport
        .send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now()))
        .await
        .unwrap();
    assert_eq!(ack, CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() });
}

#[tokio::test]
async fn malformed_commands_fail_not_sent_before_any_network_attempt() {
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let mut broken = command(now());
    broken.association.attempt = 0;
    let error = transport
        .send_caller_execution(&target_at("127.0.0.1:1".into()), &RedactedToken::new("svc-token"), broken)
        .await
        .unwrap_err();
    assert_eq!(error, CallerSendError::NotSent);

    let mut broken = command(now());
    broken.request_digest = "sha256:short".into();
    let error = transport
        .send_caller_execution(&target_at("127.0.0.1:1".into()), &RedactedToken::new("svc-token"), broken)
        .await
        .unwrap_err();
    assert_eq!(error, CallerSendError::NotSent);

    let mut broken = command(now());
    broken.not_after_ms = now();
    let error = transport
        .send_caller_execution(&target_at("127.0.0.1:1".into()), &RedactedToken::new("svc-token"), broken)
        .await
        .unwrap_err();
    assert_eq!(error, CallerSendError::NotSent);

    // Review-V P2-5: freshness is judged at WIRE TIME — an already-expired
    // proof is never shipped even when the command's own entry clock
    // claims the window is still alive.
    let mut stale_proof = command(now());
    stale_proof.execution_token_expires_at_ms = now();
    let error = transport
        .send_caller_execution(&target_at("127.0.0.1:1".into()), &RedactedToken::new("svc-token"), stale_proof)
        .await
        .unwrap_err();
    assert_eq!(error, CallerSendError::NotSent);
}

#[tokio::test]
async fn non_ack_status_is_a_definite_failure_and_state_unknown_enumerates() {
    // HTTP 409 from the Provider: a definite non-ack, 4xx not retryable.
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::CONFLICT,
        body: json!({}),
    });
    let addr = serve(state).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let error = transport
        .send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now()))
        .await
        .unwrap_err();
    assert_eq!(error, CallerSendError::GatewayFailed { retryable: false });

    // state=unknown ACK enumifies (受理不确定) rather than mapping to error.
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None),
        respond_with: axum::http::StatusCode::OK,
        body: json!({
            "ok": true,
            "contract_version": 1,
            "dispatch": {
                "environment": "test",
                "target_bot_id": "bot-b",
                "provider_id": "provider-a",
                "provider_bot_ref": "runtime-b",
                "session_id": "session-1",
                "run_id": "run-1",
                "attempt": 1,
                "dispatch_id": "dispatch-1"
            },
            "control_revision": 0,
            "request_digest": DIGEST,
            "state": { "state": "unknown" }
        }),
    });
    let addr = serve(state).await;

    let ack = transport
        .send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now()))
        .await
        .expect("unknown is an ack state");
    assert_eq!(ack, CallerDispatchAck::Unknown);
}

#[tokio::test]
async fn route_association_mismatch_is_not_sent() {
    let state = default_ack_server();
    let target = target_at(serve(state.clone()).await);
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    for field in ["target_bot_id", "provider_id", "provider_bot_ref"] {
        let mut request = command(now());
        match field {
            "target_bot_id" => request.association.target_bot_id = "other-bot".into(),
            "provider_id" => request.association.provider_id = "other-provider".into(),
            _ => request.association.provider_bot_ref = "other-runtime".into(),
        }
        let result = transport
            .send_caller_execution(&target, &RedactedToken::new("svc-token"), request)
            .await;
        assert!(matches!(result, Err(CallerSendError::NotSent)), "{field}");
    }
    assert!(state.last_request.lock().await.is_none());
}

#[tokio::test]
async fn ack_uses_local_control_facts_without_provider_echo() {
    let state = default_ack_server();
    let target = target_at(serve(state.clone()).await);
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let mut request = command(now());
    request.association.attempt = 2;
    request.association.dispatch_id = "dispatch-2".into();
    request.control_revision = 7;
    let ack = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), request).await.unwrap();
    assert_eq!(ack, CallerDispatchAck::Accepted { control_revision: 7, request_digest: DIGEST.into() });
    let body = state.last_request.lock().await.clone().unwrap();
    assert_eq!(body["id"], "run-1");
    assert!(body["extensions"]["execution"].get("run").is_none());
}

#[tokio::test]
async fn malformed_or_rejected_success_states_are_not_accepted() {
    for body in [json!({}), json!({"ok":"true"}), json!({"ok":true,"state":"rejected"}),
        json!({"ok":true,"state":null}), json!({"ok":true,"state":{}})] {
        let state = Arc::new(AckServer {
            last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK, body,
        });
        let target = target_at(serve(state).await);
        let transport = HttpProviderTransport::allowing_private_networks_for_tests();
        let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), command(now())).await;
        assert!(!matches!(result, Ok(CallerDispatchAck::Accepted { .. })));
    }
}

#[tokio::test]
async fn shared_history_header_reaches_gateway() {
    let state = default_ack_server();
    let addr = serve(state.clone()).await;
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let mut command = command(now());
    command.group_id = Some("group-1".into());
    command.history = Some(bcs_service_api::port::CallerExecutionHistory {
        history_snapshot_id: "snapshot-1".into(), history_cutoff_seq: 42,
        projection_version: 1, manifest_digest: DIGEST.into(),
    });
    transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command)
        .await.unwrap();
    let body = state.last_request.lock().await.clone().unwrap();
    assert_eq!(body["bcn_group_id"], "group-1");
    assert_eq!(body["extensions"]["execution"]["history"], json!({
        "history_snapshot_id": "snapshot-1", "history_cutoff_seq": 42,
        "projection_version": 1, "manifest_digest": DIGEST,
    }));
}

#[tokio::test]
async fn observe_requires_positive_business_ack() {
    use bcs_service_api::port::{ObserveInjectPort, ObserveInjectCommand, ObserveInjectError};
    for body in [json!({"ok": false}), json!({}), json!({"ok": "true"})] {
        let state = Arc::new(AckServer {
            last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK, body,
        });
        let addr = serve(state).await;
        let transport = HttpProviderTransport::allowing_private_networks_for_tests();
        let injector = transport.observe_injector(target_at(addr)).unwrap();
        let result = injector.inject_shared_context(&RedactedToken::new("svc-token"), ObserveInjectCommand {
            caller_subject: CallerOpaqueRef::new("human-alice"),
            target_bot_id: "bot-b".into(), session_id: "session-1".into(),
            runtime_target_ref: Some(CallerOpaqueRef::new("observe-ref")),
            runtime_target_epoch: Some(CallerOpaqueRef::new("epoch-1")),
            message_id: "message-1".into(), text: "shared text".into(), now_ms: now(),
        }).await;
        assert_eq!(result, Err(ObserveInjectError::Unavailable { retryable: false }));
    }
}

#[tokio::test]
async fn observe_status_and_ack_size_have_bounded_failure_semantics() {
    use bcs_service_api::port::{ObserveInjectPort, ObserveInjectCommand, ObserveInjectError};
    use axum::http::StatusCode;
    for (status, body, retryable) in [
        (StatusCode::SERVICE_UNAVAILABLE, json!({"ok": true}), true),
        (StatusCode::FORBIDDEN, json!({"ok": true}), false),
        (StatusCode::OK, json!({"ok": false, "retryable": true, "error": "not ready"}), true),
        (StatusCode::OK, json!({"ok": true, "padding": "x".repeat(65 * 1024)}), false),
    ] {
        let state = Arc::new(AckServer {
            last_request: Mutex::new(None), respond_with: status, body,
        });
        let addr = serve(state).await;
        let transport = HttpProviderTransport::allowing_private_networks_for_tests();
        let injector = transport.observe_injector(target_at(addr)).unwrap();
        let result = injector.inject_shared_context(&RedactedToken::new("svc-token"), ObserveInjectCommand {
            caller_subject: CallerOpaqueRef::new("human-alice"),
            target_bot_id: "bot-b".into(), session_id: "session-1".into(),
            runtime_target_ref: Some(CallerOpaqueRef::new("observe-ref")),
            runtime_target_epoch: Some(CallerOpaqueRef::new("epoch-1")),
            message_id: "message-1".into(), text: "shared text".into(), now_ms: now(),
        }).await;
        assert_eq!(result, Err(ObserveInjectError::Unavailable { retryable }));
    }
}

#[tokio::test]
async fn observe_always_routes_by_subject_with_optional_paired_ensure_references() {
    use bcs_service_api::port::{ObserveInjectPort, ObserveInjectCommand, ObserveInjectError};
    for ensured in [false, true] {
        let state = Arc::new(AckServer {
            last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK,
            body: json!({"ok":true}),
        });
        let addr = serve(state.clone()).await;
        let transport = HttpProviderTransport::allowing_private_networks_for_tests();
        let injector = transport.observe_injector(target_at(addr)).unwrap();
        let command = ObserveInjectCommand {
            caller_subject: CallerOpaqueRef::new("human-bob"),
            target_bot_id: "bot-b".into(), session_id: "session-1".into(),
            runtime_target_ref: ensured.then(|| CallerOpaqueRef::new("ref-bob")),
            runtime_target_epoch: ensured.then(|| CallerOpaqueRef::new("epoch-bob")),
            message_id: "msg-alice".into(), text: "Alice says hello".into(), now_ms: now(),
        };
        injector.inject_shared_context(&RedactedToken::new("svc-token"), command.clone()).await.unwrap();
        let body = state.last_request.lock().await.take().unwrap();
        let ext = &body["extensions"];
        assert_eq!(ext["caller_context"]["subject"]["ref"], "human-bob");
        assert_eq!(ext["execution"]["mode"], "observe_only");
        assert_eq!(ext.get("runtime_target").is_some(), ensured);
        assert_eq!(ext["runtime_target"].get("caller_ref").is_some(), ensured);
        assert_eq!(ext["runtime_target"].get("target_epoch").is_some(), ensured);
        assert_eq!(ext["caller_context"], json!({"subject":{"kind":"human","ref":"human-bob"}}));
        assert!(ext.get("caller_execution_token").is_none());
        assert!(ext["runtime_target"].get("caller_subject").is_none());
        assert!(ext.get("caller_subject").is_none());
        assert_eq!(ext["caller_context"].as_object().unwrap().len(), 1);
        for invalid in 0..3 {
            let mut request = command.clone();
            match invalid {
                0 => request.caller_subject = CallerOpaqueRef::new(" "),
                1 => { request.runtime_target_ref = Some(CallerOpaqueRef::new("ref")); request.runtime_target_epoch = None; },
                _ => request.target_bot_id = "other-bot".into(),
            }
            assert_eq!(injector.inject_shared_context(&RedactedToken::new("svc-token"), request).await, Err(ObserveInjectError::InvalidRequest));
            assert!(state.last_request.lock().await.is_none());
        }
    }
}
