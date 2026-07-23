//! E2E tests for `bcs-cli discover`.

use crate::common::{assert_success, TestContext};
use wiremock::{
    matchers::{method, path},
    Mock, ResponseTemplate,
};

async fn mount_discover_response(ctx: &TestContext) {
    Mock::given(method("GET"))
        .and(path("/bots/discover"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "count": 1,
            "bots": [{
                "bot_uuid": "bot-1",
                "capabilities": {
                    "name": "Helper",
                    "summary": "A test helper"
                },
                "visibility": "public",
                "is_friend": true,
                "agent_code": "agent-code-1",
                "provider_info": {
                    "provider_id": "provider-1",
                    "provider_name": "Provider One"
                }
            }]
        })))
        .mount(&ctx.mock_server)
        .await;
}

#[tokio::test]
async fn discover_defaults_to_json_output() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    mount_discover_response(&ctx).await;

    let output = ctx
        .cmd()
        .arg("discover")
        .output()
        .expect("Failed to execute discover");

    assert_success(&output);
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["count"], 1);
    assert_eq!(value["bots"][0]["bot_uuid"], "bot-1");
    assert_eq!(value["bots"][0]["provider_info"]["provider_id"], "provider-1");
}

#[tokio::test]
async fn discover_no_json_preserves_human_output() {
    let ctx = TestContext::new().await.expect("Failed to create test context");
    mount_discover_response(&ctx).await;

    let output = ctx
        .cmd()
        .arg("discover")
        .arg("--no-json")
        .output()
        .expect("Failed to execute discover --no-json");

    assert_success(&output);
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("Discovered 1 bots:"));
    assert!(stdout.contains("bot-1 (Helper) [public]"));
    assert!(stdout.contains("provider=Provider One/provider-1"));
    assert!(serde_json::from_str::<serde_json::Value>(&stdout).is_err());
}
