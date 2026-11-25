use bcs_protocol::{ProviderWebhookRequest, ProviderWebhookBotRef};
use bcs_domain::BotDeliveryTarget;
use bcs_provider_http::{HttpProviderTransport, install_provider_request_header_policy};
use bcs_service_api::{BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, ProviderTransportPreference, ServiceError};
use bcs_service_api::port::provider_request_headers::{ProviderRequestHeaderContext, ProviderRequestHeaderPolicy};
use serde_json::Value;

struct Policy;
impl ProviderRequestHeaderPolicy for Policy {
    fn headers(&self, context: &ProviderRequestHeaderContext<'_>) -> Vec<(String, RedactedToken)> {
        assert_eq!(context.provider_id, "provider-1");
        assert_eq!(context.provider_bot_ref, "bot-1");
        if context.method != "chat.send" { return vec![]; }
        let Some(credential) = context.extensions.and_then(|e| e.get("caller_execution_token")) else { return vec![]; };
        if credential["type"] != "test-proof" { return vec![]; }
        let Some(token) = credential["token"].as_str().filter(|v| !v.is_empty()) else { return vec![]; };
        vec![(credential["header_name"].as_str().unwrap_or("x-runtime-credential").into(), RedactedToken::new(token))]
    }
}
use bcs_domain::RedactedToken;

#[tokio::test]
async fn runtime_header_policy_preserves_json_and_provider_auth() {
    install_provider_request_header_policy(std::sync::Arc::new(Policy)).unwrap();
    for (method, credential, expected) in [
        ("chat.send", serde_json::json!({"type": "test-proof", "token": "test-value"}), Some("test-value")),
        ("chat.send", serde_json::json!({"type": "agentpass", "token": "proof"}), None),
        ("chat.send", serde_json::json!({"type": "test-proof", "token": ""}), None),
        ("chat.send", Value::Null, None),
        ("chat.abort", serde_json::json!({"type": "test-proof", "token": "not-for-abort"}), None),
    ] {
        for accept_sse in [false, true] {
            let (tx, mut rx) = tokio::sync::mpsc::channel(1);
            let app = axum::Router::new().route("/", axum::routing::post(
                move |headers: axum::http::HeaderMap, axum::Json(body): axum::Json<Value>| {
                    let tx = tx.clone();
                    async move {
                        tx.send((headers, body)).await.unwrap();
                        axum::Json(serde_json::json!({"ok": true}))
                    }
                },
            ));
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
            let target = BotDeliveryTarget::HttpProvider {
                bot_id: "bot-1".into(), provider_id: "provider-1".into(),
                provider_bot_ref: "bot-1".into(), webhook_url: format!("http://{addr}/"),
                bcs_to_provider_token: RedactedToken::new("provider-secret"),
                protocol_version: "2.0".into(),
            };
            let body = ProviderWebhookRequest {
                frame_type: "req".into(), id: "frame-1".into(), method: method.into(),
                params: None, session_id: "session-1".into(), bcn_group_id: "group-1".into(),
                to_bot: ProviderWebhookBotRef {
                    provider_id: "provider-1".into(), provider_bot_ref: "bot-1".into(), tags: vec![],
                },
                from: None, message: None, attachments: vec![], before: None,
                after: None, limit: None, timeout_ms: 1000,
                extensions: Some(serde_json::json!({"caller_execution_token": credential, "other": "keep"})),
            };
            let transport = HttpProviderTransport::allowing_private_networks_for_tests();
            let command = |body: &ProviderWebhookRequest| BotDeliveryCommand {
                a2a_test_user: None,
                target: target.clone(), run_id: body.id.clone(),
                frame: bcs_protocol::BcsFrame::Request(bcs_protocol::RequestFrame::new(
                    &body.id, &body.method, Some(serde_json::json!({
                        "bcs_session_id": body.session_id, "bcs_group_id": body.bcn_group_id,
                        "extensions": body.extensions,
                    })),
                )),
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: if accept_sse { ProviderTransportPreference::SseFirst } else { ProviderTransportPreference::Callback },
                provider_bypass_headers: vec![],
            };
            if expected.is_some() {
                for (key, value) in [("token", "invalid\r\ninjected: value"), ("header_name", "authorization"), ("header_name", "bcn-transport")] {
                    let mut invalid_body = body.clone();
                    invalid_body.extensions.as_mut().unwrap()["caller_execution_token"][key] = serde_json::json!(value);
                    let error = transport.deliver(command(&invalid_body)).await.unwrap_err();
                    assert!(matches!(error, ServiceError::DeliveryNotSent {
                        code: "provider_request_build_failed", retryable: false,
                    }));
                    assert!(rx.try_recv().is_err(), "invalid header must fail before sending");
                }
            }
            let result = transport.deliver(command(&body)).await.unwrap();
            assert!(result.delivered);
            let (headers, wire_body) = rx.recv().await.unwrap();
            assert_eq!(headers.get("x-runtime-credential").map(|value| value.to_str().unwrap()), expected);
            assert_eq!(headers["authorization"], "Bearer provider-secret");
            assert_eq!(wire_body["extensions"], body.extensions.clone().unwrap());
            server.abort();
        }
    }
}
