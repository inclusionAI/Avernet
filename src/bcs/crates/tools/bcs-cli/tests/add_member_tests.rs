#[allow(dead_code)]
#[path = "e2e/common/mod.rs"]
mod common;

use common::TestContext;
use wiremock::{
    matchers::{bearer_token, method, path},
    Mock, ResponseTemplate,
};

#[tokio::test(flavor = "multi_thread")]
async fn add_group_member_sends_only_bot_uuid_and_prints_resolved_role() {
    let ctx = TestContext::new().await.expect("Failed to create test context");

    Mock::given(method("POST"))
        .and(path("/groups/group-1/members"))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "added": true,
            "session_id": "group-1",
            "member": {
                "bot_uuid": "worker-1",
                "role": "worker"
            }
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("add-member")
        .arg("--group")
        .arg("group-1")
        .arg("--bot-uuid")
        .arg("worker-1")
        .output()
        .expect("Failed to execute add-member");

    assert!(
        output.status.success(),
        "add-member failed with status {:?}: stdout={} stderr={}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("Role: worker"));

    let requests = ctx.mock_server.received_requests().await.unwrap();
    let request = requests
        .iter()
        .find(|request| {
            request.method.as_str() == "POST"
                && request.url.path() == "/groups/group-1/members"
        })
        .expect("add-member request");
    let body: serde_json::Value = serde_json::from_slice(&request.body).unwrap();
    assert_eq!(body, serde_json::json!({"bot_uuid": "worker-1"}));
}
