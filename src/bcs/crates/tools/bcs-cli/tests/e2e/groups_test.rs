//! E2E tests for `bcs-cli list-groups`.

use crate::common::{assert_output_contains, assert_success, TestContext};
use wiremock::{
    matchers::{bearer_token, method, path},
    Mock, ResponseTemplate,
};

#[tokio::test]
async fn list_groups_mine_uses_current_bot_from_session() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    let bot_groups_path = format!("/bots/{}/groups", ctx.session.bot_uuid);

    Mock::given(method("GET"))
        .and(path(bot_groups_path))
        .and(bearer_token(&ctx.session.token))
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
            "limit": 10
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("list-groups")
        .arg("--mine")
        .output()
        .expect("Failed to execute command");

    assert_success(&output);
    assert_output_contains(&output, "Groups for current bot");
    assert_output_contains(
        &output,
        "group-for-current-bot [chat] driver=current-bot",
    );
    assert!(!String::from_utf8_lossy(&output.stdout).contains("unknown"));
}
