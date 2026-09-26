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
