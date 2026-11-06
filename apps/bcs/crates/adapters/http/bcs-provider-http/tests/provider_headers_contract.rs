//! Common BCN headers must have the same semantics on ordinary and protected
//! downlinks. Record real HTTP requests, including repeated business commands.
use std::{collections::HashSet, sync::Arc};

use axum::{extract::State, http::HeaderMap, routing::post, Json, Router};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::{BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, ProviderTransportPreference};
use bcs_service_api::port::{
    CallerContainerGatewayPort, CallerContainerMinimum, CallerDispatchAck,
    CallerDispatchAssociation, CallerExecutionSendPort, CallerOpaqueRef, ContainerRunAssociation,
    EnsureCallerContainer, EnsureCreateMode, HistoryBackfillPort, HistoryCompleteCommand,
    ObserveInjectCommand, ObserveInjectPort, SendCallerExecutionCommand,
};
use serde_json::{json, Value};
use tokio::sync::Mutex;

const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
type Requests = Arc<Mutex<Vec<(HeaderMap, Value)>>>;

#[derive(Clone, Copy, Debug)]
enum Downlink {
    Ordinary,
    CallerSend,
    Ensure,
    Observe,
    HistoryMessage,
    HistoryComplete,
}

async fn record(State(requests): State<Requests>, headers: HeaderMap, Json(body): Json<Value>) -> Json<Value> {
    let response = match body["method"].as_str().unwrap() {
        "caller.container.ensure" => json!({"ok": true, "isolation": {"level": "container_bound"}}),
        "chat.inject" if body["extensions"]["kind"] == "history_complete" => json!({
            "ok": true,
            "history_ready": {"history_snapshot_id": "snapshot-1", "manifest_digest": DIGEST},
        }),
        _ => json!({"ok": true}),
    };
    requests.lock().await.push((headers, body));
    Json(response)
}

async fn assert_common_headers(downlink: Downlink) {
    let requests: Requests = Arc::new(Mutex::new(Vec::new()));
    let app = Router::new().route("/webhook", post(record)).with_state(requests.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/webhook", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let mut message_ids = HashSet::new();

    for version in ["1.0", "2.0"] {
        let target = BotDeliveryTarget::HttpProvider {
            bot_id: "bot-b".into(), provider_id: "provider-a".into(),
            provider_bot_ref: "runtime-b".into(), webhook_url: url.clone(),
            bcs_to_provider_token: RedactedToken::new("provider-test"),
            protocol_version: version.into(),
        };
        // Replaying the same business command must not reuse its HTTP tracking ID.
        for _ in 0..2 {
            let before = bcs_protocol::now_ms();
            send(&transport, &target, downlink).await;
            let after = bcs_protocol::now_ms();
            let mut recorded = requests.lock().await;
            assert_eq!(recorded.len(), 1, "exactly one HTTP attempt per invocation");
            let (headers, body) = recorded.pop().unwrap();
            for name in ["x-bcn-protocol-version", "x-bcn-message-id", "x-bcn-timestamp"] {
                assert_eq!(headers.get_all(name).iter().count(), 1, "{downlink:?} {version}: {name}");
            }
            assert_eq!(headers["x-bcn-protocol-version"], version);
            let raw_id = headers["x-bcn-message-id"].to_str().unwrap();
            let message_id = uuid::Uuid::parse_str(raw_id).expect("request ID is a UUID");
            assert_eq!(message_id.get_version(), Some(uuid::Version::Random));
            assert_eq!(message_id.get_variant(), uuid::Variant::RFC4122);
            assert_eq!(message_id.to_string(), raw_id);
            assert!(message_ids.insert(message_id), "every HTTP request needs a fresh ID");
            assert_ne!(body["id"].as_str().unwrap(), raw_id, "HTTP ID is not the business frame ID");
            let timestamp: u64 = headers["x-bcn-timestamp"].to_str().unwrap().parse().unwrap();
            assert!((before..=after).contains(&timestamp), "timestamp must be current Unix milliseconds");
            assert_eq!(headers["authorization"], "Bearer provider-test");
            assert_eq!(headers["content-type"], "application/json; charset=utf-8");
            match downlink {
                Downlink::Ordinary | Downlink::CallerSend => {
                    assert_eq!(body["method"], "chat.send");
                    assert_eq!(body["id"], "run-1");
                    if matches!(downlink, Downlink::CallerSend) && version == "2.0" {
                        assert_eq!(headers["accept"], "text/event-stream, application/json");
                        assert_eq!(headers["x-bcn-transport"], "sse");
                    } else {
                        assert_eq!(headers["accept"], "application/json");
                    }
                }
                Downlink::Ensure => {
                    assert_eq!(body["method"], "caller.container.ensure");
                    assert_eq!(body["extensions"]["idempotency_key"], "ensure-1");
                }
                Downlink::Observe | Downlink::HistoryMessage => {
                    assert_eq!(body["method"], "chat.inject");
                    assert_eq!(body["extensions"]["message_dedup_key"], "message-1");
                }
                Downlink::HistoryComplete => {
                    assert_eq!(body["method"], "chat.inject");
                    assert_eq!(body["extensions"]["message_dedup_key"], "history_complete:snapshot-1");
                }
            }
        }
    }
    server.abort();
}

async fn send(transport: &HttpProviderTransport, target: &BotDeliveryTarget, downlink: Downlink) {
    let service_credential = RedactedToken::new("provider-test");
    match downlink {
        Downlink::Ordinary => {
            let result = transport.deliver(BotDeliveryCommand {
                target: target.clone(), run_id: "run-1".into(),
                frame: BcsFrame::Request(RequestFrame::new("run-1", "chat.send", Some(json!({
                    "bcs_session_id": "session-1", "message": {"text": "hello"},
                })))),
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: ProviderTransportPreference::Callback,
                provider_bypass_headers: vec![],
            }).await.unwrap();
            assert!(result.delivered);
        }
        Downlink::CallerSend => {
            let deadline = bcs_protocol::now_ms() as i64 + 300_000;
            let ack = transport.send_caller_execution(target, &service_credential, SendCallerExecutionCommand {
                association: CallerDispatchAssociation {
                    environment: "test".into(), target_bot_id: "bot-b".into(),
                    provider_id: "provider-a".into(), provider_bot_ref: "runtime-b".into(),
                    session_id: "session-1".into(), run_id: "run-1".into(),
                    attempt: 1, dispatch_id: "dispatch-1".into(),
                },
                group_id: None, text: "hello".into(), caller_task: "test task".into(),
                message_turn_id: "turn-1".into(), root_run_id: "run-1".into(),
                caller_subject_ref: CallerOpaqueRef::new("subject-1"), subject_kind: "human".into(),
                caller_context_expires_at_ms: deadline,
                execution_token: CallerOpaqueRef::new("test-proof"), execution_token_expires_at_ms: deadline,
                proof_format: "opaque".into(), proof_type: "test".into(), control_revision: 0,
                not_after_ms: deadline, request_digest: DIGEST.into(),
                channel_actor_id: "actor-1".into(), channel_actor_name: "Test actor".into(),
                cross_group_lineage: None, callback_timeout_ms: 3_000, now_ms: 1, history: None,
            }).await.unwrap();
            assert!(matches!(ack, CallerDispatchAck::Accepted { .. }));
        }
        Downlink::Ensure => {
            let result = transport.ensure_container(target, &service_credential, EnsureCallerContainer {
                caller_subject_ref: CallerOpaqueRef::new("subject-1"), target_bot_id: "bot-b".into(),
                session_id: "session-1".into(), environment: "test".into(),
                minimum_isolation: CallerContainerMinimum::CallerContainer,
                idempotency_key: "ensure-1".into(),
                run: ContainerRunAssociation {
                    run_id: "run-1".into(), root_run_id: "run-1".into(), parent_run_id: None,
                    message_turn_id: "turn-1".into(), history_cutoff_seq: 0,
                },
                create_mode: EnsureCreateMode::Lazy, container_ttl_ms: 300_000,
                deadline_ms: i64::MAX, now_ms: 1,
            }).await.unwrap();
            assert!(result.ready);
        }
        Downlink::Observe | Downlink::HistoryMessage => {
            let command = ObserveInjectCommand {
                target_bot_id: "bot-b".into(), session_id: "session-1".into(),
                caller_subject: CallerOpaqueRef::new("subject-1"),
                runtime_target_ref: None, runtime_target_epoch: None,
                message_id: "message-1".into(), text: "context".into(), now_ms: 1,
            };
            if matches!(downlink, Downlink::Observe) {
                transport.observe_injector(target.clone()).unwrap()
                    .inject_shared_context(&service_credential, command).await.unwrap();
            } else {
                transport.inject_history_message(target, &service_credential, command).await.unwrap();
            }
        }
        Downlink::HistoryComplete => {
            transport.inject_history_complete(target, &service_credential, HistoryCompleteCommand {
                caller_subject: CallerOpaqueRef::new("subject-1"), session_id: "session-1".into(),
                snapshot_id: "snapshot-1".into(), history_cutoff_seq: 1, projection_version: 1,
                message_ids: vec!["message-1".into()], manifest_digest: DIGEST.into(), now_ms: 1,
            }).await.unwrap();
        }
    }
}

#[tokio::test]
async fn ordinary_downlink_common_headers() {
    assert_common_headers(Downlink::Ordinary).await;
}

#[tokio::test]
async fn caller_send_common_headers() {
    assert_common_headers(Downlink::CallerSend).await;
}

#[tokio::test]
async fn caller_ensure_common_headers() {
    assert_common_headers(Downlink::Ensure).await;
}

#[tokio::test]
async fn caller_observe_common_headers() {
    assert_common_headers(Downlink::Observe).await;
}

#[tokio::test]
async fn caller_history_message_common_headers() {
    assert_common_headers(Downlink::HistoryMessage).await;
}

#[tokio::test]
async fn caller_history_complete_common_headers() {
    assert_common_headers(Downlink::HistoryComplete).await;
}
