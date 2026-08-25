#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::collections::BTreeMap;

use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::port::{EventDeliveryDisposition, EventDeliveryPort, EventDeliveryRequest};
use bcs_test_support::contract::port::event_delivery_port_contract_tests;
use bcs_webhook_client::{WebhookClient, WebhookEndpointPolicy};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;

#[tokio::test]
async fn webhook_client_passes_delivery_contract_and_preserves_raw_body() {
    let (endpoint, received) = serve_once(204, &[], Vec::new()).await;
    let client = test_client();
    let request = fixed_request(endpoint);
    event_delivery_port_contract_tests(
        &client,
        request.clone(),
        EventDeliveryDisposition::Succeeded,
    )
    .await;

    let captured = received.await.expect("captured request");
    assert_eq!(captured.body, request.body);
    assert_eq!(captured.method, "POST");
    assert_eq!(
        captured.headers["content-type"],
        "application/json; charset=utf-8"
    );
}

#[tokio::test]
async fn webhook_client_classifies_http_and_retry_after_without_following_redirects() {
    for (status, expected) in [
        (408, EventDeliveryDisposition::Retryable),
        (425, EventDeliveryDisposition::Retryable),
        (500, EventDeliveryDisposition::Retryable),
        (410, EventDeliveryDisposition::DisableSubscription),
        (409, EventDeliveryDisposition::Terminal),
    ] {
        let (endpoint, _) = serve_once(status, &[], Vec::new()).await;
        let response = test_client()
            .deliver(fixed_request(endpoint))
            .await
            .expect("classified HTTP response");
        assert_eq!(response.disposition, expected, "HTTP {status}");
    }

    let (endpoint, _) = serve_once(429, &[("Retry-After", "9999")], Vec::new()).await;
    let response = test_client()
        .with_retry_after_cap_ms(1_000)
        .deliver(fixed_request(endpoint))
        .await
        .expect("classified 429");
    assert_eq!(response.disposition, EventDeliveryDisposition::Retryable);
    assert_eq!(response.retry_after_ms, Some(1_000));

    let (endpoint, _) = serve_once(
        302,
        &[("Location", "http://127.0.0.1:9/must-not-follow")],
        Vec::new(),
    )
    .await;
    let response = test_client()
        .deliver(fixed_request(endpoint))
        .await
        .expect("redirect is a terminal response");
    assert_eq!(response.disposition, EventDeliveryDisposition::Terminal);
    assert_eq!(response.http_status, Some(302));
}

#[tokio::test]
async fn webhook_client_bounds_response_observation_and_never_surfaces_body() {
    let secret_marker = "remote-secret-marker";
    let body = secret_marker.repeat(1_000).into_bytes();
    let (endpoint, _) = serve_once(500, &[], body).await;
    let response = test_client()
        .deliver(fixed_request(endpoint))
        .await
        .expect("large error response classified");
    assert_eq!(response.disposition, EventDeliveryDisposition::Retryable);
    assert!(response.response_bytes_observed <= 4 * 1024);
    assert!(
        !response
            .error_summary
            .as_deref()
            .unwrap_or_default()
            .contains(secret_marker)
    );
}

#[tokio::test]
async fn production_policy_and_strict_guard_reject_unsafe_endpoints_before_io() {
    let production = WebhookClient::production();
    for endpoint in [
        "http://example.com/hook",
        "https://example.com/hook?token=secret",
        "https://example.com/hook#fragment",
        "https://user:password@example.com/hook",
        "https://example.com:8443/hook",
    ] {
        let response = production
            .deliver(fixed_request(endpoint.to_string()))
            .await
            .expect("unsafe configured endpoint is classified");
        assert_eq!(response.disposition, EventDeliveryDisposition::Terminal);
    }

    let strict_http = WebhookClient::new(
        OutboundUrlGuard::strict(),
        WebhookEndpointPolicy::local_test(),
    );
    for endpoint in [
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/hook",
        "http://[fc00::1]/hook",
    ] {
        let response = strict_http
            .deliver(fixed_request(endpoint.to_string()))
            .await
            .expect("private endpoint is classified");
        assert_eq!(response.disposition, EventDeliveryDisposition::Terminal);
        assert_eq!(response.error_category.as_deref(), Some("private_address"));
    }
}

#[tokio::test]
async fn connection_failure_is_retryable_and_does_not_expose_endpoint() {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("reserve local port");
    let address = listener.local_addr().expect("local address");
    drop(listener);
    let endpoint = format!("http://{address}/sensitive-hook-path");
    let response = test_client()
        .deliver(fixed_request(endpoint.clone()))
        .await
        .expect("connection failure classified");
    assert_eq!(response.disposition, EventDeliveryDisposition::Retryable);
    assert!(
        !response
            .error_summary
            .as_deref()
            .unwrap_or_default()
            .contains(&endpoint)
    );
}

fn test_client() -> WebhookClient {
    WebhookClient::new(
        OutboundUrlGuard::allowing_private_networks_for_tests(),
        WebhookEndpointPolicy::local_test(),
    )
}

fn fixed_request(endpoint: String) -> EventDeliveryRequest {
    EventDeliveryRequest {
        endpoint_url: endpoint,
        body: br#"{"event_id":"evt_test"}"#.to_vec(),
        request_timeout_ms: 2_000,
    }
}

#[derive(Debug)]
struct CapturedRequest {
    method: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

async fn serve_once(
    status: u16,
    response_headers: &[(&str, &str)],
    response_body: Vec<u8>,
) -> (String, oneshot::Receiver<CapturedRequest>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind local Webhook server");
    let address = listener.local_addr().expect("local Webhook address");
    let (captured_tx, captured_rx) = oneshot::channel();
    let response_headers = response_headers
        .iter()
        .map(|(name, value)| ((*name).to_string(), (*value).to_string()))
        .collect::<Vec<_>>();
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("accept Webhook request");
        let mut bytes = Vec::new();
        let header_end = loop {
            let mut buffer = [0_u8; 1024];
            let read = stream.read(&mut buffer).await.expect("read request");
            assert!(read > 0, "request closed before headers");
            bytes.extend_from_slice(&buffer[..read]);
            if let Some(position) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
                break position + 4;
            }
        };
        let header_text = String::from_utf8(bytes[..header_end].to_vec()).expect("UTF-8 headers");
        let mut lines = header_text.split("\r\n");
        let request_line = lines.next().expect("request line");
        let method = request_line
            .split_whitespace()
            .next()
            .expect("request method")
            .to_string();
        let mut headers = BTreeMap::new();
        for line in lines.filter(|line| !line.is_empty()) {
            let (name, value) = line.split_once(':').expect("request header");
            headers.insert(name.to_ascii_lowercase(), value.trim().to_string());
        }
        let content_length = headers
            .get("content-length")
            .expect("content length")
            .parse::<usize>()
            .expect("numeric content length");
        while bytes.len() - header_end < content_length {
            let mut buffer = [0_u8; 1024];
            let read = stream.read(&mut buffer).await.expect("read request body");
            assert!(read > 0, "request closed before body");
            bytes.extend_from_slice(&buffer[..read]);
        }
        let body = bytes[header_end..header_end + content_length].to_vec();
        captured_tx
            .send(CapturedRequest {
                method,
                headers,
                body,
            })
            .ok();

        let reason = match status {
            204 => "No Content",
            302 => "Found",
            410 => "Gone",
            429 => "Too Many Requests",
            500 => "Internal Server Error",
            _ => "Test",
        };
        let mut response = format!(
            "HTTP/1.1 {status} {reason}\r\nContent-Length: {}\r\nConnection: close\r\n",
            response_body.len()
        );
        for (name, value) in response_headers {
            response.push_str(&format!("{name}: {value}\r\n"));
        }
        response.push_str("\r\n");
        stream
            .write_all(response.as_bytes())
            .await
            .expect("write response headers");
        stream
            .write_all(&response_body)
            .await
            .expect("write response body");
    });
    (format!("http://{address}/webhook"), captured_rx)
}
