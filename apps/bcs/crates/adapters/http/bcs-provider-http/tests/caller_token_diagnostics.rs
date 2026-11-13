use std::{
    io::Write,
    sync::{Arc, Mutex},
};

use axum::{Json, Router, routing::post};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_provider_http::HttpProviderTransport;
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, ProviderTransportPreference,
};
use serde_json::{Value, json};

#[derive(Clone, Default)]
struct Logs(Arc<Mutex<Vec<u8>>>);

impl Write for Logs {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for Logs {
    type Writer = Self;
    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

fn command(url: String, protocol: &str, method: &str, token_type: &str) -> BotDeliveryCommand {
    BotDeliveryCommand {
        target: BotDeliveryTarget::HttpProvider {
            bot_id: "test-bot".into(),
            provider_id: "test-provider".into(),
            provider_bot_ref: "test-provider-bot".into(),
            webhook_url: url,
            bcs_to_provider_token: RedactedToken::new("test-token"),
            protocol_version: protocol.into(),
        },
        run_id: "diagnostic-run".into(),
        frame: BcsFrame::Request(RequestFrame::new(
            "attempt-id",
            method,
            Some(json!({
                "bcs_session_id": "test-session",
                "bcs_group_id": "test-group",
                "message": {"text": "test"},
                "extensions": {
                    "caller_context": {"subject": {"kind": "human", "ref": "test-user"}},
                    "caller_execution_token": {"type": token_type, "token": TOKEN}
                }
            })),
        )),
        delivery_kind: BotDeliveryKind::Send,
        provider_transport: ProviderTransportPreference::SseFirst,
        provider_bypass_headers: Vec::new(),
    }
}

const TOKEN: &str = "  fake-caller.jwt+/=%\r\n\"中文  ";

#[tokio::test]
async fn diagnostic_is_preproduction_only_and_matches_the_wire_value() {
    let logs = Logs::default();
    let subscriber = tracing_subscriber::fmt()
        .without_time()
        .with_ansi(false)
        .with_writer(logs.clone())
        .with_max_level(tracing::Level::INFO)
        .finish();
    let _guard = tracing::subscriber::set_default(subscriber);
    let received = Arc::new(Mutex::new(Vec::<Value>::new()));
    let capture = received.clone();
    let app = Router::new().route(
        "/webhook",
        post(move |Json(body): Json<Value>| {
            let capture = capture.clone();
            async move {
                capture.lock().unwrap().push(body);
                Json(json!({"ok": true}))
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/webhook", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });

    for protocol in ["1.0", "2.0"] {
        for environment in [
            None,
            Some("pre"),
            Some("prepub"),
            Some("prod"),
            Some("gray"),
            Some("dev"),
            Some("stable"),
            Some("local"),
            Some(""),
            Some("unknown"),
        ] {
            logs.0.lock().unwrap().clear();
            let mut transport = HttpProviderTransport::allowing_private_networks_for_tests();
            if let Some(environment) = environment {
                transport = transport.with_caller_token_diagnostic_environment(environment);
            }
            let result = transport
                .deliver(command(url.clone(), protocol, "chat.send", "iam-token"))
                .await
                .unwrap();
            assert!(result.delivered);
            let wire = received.lock().unwrap().last().unwrap().clone();
            assert_eq!(wire["extensions"]["caller_execution_token"]["token"], TOKEN);
            assert_eq!(wire["id"], "diagnostic-run");
            let output = String::from_utf8(logs.0.lock().unwrap().clone()).unwrap();
            let diagnostic: Vec<_> = output
                .lines()
                .filter(|line| line.contains("TEMP_PREPUB_CALLER_TOKEN"))
                .collect();
            if matches!(environment, Some("pre" | "prepub")) {
                assert_eq!(diagnostic.len(), 1);
                let line = diagnostic[0];
                assert!(line.contains("run_id=diagnostic-run"));
                assert!(line.contains("session_id=test-session"));
                let token_json = line.split("caller_token_json=").nth(1).unwrap();
                assert_eq!(serde_json::from_str::<String>(token_json).unwrap(), TOKEN);
                assert!(line.contains(&format!("token_bytes={}", TOKEN.len())));
            } else {
                assert!(
                    diagnostic.is_empty(),
                    "unexpected credential log for {environment:?}"
                );
                assert!(!output.contains("fake-caller"));
            }
        }
    }

    let transport = HttpProviderTransport::allowing_private_networks_for_tests()
        .with_caller_token_diagnostic_environment("pre");
    for (method, token_type) in [("chat.send", "agentpass"), ("chat.inject", "iam-token")] {
        logs.0.lock().unwrap().clear();
        transport
            .deliver(command(url.clone(), "1.0", method, token_type))
            .await
            .unwrap();
        let output = String::from_utf8(logs.0.lock().unwrap().clone()).unwrap();
        assert!(!output.contains("TEMP_PREPUB_CALLER_TOKEN"));
        assert!(!output.contains("fake-caller"));
    }
    server.abort();
}

#[tokio::test]
async fn blocked_url_does_not_log_a_token_as_a_sent_request() {
    let logs = Logs::default();
    let subscriber = tracing_subscriber::fmt()
        .without_time()
        .with_ansi(false)
        .with_writer(logs.clone())
        .finish();
    let _guard = tracing::subscriber::set_default(subscriber);
    let transport = HttpProviderTransport::new().with_caller_token_diagnostic_environment("pre");
    assert!(
        transport
            .deliver(command(
                "http://127.0.0.1/webhook".into(),
                "1.0",
                "chat.send",
                "iam-token"
            ))
            .await
            .is_err()
    );
    let output = String::from_utf8(logs.0.lock().unwrap().clone()).unwrap();
    assert!(!output.contains("TEMP_PREPUB_CALLER_TOKEN"));
    assert!(!output.contains("fake-caller"));
}
