#[allow(dead_code)]
#[path = "e2e/common/mod.rs"]
mod common;

use common::TestContext;
use wiremock::{
    matchers::{bearer_token, method, path},
    Mock, ResponseTemplate,
};

#[tokio::test(flavor = "multi_thread")]
async fn get_group_sends_authenticated_request() {
    let ctx = TestContext::new().await.expect("test context");

    Mock::given(method("GET"))
        .and(path("/groups/group-1"))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "id": "group-1",
            "driver_bot": "driver-bot",
            "participants": ["driver-bot", "worker-1"]
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("get-group")
        .arg("--id")
        .arg("group-1")
        .output()
        .expect("get-group execution");

    assert!(
        output.status.success(),
        "get-group failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("group-1"));
}

#[tokio::test(flavor = "multi_thread")]
async fn fuse_sends_authenticated_request() {
    let ctx = TestContext::new().await.expect("test context");

    Mock::given(method("POST"))
        .and(path("/groups/group-1/fuse"))
        .and(bearer_token(&ctx.session.token))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "recommendation": "release-ready",
            "perspectives": [],
            "conflicts": [],
            "alignment_points": [],
            "key_insights": []
        })))
        .expect(1)
        .mount(&ctx.mock_server)
        .await;

    let output = ctx
        .cmd()
        .arg("fuse")
        .arg("--group")
        .arg("group-1")
        .arg("--question")
        .arg("Is the release ready?")
        .arg("--participants")
        .arg("driver-bot,worker-1")
        .arg("--focus")
        .arg("release risk")
        .output()
        .expect("fuse execution");

    assert!(
        output.status.success(),
        "fuse failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("release-ready"));

    let requests = ctx.mock_server.received_requests().await.unwrap();
    let request = requests
        .iter()
        .find(|request| request.method.as_str() == "POST"
            && request.url.path() == "/groups/group-1/fuse")
        .expect("fuse request");
    let body: serde_json::Value = serde_json::from_slice(&request.body).unwrap();
    assert_eq!(body["question"], "Is the release ready?");
    assert_eq!(
        body["participants"],
        serde_json::json!(["driver-bot", "worker-1"])
    );
    assert_eq!(body["focus"], "release risk");
}
