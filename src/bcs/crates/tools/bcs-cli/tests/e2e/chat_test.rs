//! E2E tests for `bcs-cli chat` output rendering.
//!
//! These exercise the binary-level output contracts from the v4 design spec:
//! - Default (no-flag) mode emits a single JSON object on stdout + ack on stderr
//! - `--no-json` mode emits human text including a `Run:` line on stdout
//!
//! Both paths assert `run_id` + `session_id` are surfaced.

use crate::common::{assert_success, TestContext};
use wiremock::{matchers::method, Mock, ResponseTemplate};

/// Set up the mock for a sync `completed` chat run.
async fn mock_chat_completed(ctx: &TestContext) {
    Mock::given(method("POST"))
        .and(wiremock::matchers::path("/bots/bot-target/chat-async"))
        .respond_with(ResponseTemplate::new(202).set_body_json(serde_json::json!({
            "run_id": "run-1",
            "bot_uuid": "bot-target",
            "session_id": "session-1",
            "status": "submitted",
            "expires_at_ms": 9_999_999_u64,
        })))
        .mount(&ctx.mock_server)
        .await;
    Mock::given(method("GET"))
        .and(wiremock::matchers::path("/chat/runs/run-1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "run_id": "run-1",
            "bot_uuid": "bot-target",
            "from_bot_id": "bot-source",
            "session_id": "session-1",
            "state": "completed",
            "response": {"content": "hello back"},
            "created_at_ms": 1_u64,
            "updated_at_ms": 2_u64,
            "expires_at_ms": 9_999_999_u64,
            "version": 2_u64,
            "content_truncated": false,
            "is_terminal": true,
        })))
        .mount(&ctx.mock_server)
        .await;
}

#[tokio::test]
async fn chat_default_mode_emits_single_json_on_stdout_with_ack_on_stderr() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    // Write session with a token so `get_token` doesn't fail.
    std::fs::write(
        ctx.session_path(),
        serde_json::to_vec(&serde_json::json!({
            "token": ctx.session.token,
            "bcs_url": ctx.session.bcs_url,
        }))
        .unwrap(),
    )
    .unwrap();
    mock_chat_completed(&ctx).await;

    let output = ctx
        .cmd()
        .arg("chat")
        .arg("--bot-uuid")
        .arg("bot-target")
        .arg("--message")
        .arg("hello")
        .arg("--timeout-ms")
        .arg("10000")
        .output()
        .expect("Failed to execute command");

    assert_success(&output);

    // stdout: exactly one JSON object (jq-parseable)
    let stdout = String::from_utf8_lossy(&output.stdout);
    let json: serde_json::Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|err| {
        panic!(
            "stdout should be a single JSON object, got: {:?}\nError: {}",
            stdout, err
        )
    });
    assert_eq!(json["delivered"], serde_json::json!(true));
    assert_eq!(json["submitted"], serde_json::json!(true));
    assert_eq!(json["run_id"], serde_json::json!("run-1"));
    assert_eq!(json["session_id"], serde_json::json!("session-1"));
    assert_eq!(json["state"], serde_json::json!("completed"));
    assert_eq!(json["response"]["content"], serde_json::json!("hello back"));

    // stderr: ack line with run_id + session_id
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("[chat] submitted run_id=run-1 session_id=session-1"),
        "stderr should contain ack line, got: {:?}",
        stderr
    );
}

#[tokio::test]
async fn chat_no_json_mode_prints_run_line_on_stdout() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    std::fs::write(
        ctx.session_path(),
        serde_json::to_vec(&serde_json::json!({
            "token": ctx.session.token,
            "bcs_url": ctx.session.bcs_url,
        }))
        .unwrap(),
    )
    .unwrap();
    mock_chat_completed(&ctx).await;

    let output = ctx
        .cmd()
        .arg("--no-json")
        .arg("chat")
        .arg("--bot-uuid")
        .arg("bot-target")
        .arg("--message")
        .arg("hello")
        .arg("--timeout-ms")
        .arg("10000")
        .output()
        .expect("Failed to execute command");

    assert_success(&output);
    let stdout = String::from_utf8_lossy(&output.stdout);
    // Regression: sync non-json success MUST include `Run:` (issue 1 fix)
    assert!(
        stdout.contains("Run: run-1"),
        "stdout should contain 'Run: run-1', got: {:?}",
        stdout
    );
    assert!(
        stdout.contains("Session: session-1"),
        "stdout should contain 'Session: session-1', got: {:?}",
        stdout
    );
    assert!(
        stdout.contains("State: completed"),
        "stdout should contain 'State: completed', got: {:?}",
        stdout
    );
    // ack also on stdout in non-json mode
    assert!(
        stdout.contains("[chat] submitted run_id=run-1 session_id=session-1"),
        "stdout should contain ack line in non-json mode, got: {:?}",
        stdout
    );
}
