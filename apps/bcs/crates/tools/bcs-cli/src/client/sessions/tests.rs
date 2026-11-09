use super::*;
    #[tokio::test]
    async fn create_session_sends_group_context_delivery() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/groups/g-1/sessions"))
            .respond_with(ResponseTemplate::new(201).set_body_json(serde_json::json!({
                "id": "g-1:abcdef12",
                "session_id": "g-1:abcdef12",
            })))
            .mount(&server)
            .await;

        let client = BcsClient::with_token(&server.uri(), "bot-token");
        client
            .create_session("g-1", None, None, None, None, Some("inject"))
            .await
            .expect("create session should succeed");

        let requests = server.received_requests().await.expect("captured requests");
        let req = requests
            .iter()
            .find(|r| r.method.as_str() == "POST" && r.url.path() == "/groups/g-1/sessions")
            .expect("create session POST request");
        let body: serde_json::Value = serde_json::from_slice(&req.body).unwrap();
        assert_eq!(body["group_context_delivery"], "inject");
    }

#[tokio::test]
async fn create_session_omits_context_names_when_copying_all() {
    use wiremock::matchers::{body_json, method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/groups/g-1/sessions"))
        .and(body_json(serde_json::json!({
            "context_from_session_id": "g-1:12345678"
        })))
        .respond_with(ResponseTemplate::new(201).set_body_json(serde_json::json!({
            "id": "g-1:abcdef12"
        })))
        .expect(1)
        .mount(&server)
        .await;

    BcsClient::with_token(&server.uri(), "bot-token")
        .create_session_with_inheritance("g-1", None, None, None, None, None, Some(("g-1:12345678", None)))
        .await
        .expect("create session should succeed");
}
