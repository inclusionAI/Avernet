//! CLI Integration Tests for BCS.
//!
//! These tests verify bcs-cli functionality without Moltis (non-E2E).
//! Token is managed directly by tests via --token argument.
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_cli_test -- --test-threads=1
//! ```

mod e2e_helpers;

use e2e_helpers::{create_temp_dir, run_bcs_cli_no_token, ProcessManager};

// ============================================================================
// CLI Tests (Non-E2E, no Moltis)
// ============================================================================

/// Test: bcs-cli health check
/// Flow: Start BCS → Run health check via CLI
#[tokio::test]
#[ignore]
async fn cli_health_check() {
    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Run health check via bcs-cli (no token needed)
    let output = run_bcs_cli_no_token(&bcs_url, &["health"])
        .expect("Health command failed");

    println!("Health output: {}", output);
    assert!(output.contains("healthy") || output.contains("ok") || output.contains("BCS"),
            "Output should indicate healthy status");
}
