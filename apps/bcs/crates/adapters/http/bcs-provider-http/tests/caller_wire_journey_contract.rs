//! Caller protocol wire journey (P6 离线最近真场, plan §15.65): ONE
//! wire-conformance stub Gateway answers the whole protocol sequence over
//! real HTTP — `caller.container.ensure` → protected `chat.send` →
//! observe-only `chat.inject` — through the SAME production
//! `HttpProviderTransport` instance (one client, one URL-guard policy, one
//! Provider webhook, one Bearer service credential). This is the closest
//! offline integration to the real Gateway: each single-method contract
//! stays in its sibling file; here the SEQUENCE, the shared transport and
//! the per-method extension zones are proven together, with the observe
//! inject carrying only caller_context.subject and NO execution token on the
//! same server that just accepted a protected send carrying them.

use std::sync::Arc;

use axum::{routing::post, Router};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::port::{
    CallerContainerMinimum, CallerContainerGatewayPort, CallerDispatchAck,
    CallerExecutionSendPort, CallerOpaqueRef, ContainerRunAssociation,
    EnsureCallerContainer, EnsureCreateMode, ObserveInjectCommand, ObserveInjectPort,
    SendCallerExecutionCommand, CallerDispatchAssociation,
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

/// One stub Gateway: records every (method, body, bearer) triple and answers
/// each caller-protocol method per the wire contract.
struct StubGateway {
    calls: Mutex<Vec<(String, Value, Option<String>)>>,
}

fn ensure_response(now: i64) -> Value {
    json!({
        "ok": true,
        "isolation": { "level": "container_bound", "binding_ref": "bind-j", "generation": 7, "reused": false, "expires_at_ms": now + 300_000 },
        "observe_target": {
            "caller_ref": "ref-j-observe",
            "bot_id": "bot-b",
            "session_id": "session-1",
            "target_epoch": "epoch-j",
            "expires_at_ms": now + 300_000
        }
    })
}


async fn gateway_handler(
    axum::extract::State(state): axum::extract::State<Arc<StubGateway>>,
    headers: axum::http::HeaderMap,
    body: axum::extract::Json<Value>,
) -> (axum::http::StatusCode, axum::Json<Value>) {
    let method = body.0["method"].as_str().unwrap_or_default().to_string();
    let bearer = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    state
        .calls
        .lock()
        .await
        .push((method.clone(), body.0, bearer));
    match method.as_str() {
        "caller.container.ensure" => (
            axum::http::StatusCode::OK,
            axum::Json(ensure_response(bcs_protocol::now_ms() as i64)),
        ),
        "chat.send" => (axum::http::StatusCode::OK, axum::Json(json!({"ok": true}))),
        "chat.inject" => (axum::http::StatusCode::OK, axum::Json(json!({"ok": true}))),
        other => (
            axum::http::StatusCode::BAD_REQUEST,
            axum::Json(json!({"ok": false, "error": {"code": format!("unsupported {other}"), "retryable": false}})),
        ),
    }
}

#[tokio::test]
async fn the_whole_protocol_journey_rides_one_transport_and_one_webhook() {
    let gateway = Arc::new(StubGateway { calls: Mutex::new(Vec::new()) });
    let app = Router::new()
        .route("/webhook", post(gateway_handler))
        .with_state(gateway.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });

    let target = BotDeliveryTarget::HttpProvider {
        bot_id: "bot-b".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-b".into(),
        webhook_url: format!("http://{addr}/webhook"),
        bcs_to_provider_token: RedactedToken::new("svc-token"),
        protocol_version: "2.0".into(),
    };
    // ONE production transport: the same client and outbound-URL policy the
    // composition root coerces into ensure/send/inject ports.
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();

    // (1) container ensure — the readback carries the observe reference the
    // observe injects will route through on this same Gateway.
    let descriptor = transport
        .ensure_container(
            &target,
            &RedactedToken::new("svc-token"),
            EnsureCallerContainer {
                caller_subject_ref: CallerOpaqueRef::new("subject-1"),
                target_bot_id: "bot-b".into(),
                session_id: "session-1".into(),
                environment: "test".into(),
                minimum_isolation: CallerContainerMinimum::CallerContainer,
                idempotency_key: "ensure-journey".into(),
                run: ContainerRunAssociation {
                    run_id: "run-1".into(),
                    root_run_id: "run-1".into(),
                    parent_run_id: None,
                    message_turn_id: "turn-1".into(),
                    history_cutoff_seq: 0,
                },
                create_mode: EnsureCreateMode::Lazy,
                container_ttl_ms: 300_000,
                deadline_ms: bcs_protocol::now_ms() as i64 + 300_000,
                now_ms: bcs_protocol::now_ms() as i64,
            },
        )
        .await
        .expect("ensure accepted by the stub gateway");
    let observe = descriptor
        .observe_target
        .as_ref()
        .expect("journey gateway grants the observe reference");
    assert_eq!(observe.target_epoch.as_ref().unwrap().expose(), "epoch-j");
    assert_eq!(observe.caller_ref.as_ref().unwrap().expose(), "ref-j-observe");

    // (2) protected chat.send — the same transport, full ACK echo match.
    let command = SendCallerExecutionCommand {
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
        text: "journey text".into(),
        caller_task: "journey task".into(),
        message_turn_id: "turn-1".into(),
        root_run_id: "run-1".into(),
        caller_subject_ref: CallerOpaqueRef::new("subject-1"),
        subject_kind: "human".into(),
        caller_context_expires_at_ms: now() + 300_000,
        execution_token: CallerOpaqueRef::new("execution-token-1"),
        // P2-5: the proof legally expires shorter than the caller context.
        execution_token_expires_at_ms: now() + 285_000,
        proof_format: "jwt".into(),
        proof_type: "jwt".into(),
        control_revision: 0,
        not_after_ms: now() + 280_000,
        request_digest: DIGEST.to_string(),
        cross_group_lineage: None,
        callback_timeout_ms: 3_000,
        now_ms: now(),
        history: None,
    };
    let ack = transport
        .send_caller_execution(&target, &RedactedToken::new("svc-token"), command)
        .await
        .expect("protected send accepted");
    assert_eq!(
        ack,
        CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.to_string() }
    );

    // (3) observe-only inject through the SAME transport's injector — routed
    // by the reference the ensure granted, on the same webhook.
    let injector = transport
        .observe_injector(target.clone())
        .expect("provider target yields the observe injector");

    injector
        .inject_shared_context(
            &RedactedToken::new("svc-token"),
            ObserveInjectCommand {
                caller_subject: observe.caller_subject.clone(),
                target_bot_id: "bot-b".into(),
                session_id: "session-1".into(),
                runtime_target_ref: observe.caller_ref.clone(),
                runtime_target_epoch: observe.target_epoch.clone(),
                message_id: "turn-1".into(),
                text: "journey text".into(),
                now_ms: now(),
            },
        )
        .await
        .expect("observe inject accepted");

    // (4) The recorded sequence: one webhook, three methods, one Bearer
    // service credential, observe-only zone carries only its destination subject.
    let calls = gateway.calls.lock().await;
    assert_eq!(calls.len(), 3, "ensure → protected send → observe inject");
    assert_eq!(calls[0].0, "caller.container.ensure");
    assert_eq!(calls[1].0, "chat.send");
    assert_eq!(calls[2].0, "chat.inject");
    for (_, _, bearer) in calls.iter() {
        assert_eq!(bearer.as_deref(), Some("Bearer svc-token"), "one service credential across the journey");
    }

    let send_zone = calls[1].1["extensions"].clone();
    assert!(send_zone["execution"].get("run").is_none());
    assert_eq!(send_zone["caller_context"]["subject"]["ref"], "subject-1");
    assert_eq!(send_zone["caller_execution_token"]["type"], "jwt");
    assert_eq!(send_zone["caller_execution_token"]["token"], "execution-token-1");

    let inject_request = &calls[2].1;
    assert_eq!(inject_request["message"]["text"], "journey text");
    let observe_zone = &inject_request["extensions"];
    assert_eq!(observe_zone["caller_context"]["subject"]["ref"], "subject-1");
    assert!(send_zone.get("runtime_target").is_none());
    assert_eq!(observe_zone["runtime_target"]["purpose"], "observe_only");
    assert_eq!(
        observe_zone["runtime_target"]["caller_ref"],
        "ref-j-observe",
        "the inject routes by the reference the same journey's ensure granted"
    );
    assert_eq!(observe_zone["runtime_target"]["target_epoch"], "epoch-j");
    assert_eq!(observe_zone["execution"]["mode"], "observe_only");
    assert_eq!(observe_zone["message_dedup_key"], "turn-1");
    assert!(
        inject_request["extensions"].get("caller_execution_token").is_none(),
        "the observe-only zone never carries an execution token"
    );
}