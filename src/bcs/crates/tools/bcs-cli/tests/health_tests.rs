//! Health command tests with mock server

use assert_cmd::Command;
use predicates::prelude::*;
use std::time::Duration;
use wiremock::{matchers::*, Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn test_health_ok_json() {
    let mock_server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/health"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "status": "ok"
        })))
        .mount(&mock_server)
        .await;

    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--json")
        .arg("--url")
        .arg(mock_server.uri())
        .arg("health");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("\"status\":\"healthy\""));
}

#[tokio::test]
async fn test_health_timeout() {
    let mock_server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/health"))
        .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(30)))
        .mount(&mock_server)
        .await;

    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--json")
        .arg("--url")
        .arg(mock_server.uri())
        .arg("health")
        .timeout(Duration::from_secs(2));

    // Command timed out and was interrupted - this is expected failure
    cmd.assert()
        .failure();
}

#[tokio::test]
async fn test_health_server_error_json() {
    let mock_server = MockServer::start().await;

    // Server returns 500 or non-ok health status
    Mock::given(method("GET"))
        .and(path("/health"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&mock_server)
        .await;

    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--json")
        .arg("--url")
        .arg(mock_server.uri())
        .arg("health");

    cmd.assert()
        .failure()
        .code(1)
        .stdout(predicate::str::contains("\"status\":\"unhealthy\""));
}

// Human (non-JSON) health mode: --no-json drives the human-readable branch in
// main.rs (prints "✓ BCS is healthy at ..." on success). Covers the else arm
// of the structured_mode fork.
#[tokio::test]
async fn test_health_ok_human() {
    let mock_server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/health"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "status": "ok"
        })))
        .mount(&mock_server)
        .await;

    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--no-json")
        .arg("--url")
        .arg(mock_server.uri())
        .arg("health");

    cmd.assert()
        .success()
        .stdout(predicate::str::contains("✓ BCS is healthy at "));
}

// Human mode on a failing server: prints "✓ ... failed" and exits 1. Covers
// the failure println + std::process::exit(1) in the human branch.
#[tokio::test]
async fn test_health_server_error_human() {
    let mock_server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/health"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&mock_server)
        .await;

    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--no-json")
        .arg("--url")
        .arg(mock_server.uri())
        .arg("health");

    cmd.assert()
        .failure()
        .code(1)
        .stdout(predicate::str::contains("✗ BCS health check failed"));
}

// Structured mode with an unreachable BCS endpoint (connection refused):
// health_check() returns Err, which under --json MUST surface as a structured
// JSON "unhealthy" result rather than a raw error/traceback on stderr. Covers
// the Err arm of the structured_mode health fork in main.rs.
#[tokio::test]
async fn test_health_unreachable_json() {
    // Bind a TCP socket then drop it so the port is guaranteed free -> the
    // bcs-cli health request gets a connection-refused Err (no server there).
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let unreachable_url = format!("http://127.0.0.1:{}", port);

    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--json")
        .arg("--url")
        .arg(&unreachable_url)
        .arg("health");
    cmd.assert()
        .failure()
        .code(1)
        .stdout(predicate::str::contains("\"status\":\"unhealthy\""))
        .stdout(predicate::str::contains("BCS health check failed"));
}
