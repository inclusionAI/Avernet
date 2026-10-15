//! BCS HTTP API Integration Tests (Non-E2E).
//!
//! These tests verify BCS HTTP endpoints without Moltis.
//! Tests interact directly with BCS HTTP API.
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_bcs_http_test -- --test-threads=1
//! ```

mod e2e_helpers;

use std::time::Duration;

use e2e_helpers::{create_temp_dir, ProcessManager};

// ============================================================================
// BCS HTTP API Tests (Non-E2E, no Moltis)
// ============================================================================

/// Test: Verify BCS server starts and responds to HTTP requests
/// Flow: Start BCS → Health check via HTTP → Verify response
#[tokio::test]
async fn bcs_http_health() {
    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Verify BCS health via HTTP (no Moltis needed)
    let client = reqwest::Client::new();
    let response = client
        .get(&format!("{}/health", bcs_url))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("Health check failed");

    assert!(response.status().is_success(), "Health check should succeed");
    let body = response.text().await.expect("Failed to read response");
    println!("BCS health response: {}", body);
}

/// Test: HTTP API bots endpoint without caller identity
/// Flow: Start BCS → Call /bots endpoint → Verify unauthorized response
#[tokio::test]
async fn bcs_http_bots_endpoint() {
    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Call /bots endpoint
    let client = reqwest::Client::new();
    let response = client
        .get(&format!("{}/bots", bcs_url))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("Failed to call /bots");

    println!("Bots endpoint status: {}", response.status());
    assert_eq!(response.status(), reqwest::StatusCode::UNAUTHORIZED);
}

/// Test: HTTP API bot detail endpoint without caller identity
/// Flow: Start BCS → Call /bots/{id} endpoint → Verify unauthorized response
#[tokio::test]
async fn bcs_http_bot_detail_endpoint_requires_caller() {
    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Call /bots/{id} endpoint without auth.
    let client = reqwest::Client::new();
    let response = client
        .get(&format!("{}/bots/missing-bot", bcs_url))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("Failed to call /bots/{id}");

    println!("Bot detail endpoint status: {}", response.status());
    assert_eq!(response.status(), reqwest::StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn concurrent_bcs_processes_keep_their_own_ports() {
    let (_first_dir, first_data) = create_temp_dir();
    let (_second_dir, second_data) = create_temp_dir();
    let mut first = ProcessManager::new();
    let mut second = ProcessManager::new();
    let (first_port, second_port) = tokio::join!(
        first.start_bcs(&first_data), second.start_bcs(&second_data),
    );
    let first_port = first_port.expect("First BCS should start");
    let second_port = second_port.expect("Second BCS should start");
    assert_ne!(first_port, second_port);
    first.kill_all();
    let response = reqwest::Client::new()
        .get(format!("http://127.0.0.1:{}/health", second_port))
        .timeout(Duration::from_secs(5)).send().await.unwrap();
    assert!(response.status().is_success());
}

#[tokio::test]
async fn occupied_port_does_not_make_another_bcs_process_ready() {
    let (_first_dir, first_data) = create_temp_dir();
    let (_second_dir, second_data) = create_temp_dir();
    let mut first = ProcessManager::new();
    let mut second = ProcessManager::new();
    let port = first.start_bcs(&first_data).await.expect("First BCS should start");
    let error = second.start_bcs_with_port(&second_data, port).await
        .expect_err("Another process's health response must not establish readiness");
    assert!(error.to_string().contains("exited before readiness"), "{}", error);
    let response = reqwest::Client::new()
        .get(format!("http://127.0.0.1:{}/health", port))
        .timeout(Duration::from_secs(5)).send().await.unwrap();
    assert!(response.status().is_success());
}
