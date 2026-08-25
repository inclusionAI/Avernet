#[allow(dead_code)]
#[path = "e2e/common/mod.rs"]
mod common;

use common::{
    TestContext, assert_failure, assert_output_contains, assert_success,
};
use wiremock::{
    matchers::{bearer_token, method, path},
    Mock, ResponseTemplate,
};

#[tokio::test(flavor = "multi_thread")]
async fn create_group_with_manager_sends_manager_worker_roles() {
    let ctx = TestContext::new().await.expect("Failed to create test context");

    Mock::given(method("POST"))
        .and(path("/groups"))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "id": "manager-worker-group",
            "driver_bot": "manager-bot",
            "participants": ["manager-bot", "worker-1", "worker-2"]
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;
    Mock::given(method("GET"))
        .and(path("/groups/manager-worker-group/sessions"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": []
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("create-group")
        .arg("--manager")
        .arg("manager-bot")
        .arg("--participants")
        .arg("worker-1,worker-2")
        .arg("--participant-tag")
        .arg("manager-bot=tenant-a")
        .arg("--participant-tag")
        .arg("worker-1=worker-tag")
        .arg("--participant-tag")
        .arg("manager-bot=scene-review")
        .output()
        .expect("Failed to execute create-group");

    assert_success(&output);
    let requests = ctx.mock_server.received_requests().await.unwrap();
    let request = requests
        .iter()
        .find(|request| request.method.as_str() == "POST" && request.url.path() == "/groups")
        .expect("create-group request");
    let body: serde_json::Value = serde_json::from_slice(&request.body).unwrap();
    assert_eq!(body["driver_bot"], "manager-bot");
    assert_eq!(body["group_strategy"], "manager_worker");
    assert_eq!(
        body["participants"],
        serde_json::json!([
            {
                "bot_uuid": "manager-bot",
                "role": "manager",
                "tags": ["tenant-a", "scene-review"]
            },
            {"bot_uuid": "worker-1", "role": "worker", "tags": ["worker-tag"]},
            {"bot_uuid": "worker-2", "role": "worker"}
        ])
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn create_group_with_driver_preserves_chat_request() {
    let ctx = TestContext::new().await.expect("Failed to create test context");

    Mock::given(method("POST"))
        .and(path("/groups"))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "id": "chat-group",
            "driver_bot": "driver-bot",
            "participants": ["driver-bot", "participant-1"]
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;
    Mock::given(method("GET"))
        .and(path("/groups/chat-group/sessions"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "items": []
        })))
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("create-group")
        .arg("--driver")
        .arg("driver-bot")
        .arg("--participants")
        .arg("participant-1")
        .arg("--participant-tag")
        .arg("driver-bot=tenant-a")
        .arg("--participant-tag")
        .arg("participant-1=reviewer")
        .arg("--participant-tag")
        .arg("driver-bot=scene-review")
        .output()
        .expect("Failed to execute create-group");

    assert_success(&output);
    let requests = ctx.mock_server.received_requests().await.unwrap();
    let request = requests
        .iter()
        .find(|request| request.method.as_str() == "POST" && request.url.path() == "/groups")
        .expect("create-group request");
    let body: serde_json::Value = serde_json::from_slice(&request.body).unwrap();
    assert_eq!(body["driver_bot"], "driver-bot");
    assert!(body.get("group_strategy").is_none());
    assert_eq!(
        body["participants"],
        serde_json::json!([
            {
                "bot_uuid": "driver-bot",
                "role": null,
                "tags": ["tenant-a", "scene-review"]
            },
            {"bot_uuid": "participant-1", "role": null, "tags": ["reviewer"]}
        ])
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn create_group_rejects_malformed_participant_tag_before_request() {
    let ctx = TestContext::new().await.expect("Failed to create test context");

    let output = ctx
        .cmd()
        .arg("create-group")
        .arg("--driver")
        .arg("driver-bot")
        .arg("--participants")
        .arg("participant-1")
        .arg("--participant-tag")
        .arg("missing-separator")
        .output()
        .expect("Failed to execute create-group");

    assert_failure(&output, Some(1));
    assert_output_contains(&output, "BOT_UUID=TAG");
    assert!(ctx.mock_server.received_requests().await.unwrap().is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn create_group_rejects_participant_tag_for_unknown_bot_before_request() {
    let ctx = TestContext::new().await.expect("Failed to create test context");

    let output = ctx
        .cmd()
        .arg("create-group")
        .arg("--driver")
        .arg("driver-bot")
        .arg("--participants")
        .arg("participant-1")
        .arg("--participant-tag")
        .arg("other-bot=tenant-a")
        .output()
        .expect("Failed to execute create-group");

    assert_failure(&output, Some(1));
    assert_output_contains(&output, "other-bot");
    assert_output_contains(&output, "not a group participant");
    assert!(ctx.mock_server.received_requests().await.unwrap().is_empty());
}
