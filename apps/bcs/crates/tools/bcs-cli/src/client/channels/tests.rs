use super::*;
    #[tokio::test]
    async fn list_channel_conversations_by_session_encodes_query_parameters() {
        use wiremock::{
            Mock, MockServer, ResponseTemplate,
            matchers::{method, path, query_param},
        };

        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/channels/conversations/by-session"))
            .and(query_param("bcs_session_id", "group 1:session/1"))
            .and(query_param("channel_type", "dingtalk"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({ "items": [] })),
            )
            .mount(&server)
            .await;

        let client = BcsClient::with_token(server.uri(), "bot-token");
        let response = client
            .list_channel_conversations_by_session("group 1:session/1", "dingtalk")
            .await
            .expect("conversation lookup should succeed");

        assert_eq!(response, serde_json::json!({ "items": [] }));
    }
