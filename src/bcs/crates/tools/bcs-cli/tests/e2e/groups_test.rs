//! E2E tests for `bcs-cli list-groups`.

use crate::common::{assert_failure, assert_output_contains, assert_success, TestContext};
use wiremock::{
    matchers::{bearer_token, method, path, query_param},
    Mock, ResponseTemplate,
};

#[tokio::test]
async fn list_groups_uses_authenticated_actor_without_local_bot_uuid() {
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

    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .and(bearer_token(&ctx.session.token))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "20"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
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
    assert_eq!(json["offset"], 0);
    assert_eq!(json["returned"], 1);
    assert_eq!(json["total"], 1);
    assert_eq!(json["has_more"], false);
    assert!(json.get("next_offset").is_none());
    assert!(json.get("next_command").is_none());
}

#[tokio::test]
async fn list_groups_uses_offset_and_returns_readable_next_command() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .and(query_param("offset", "2"))
        .and(query_param("limit", "2"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
            "items": [
                {"group_id": "group-3"},
                {"group_id": "group-4"}
            ],
            "total": 5,
            "offset": 2,
            "limit": 2
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--offset")
        .arg("2")
        .arg("--batch-size")
        .arg("2")
        .output()
        .expect("Failed to execute offset page");

    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["offset"], 2);
    assert_eq!(json["next_offset"], 4);
    assert_eq!(
        json["next_command"],
        "bcs-cli list-groups --offset 4 --batch-size 2"
    );
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
async fn list_groups_rejects_malformed_page_envelope() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    Mock::given(method("GET"))
        .and(path("/groups/my"))
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
    assert_output_contains(&output, "invalid current actor groups response");
}

#[tokio::test]
async fn list_groups_accepts_server_capped_limit() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .and(query_param("offset", "0"))
        .and(query_param("limit", "20"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
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
    assert_eq!(json["next_offset"], 1);
}

#[tokio::test]
async fn list_groups_rejects_mismatched_page_offset() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
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
async fn list_groups_human_output_includes_readable_next_command() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
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
    assert_output_contains(
        &output,
        "Next: bcs-cli list-groups --offset 1 --batch-size 20",
    );
}

#[tokio::test]
async fn list_groups_rejects_removed_continue_flag() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--continue")
        .arg("20")
        .output()
        .expect("Failed to execute list-groups");

    assert_failure(&output, Some(2));
    assert_output_contains(&output, "unexpected argument '--continue'");
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
async fn list_groups_all_collects_remaining_pages_from_offset() {
    let ctx = TestContext::new().await.expect("Failed to create test context");

    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .and(query_param("offset", "1"))
        .and(query_param("limit", "2"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
            "items": [{"group_id": "group-2"}, {"group_id": "group-3"}],
            "total": 4,
            "offset": 1,
            "limit": 2
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;
    Mock::given(method("GET"))
        .and(path("/groups/my"))
        .and(query_param("offset", "3"))
        .and(query_param("limit", "2"))
        .and(query_param("include_session_groups", "false"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "actor_id": "bot-current",
            "items": [{"group_id": "group-4"}],
            "total": 4,
            "offset": 3,
            "limit": 2
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--all")
        .arg("--offset")
        .arg("1")
        .arg("--batch-size")
        .arg("2")
        .output()
        .expect("Failed to execute all-pages listing");
    assert_success(&output);
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["items"].as_array().unwrap().len(), 3);
    assert_eq!(json["offset"], 1);
    assert_eq!(json["returned"], 3);
    assert_eq!(json["total"], 4);
    assert_eq!(json["has_more"], false);
    assert!(json.get("next_offset").is_none());
    assert!(json.get("next_command").is_none());
}
