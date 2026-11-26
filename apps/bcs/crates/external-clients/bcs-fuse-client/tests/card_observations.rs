use std::sync::{Arc, Mutex};
use bcs_fuse_client::{BcsFuseConfig, CardResourceType, CardWriteRequest, FuseClient};
use serde_json::{json, Value};
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::TcpListener};
use tracing::instrument::WithSubscriber;
use tracing_subscriber::{layer::SubscriberExt, Layer};

#[derive(Clone, Default)]
struct Output(Arc<Mutex<Vec<u8>>>);
impl std::io::Write for Output {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
}

async fn scenario(method: &str, status: u16, body: &str, timeout: bool) -> (bool, Vec<Value>, String) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = BcsFuseConfig {
        url: format!("http://{}", listener.local_addr().unwrap()), sync_timeout_ms: 100,
        ..Default::default()
    };
    config.set_resolved_authorization("never-log-authorization".into());
    let client = FuseClient::new(&config).unwrap();
    let body = body.to_string();
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut buffer = [0; 8192];
        let _ = socket.read(&mut buffer).await.unwrap();
        if timeout { std::future::pending::<()>().await; }
        let response = format!("HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
        socket.write_all(response.as_bytes()).await.unwrap();
    });
    let output = Output::default();
    let writer = output.clone();
    // Same observation target and INFO threshold as the deployment file sink.
    let subscriber = tracing_subscriber::registry().with(
        tracing_subscriber::fmt::layer().json().with_writer(move || writer.clone())
            .with_filter(tracing_subscriber::filter::Targets::new().with_target("bcs_observation", tracing::Level::INFO))
    );
    let request = CardWriteRequest {
        resource_type: CardResourceType::Agent,
        labels: [("visibility".into(), vec!["private".into()])].into(),
        card: json!({"name":"Summary\nOnly", "description":"never-log-description", "extensions":{"opaque":"never-log-extension"},
            "skills":[{"name":"never-log-skill", "description":"never-log-skill-detail"}]}),
    };
    let success = async {
        if method == "PUT" { client.put_card("bot-log", &request).await.is_ok() }
        else { client.delete_card("bot-log").await.is_ok() }
    }.with_subscriber(subscriber).await;
    server.abort();
    let _ = server.await;
    let text = String::from_utf8(output.0.lock().unwrap().clone()).unwrap();
    let events = text.lines().map(|line| serde_json::from_str::<Value>(line).unwrap()["fields"].clone()).collect();
    (success, events, text)
}

#[tokio::test]
async fn card_http_logs_validated_ack_status_and_redacted_summaries() {
    for (method, status, body, expected, category) in [
        ("PUT", 202, r#"{"saved":true,"card_version":7}"#, true, ""),
        ("DELETE", 202, r#"{"deleted":true}"#, true, ""),
        ("PUT", 503, "never-log-response-body", false, "http_status"),
        ("PUT", 202, r#"{"saved":false,"card_version":7}"#, false, "negative_ack"),
        ("DELETE", 202, r#"{"deleted":false}"#, false, "negative_ack"),
        ("PUT", 202, "never-log-invalid-json", false, "decode"),
        ("DELETE", 404, "never-log-response-body", false, "http_status"),
    ] {
        let (success, logs, text) = scenario(method, status, body, false).await;
        assert_eq!(success, expected);
        assert_eq!(logs.len(), 2, "{text}");
        assert_eq!(logs[0]["event"], "fuse.card.write_started");
        assert_eq!(logs[1]["http_status"], status);
        assert_eq!(logs[1]["resource_id"], "bot-log");
        assert!(logs[1]["duration_ms"].is_number());
        assert!(!text.contains("never-log"));
        if method == "PUT" {
            assert_eq!(logs[0]["name"], "SummaryOnly");
            assert_eq!(logs[0]["skills_count"], 1);
        }
        if expected {
            assert_eq!(logs[1]["event"], "fuse.card.write_succeeded");
            if method == "PUT" { assert_eq!(logs[1]["card_version"], 7); }
        } else {
            assert_eq!(logs[1]["event"], "fuse.card.write_failed");
            assert_eq!(logs[1]["error_kind"], category);
        }
    }
}

#[tokio::test]
async fn timeout_log_does_not_fabricate_http_status() {
    let (success, logs, text) = scenario("PUT", 202, "", true).await;
    assert!(!success);
    assert_eq!(logs.len(), 2, "{text}");
    assert_eq!(logs[1]["error_kind"], "timeout");
    assert!(logs[1].get("http_status").is_none());
}
