//! E2E tests for `bcs-cli list` command

use crate::common::{assert_failure, assert_output_contains, assert_success, TestContext};

/// Test successful listing of bots
#[tokio::test]
async fn test_list_bots_success() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    
    // Setup mock response with sample bots
    // BotInfo structure expected by client
    let mock_bots = vec![
        serde_json::json!({
            "bot_uuid": "bot-111",
            "bot_name": "TestBot1",
            "engine_type": "open_claw",
            "capabilities": {
                "name": "TestBot1",
                "summary": "A test bot",
                "domains": ["testing"]
            }
        }),
        serde_json::json!({
            "bot_uuid": "bot-222",
            "bot_name": "TestBot2", 
            "engine_type": "open_claw",
            "capabilities": {
                "name": "TestBot2",
                "summary": "Another test bot",
                "domains": ["testing"]
            }
        }),
    ];
    
    ctx.mock_list_bots(mock_bots).await;
    
    // Execute the list command
    let output = ctx.cmd().arg("list").output().expect("Failed to execute command");
    
    // Assert success and verify output contains bot names
    assert_success(&output);
    assert_output_contains(&output, "TestBot1");
    assert_output_contains(&output, "TestBot2");
}

/// Test list with empty results
#[tokio::test]
async fn test_list_bots_empty() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    
    ctx.mock_list_bots(vec![]).await;
    
    let output = ctx.cmd().arg("list").output().expect("Failed to execute command");
    
    assert_success(&output);
}

/// Test list with server error (500)
#[tokio::test]
async fn test_list_bots_server_error() {
    use wiremock::{matchers::{method, path}, Mock, ResponseTemplate};
    
    let ctx = TestContext::new().await.expect("Failed to create test context");
    
    Mock::given(method("GET"))
        .and(path("/bots"))
        .respond_with(ResponseTemplate::new(500).set_body_json(serde_json::json!({
            "error": "Internal Server Error"
        })))
        .mount(&ctx.mock_server)
        .await;
    
    let output = ctx.cmd().arg("list").output().expect("Failed to execute command");
    
    assert_failure(&output, None);
}

/// Test list with timeout - skipped in CI due to long timeout duration
#[tokio::test]
#[ignore = "Timeout test takes 2+ minutes, run manually with --ignored"]
async fn test_list_bots_timeout() {
    use wiremock::{matchers::{method, path}, Mock, ResponseTemplate};
    use std::time::Duration;
    
    let ctx = TestContext::new().await.expect("Failed to create test context");
    
    // Mock with very long delay (longer than client timeout)
    Mock::given(method("GET"))
        .and(path("/bots"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_delay(Duration::from_secs(130)) // Longer than 120s client timeout
        )
        .mount(&ctx.mock_server)
        .await;
    
    let output = ctx.cmd().arg("list").output().expect("Failed to execute command");
    
    assert_failure(&output, None);
}
