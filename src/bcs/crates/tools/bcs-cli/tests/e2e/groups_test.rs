//! E2E tests for `bcs-cli list-groups`.

use crate::common::{assert_failure, assert_output_contains, assert_success, TestContext};
use wiremock::{
    matchers::{bearer_token, method, path, query_param},
    Mock, ResponseTemplate,
};

#[tokio::test]
async fn list_groups_uses_current_bot_and_excludes_session_only_groups() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);

    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .and(bearer_token(&ctx.session.token))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "20"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "bot_uuid": ctx.session.bot_uuid,
            "items": [{
                "group_id": "group-for-current-bot",
                "coordinator_bot": "current-bot",
                "participants": [],
                "group_kind": "normal",
                "group_strategy": "chat",
                "visibility": "private"
            }],
            "total": 1,
            "offset": 0,
            "limit": 20
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .output()
        .expect("Failed to execute command");

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["items"][0]["group_id"], "group-for-current-bot");
    assert_eq!(json["returned"], 1);
    assert_eq!(json["total"], 1);
    assert_eq!(json["has_more"], false);
    assert!(json.get("continuation").is_none());
    assert!(json.get("next_command").is_none());
}

#[tokio::test]
async fn list_groups_continues_from_returned_token() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);

    Mock::given(method("GET"))
        .and(path(bot_groups_path.clone()))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "2"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "bot_uuid": ctx.session.bot_uuid,
            "items": [
                {"group_id": "group-1"},
                {"group_id": "group-2"}
            ],
            "total": 3,
            "offset": 0,
            "limit": 2
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let first = ctx
        .cmd()
        .arg("list-groups")
        .arg("--batch-size")
        .arg("2")
        .output()
        .expect("Failed to execute first page");
    assert_success(&first);
    let first_json: serde_json::Value = serde_json::from_slice(&first.stdout).unwrap();
    assert_eq!(first_json["returned"], 2);
    assert_eq!(first_json["has_more"], true);
    let continuation = first_json["continuation"].as_str().unwrap();
    assert!(first_json["next_command"]
        .as_str()
        .unwrap()
        .contains(continuation));

    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .and(query_param("offset", "2"))
        .and(query_param("limit", "1"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "bot_uuid": ctx.session.bot_uuid,
            "items": [{"group_id": "group-3"}],
            "total": 3,
            "offset": 2,
            "limit": 1
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let second = ctx
        .cmd()
        .arg("list-groups")
        .arg("--continue")
        .arg(continuation)
        .arg("--batch-size")
        .arg("1")
        .output()
        .expect("Failed to execute continuation page");
    assert_success(&second);
    let second_json: serde_json::Value = serde_json::from_slice(&second.stdout).unwrap();
    assert_eq!(second_json["items"][0]["group_id"], "group-3");
    assert_eq!(second_json["returned"], 1);
    assert_eq!(second_json["has_more"], false);
    assert!(second_json.get("continuation").is_none());
}

#[tokio::test]
async fn list_groups_rejects_zero_batch_size() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--batch-size")
        .arg("0")
        .output()
        .expect("Failed to execute list-groups");

    assert_failure(&output, None);
    assert_output_contains(&output, "batch size must be greater than 0");
}

#[tokio::test]
async fn list_groups_rejects_invalid_continuation() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--continue")
        .arg("not-a-token")
        .output()
        .expect("Failed to execute list-groups");

    assert_failure(&output, None);
    assert_output_contains(&output, "invalid list-groups continuation token");
}

#[tokio::test]
async fn list_groups_rejects_malformed_page_envelope() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);
    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": []
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .output()
        .expect("Failed to execute list-groups");
    assert_failure(&output, None);
    assert_output_contains(&output, "invalid bot groups response");
}

#[tokio::test]
async fn list_groups_accepts_server_capped_limit() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);
    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "20"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": [{"group_id": "group-1"}],
            "total": 2,
            "offset": 0,
            "limit": 10
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .output()
        .expect("Failed to execute list-groups");
    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["returned"], 1);
    assert_eq!(json["has_more"], true);
    assert!(json["continuation"].is_string());
}

#[tokio::test]
async fn list_groups_rejects_mismatched_page_offset() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);
    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": [],
            "total": 0,
            "offset": 1,
            "limit": 20
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .output()
        .expect("Failed to execute list-groups");
    assert_failure(&output, None);
    assert_output_contains(&output, "requested offset=0");
    assert_output_contains(&output, "received offset=1");
}

#[tokio::test]
async fn list_groups_human_output_includes_next_command() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);
    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": [{"group_id": "group-1"}],
            "total": 2,
            "offset": 0,
            "limit": 20
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("--no-json")
        .arg("list-groups")
        .output()
        .expect("Failed to execute human list-groups");
    assert_success(&output);
    assert_output_contains(&output, "Has more: true");
    assert_output_contains(&output, "Next: bcs-cli list-groups --continue");
}

#[tokio::test]
async fn list_groups_rejects_continuation_for_another_bot() {
    let first_ctx = TestContext::new().await.expect("Failed to create first context");
    let first_path = format!("/bots/{}/groups", first_ctx.session.bot_uuid);
    Mock::given(method("GET"))
        .and(path(first_path))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": [{"group_id": "group-1"}],
            "total": 2,
            "offset": 0,
            "limit": 1
        })))
        .mount(&first_ctx.mock_server)
        .await;
    let first = first_ctx
        .cmd()
        .arg("list-groups")
        .arg("--batch-size")
        .arg("1")
        .output()
        .expect("Failed to create continuation token");
    assert_success(&first);
    let first_json: serde_json::Value = serde_json::from_slice(&first.stdout).unwrap();
    let continuation = first_json["continuation"].as_str().unwrap();

    let second_ctx = TestContext::new().await.expect("Failed to create second context");
    let second = second_ctx
        .cmd()
        .arg("list-groups")
        .arg("--continue")
        .arg(continuation)
        .output()
        .expect("Failed to execute mismatched continuation");
    assert_failure(&second, None);
    assert_output_contains(&second, "continuation token belongs to bot");
}

#[tokio::test]
async fn list_groups_rejects_all_with_continuation() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--all")
        .arg("--continue")
        .arg("token")
        .output()
        .expect("Failed to execute conflicting list-groups options");

    assert_failure(&output, Some(2));
    assert_output_contains(&output, "--all' cannot be used with '--continue");
}

#[tokio::test]
async fn list_groups_rejects_removed_mine_flag() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--mine")
        .output()
        .expect("Failed to execute list-groups");

    assert_failure(&output, Some(2));
    assert_output_contains(&output, "unexpected argument '--mine'");
}

#[tokio::test]
async fn list_groups_all_collects_every_batch() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);

    Mock::given(method("GET"))
        .and(path(bot_groups_path.clone()))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "2"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": [{"group_id": "group-1"}, {"group_id": "group-2"}],
            "total": 3,
            "offset": 0,
            "limit": 2
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;
    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .and(query_param("offset", "2"))
        .and(query_param("limit", "2"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": [{"group_id": "group-3"}],
            "total": 3,
            "offset": 2,
            "limit": 2
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--all")
        .arg("--batch-size")
        .arg("2")
        .output()
        .expect("Failed to execute all-pages listing");
    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["items"].as_array().unwrap().len(), 3);
    assert_eq!(json["returned"], 3);
    assert_eq!(json["total"], 3);
    assert_eq!(json["has_more"], false);
    assert!(json.get("continuation").is_none());
    assert!(json.get("next_command").is_none());
}
