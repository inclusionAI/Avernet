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
