//! E2E tests for `bcs-cli group-context`.
//!
//! These tests exercise the complete CLI-to-HTTP path: clap parsing, request
//! body construction, query-string projection, bearer authentication, and
//! response printing.

use crate::common::{TestContext, assert_failure, assert_output_contains, assert_success};
use std::sync::{Arc, Mutex};
use wiremock::{
    Mock, ResponseTemplate,
    matchers::{bearer_token, method, path, query_param},
};

type BodyCapture = Arc<Mutex<Option<serde_json::Value>>>;

fn new_body_capture() -> BodyCapture {
    Arc::new(Mutex::new(None))
}

async fn mount_group_context(ctx: &TestContext, operation: &str, body: BodyCapture) {
    let operation = operation.to_string();
    let capture = body;
    Mock::given(method("POST"))
        .and(path(format!("/groupcontext/{operation}")))
        .and(query_param("groupId", "game-room-7"))
        .and(bearer_token("e2e-token"))
        .respond_with(move |request: &wiremock::Request| {
            *capture.lock().unwrap() =
                Some(serde_json::from_slice(&request.body).unwrap_or(serde_json::Value::Null));
            ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "status": "ok",
                "operation": operation,
            }))
        })
        .mount(&ctx.mock_server)
        .await;
}

fn response_json(output: &std::process::Output) -> serde_json::Value {
    serde_json::from_slice(&output.stdout).expect("CLI should print one JSON response")
}

#[tokio::test]
async fn group_context_add_sends_full_body_and_context_query() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let capture = new_body_capture();
    mount_group_context(&ctx, "add", capture.clone()).await;

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("add")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--session-id")
        .arg("sess-001")
        .arg("--round-id")
        .arg("round-001")
        .arg("--name")
        .arg("game_rule")
        .arg("--scope-level")
        .arg("round")
        .arg("--content")
        .arg("张三出局")
        .arg("--visible-to")
        .arg(r#"{"userIds":["bot-1"]}"#)
        .arg("--collect-from")
        .arg(r#"{"userIds":["bot-2"]}"#)
        .arg("--valid-from")
        .arg("2026-10-12T14:56:08+08:00")
        .arg("--valid-to")
        .arg("1791797768000")
        .output()
        .expect("Failed to execute group-context add");

    assert_success(&output);
    let response = response_json(&output);
    assert_eq!(response["status"], "ok");
    assert_eq!(response["operation"], "add");

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["name"], "game_rule");
    assert_eq!(body["scope_level"], "round");
    assert_eq!(body["content"], "张三出局");
    assert_eq!(
        body["visible_to"],
        serde_json::json!({"userIds": ["bot-1"]})
    );
    assert_eq!(
        body["collect_from"],
        serde_json::json!({"userIds": ["bot-2"]})
    );
    assert!(body.as_object().unwrap().get("owners").is_none());
    assert_eq!(body["valid_from"], "2026-10-12T14:56:08+08:00");
    assert_eq!(body["valid_to"], 1_791_797_768_000_i64);
}

#[tokio::test]
async fn group_context_update_omits_patch_fields_not_provided() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let capture = new_body_capture();
    let request_capture = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/update"))
        .and(query_param("groupId", "game-room-7"))
        .and(query_param("sessionId", "sess-001"))
        .and(bearer_token("e2e-token"))
        .respond_with(move |request: &wiremock::Request| {
            *request_capture.lock().unwrap() =
                Some(serde_json::from_slice(&request.body).unwrap_or(serde_json::Value::Null));
            ResponseTemplate::new(200).set_body_json(serde_json::json!({"status": "updated"}))
        })
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("update")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--session-id")
        .arg("sess-001")
        .arg("--name")
        .arg("player_state")
        .arg("--scope-level")
        .arg("session")
        .arg("--valid-from")
        .arg("1791797768000")
        .arg("--change-reason")
        .arg("第一轮投票")
        .output()
        .expect("Failed to execute group-context update");

    assert_success(&output);
    assert_eq!(response_json(&output)["status"], "updated");

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["name"], "player_state");
    assert_eq!(body["scope_level"], "session");
    assert_eq!(body["valid_from"], 1_791_797_768_000_i64);
    assert_eq!(body["change_reason"], "第一轮投票");
    assert!(body.as_object().unwrap().get("content").is_none());
    assert!(body.as_object().unwrap().get("visible_to").is_none());
    assert!(body.as_object().unwrap().get("collect_from").is_none());
    assert!(body.as_object().unwrap().get("owners").is_none());
    assert!(body.as_object().unwrap().get("valid_to").is_none());
}

#[tokio::test]
async fn group_context_update_sends_owners_json() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let capture = new_body_capture();
    mount_group_context(&ctx, "update", capture.clone()).await;

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("update")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--name")
        .arg("player_state")
        .arg("--scope-level")
        .arg("group")
        .arg("--content")
        .arg("v2")
        .arg("--owners")
        .arg(r#"{"userIds":["bot-a","bot-b"]}"#)
        .arg("--change-reason")
        .arg("transfer ownership")
        .output()
        .expect("Failed to execute group-context update");

    assert_success(&output);
    assert_eq!(response_json(&output)["operation"], "update");

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(
        body["owners"],
        serde_json::json!({"userIds": ["bot-a", "bot-b"]})
    );
    assert_eq!(body["change_reason"], "transfer ownership");
}

#[tokio::test]
async fn group_context_list_sends_resolved_scope_levels() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let capture = new_body_capture();
    mount_group_context(&ctx, "list", capture.clone()).await;

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("list")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--scope-levels")
        .arg("group,session")
        .output()
        .expect("Failed to execute group-context list");

    assert_success(&output);
    assert_eq!(response_json(&output)["operation"], "list");

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(
        body["scope_levels"],
        serde_json::json!(["group", "session"])
    );
}

#[tokio::test]
async fn group_context_get_accepts_repeated_scope_level_flags() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    let capture = new_body_capture();
    let request_capture = capture.clone();
    Mock::given(method("POST"))
        .and(path("/groupcontext/get"))
        .and(query_param("groupId", "game-room-7"))
        .and(query_param("sessionId", "sess-001"))
        .and(bearer_token("e2e-token"))
        .respond_with(move |request: &wiremock::Request| {
            *request_capture.lock().unwrap() =
                Some(serde_json::from_slice(&request.body).unwrap_or(serde_json::Value::Null));
            ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "items": [{"name": "player_word_1", "content": "香蕉"}]
            }))
        })
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("get")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--session-id")
        .arg("sess-001")
        .arg("--name")
        .arg("player_word_1")
        .arg("--scope-level")
        .arg("session")
        .arg("--scope-level")
        .arg("round")
        .arg("--limit")
        .arg("5")
        .output()
        .expect("Failed to execute group-context get");

    assert_success(&output);
    assert_eq!(response_json(&output)["items"][0]["content"], "香蕉");

    let body = capture.lock().unwrap().take().unwrap();
    assert_eq!(body["name"], "player_word_1");
    assert_eq!(
        body["scope_levels"],
        serde_json::json!(["session", "round"])
    );
    assert_eq!(body["limit"], 5);
}

#[tokio::test]
async fn group_context_rejects_invalid_visible_to_json() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("add")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--name")
        .arg("game_rule")
        .arg("--scope-level")
        .arg("group")
        .arg("--content")
        .arg("rules")
        .arg("--visible-to")
        .arg("[]")
        .arg("--collect-from")
        .arg(r#"{"userIds":[]}"#)
        .output()
        .expect("Failed to execute invalid group-context add");

    assert_failure(&output, None);
    assert_output_contains(&output, "--visible-to must be a JSON object");
}

#[tokio::test]
async fn group_context_rejects_both_scope_level_flag_forms() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("list")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--scope-level")
        .arg("group")
        .arg("--scope-levels")
        .arg("session")
        .output()
        .expect("Failed to execute conflicting scope-level list");

    assert_failure(&output, None);
    assert_output_contains(
        &output,
        "--scope-level and --scope-levels cannot be used together",
    );
}

#[tokio::test]
async fn group_context_server_error_exits_nonzero() {
    let ctx = TestContext::new()
        .await
        .expect("Failed to create test context");
    Mock::given(method("POST"))
        .and(path("/groupcontext/add"))
        .and(query_param("groupId", "game-room-7"))
        .and(bearer_token("e2e-token"))
        .respond_with(ResponseTemplate::new(409).set_body_json(serde_json::json!({
            "status": "conflict",
            "error_msg": "game_rule already exists"
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("group-context")
        .arg("--auth")
        .arg("e2e-token")
        .arg("add")
        .arg("--group-id")
        .arg("game-room-7")
        .arg("--name")
        .arg("game_rule")
        .arg("--scope-level")
        .arg("group")
        .arg("--content")
        .arg("rules")
        .arg("--collect-from")
        .arg(r#"{"userIds":[]}"#)
        .output()
        .expect("Failed to execute failing group-context add");

    assert_failure(&output, None);
    assert_output_contains(&output, "game_rule already exists");
}
