use crate::client::*;

    #[test]
    #[allow(unsafe_code)]
    fn test_default_bcs_url() {
        // SAFETY: This is a test, we're removing an env var in a controlled manner
        unsafe {
            std::env::remove_var("MOLTIS_BCS_URL");
        }
        let client = BcsClient::from_env();
        assert_eq!(client.base_url(), DEFAULT_BCS_URL);
    }

    #[test]
    fn test_custom_bcs_url() {
        let client = BcsClient::new("http://custom:9000");
        assert_eq!(client.base_url(), "http://custom:9000");
    }

    #[test]
    fn test_with_token_and_oauth() {
        let mut headers = HashMap::new();
        headers.insert("X-Auth-Token".to_string(), "oauth-token-123".to_string());
        headers.insert("Cookie".to_string(), "session=abc".to_string());

        let client =
            BcsClient::with_token_and_oauth("http://localhost:21000", "bot-token", headers);
        assert_eq!(client.token(), Some("bot-token"));
        assert!(client.oauth_headers.is_some());
        let oauth = client.oauth_headers.as_ref().unwrap();
        assert_eq!(oauth.get("X-Auth-Token").unwrap(), "oauth-token-123");
    }

    #[test]
    fn test_set_oauth_headers() {
        let mut client = BcsClient::with_token("http://localhost:21000", "bot-token");
        assert!(client.oauth_headers.is_none());

        let mut headers = HashMap::new();
        headers.insert("X-Auth-Token".to_string(), "oauth-val".to_string());
        client.set_oauth_headers(headers);
        assert!(client.oauth_headers.is_some());
    }

    #[test]
    fn test_no_oauth_headers_by_default() {
        let client = BcsClient::new("http://localhost:21000");
        assert!(client.oauth_headers.is_none());

        let client = BcsClient::with_token("http://localhost:21000", "token");
        assert!(client.oauth_headers.is_none());

        let client = BcsClient::with_token_and_cookie("http://localhost:21000", "token", "cookie");
        assert!(client.oauth_headers.is_none());
    }

    #[test]
    fn test_oauth_headers_layout() {
        // Simulate SDK headers: Authorization, User-Agent, starpoint-data2
        let mut oauth_headers = HashMap::new();
        oauth_headers.insert(
            "Authorization".to_string(),
            "Bearer oauth-token".to_string(),
        );
        oauth_headers.insert(
            "User-Agent".to_string(),
            "agentClientSdk process/bcs-cli".to_string(),
        );
        oauth_headers.insert(
            "starpoint-data2".to_string(),
            "device-token-xyz".to_string(),
        );

        let client = BcsClient::with_token_and_oauth(
            "http://localhost:21000",
            "bot-token-abc",
            oauth_headers,
        );

        let req = client.http_client.get("http://localhost:21000/test");
        let req = client.add_headers(req);
        let built = req.build().unwrap();

        // OAuth Authorization should be the primary (for Spanner gateway)
        let auth = built
            .headers()
            .get("authorization")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(auth, "Bearer oauth-token");

        // Bot token should be in X-BCS-Bot-Token (for BCS server)
        let bot_token = built
            .headers()
            .get("x-bcs-bot-token")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(bot_token, "bot-token-abc");

        // starpoint-data2 should be passed through
        let device = built
            .headers()
            .get("starpoint-data2")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(device, "device-token-xyz");

        // User-Agent should NOT be the SDK's custom one
        let ua = built.headers().get("user-agent");
        if let Some(ua_val) = ua {
            assert!(!ua_val.to_str().unwrap().contains("agentClientSdk"));
        }
    }

    #[test]
    fn test_no_oauth_uses_standard_authorization() {
        // Without OAuth, bot token goes to Authorization as usual
        let client = BcsClient::with_token("http://localhost:21000", "bot-token-abc");

        let req = client.http_client.get("http://localhost:21000/test");
        let req = client.add_headers(req);
        let built = req.build().unwrap();

        let auth = built
            .headers()
            .get("authorization")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(auth, "Bearer bot-token-abc");
        assert!(built.headers().get("x-bcs-bot-token").is_none());
    }

    #[test]
    fn test_chat_headers_include_chat_version() {
        let client = BcsClient::new("http://localhost:21000");
        let request = client
            .add_chat_headers(
                client
                    .http_client
                    .get("http://localhost:21000/chat/runs/run-1"),
            )
            .build()
            .unwrap();

        assert_eq!(
            request
                .headers()
                .get(BCS_CHAT_VERSION_HEADER)
                .and_then(|value| value.to_str().ok()),
            Some(BCS_CHAT_VERSION)
        );
    }

    #[test]
    fn test_chat_dispatch_headers_include_configured_traceparent() {
        let mut client = BcsClient::new("http://localhost:21000");
        client
            .set_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
            .unwrap();

        let request = client
            .add_chat_dispatch_headers(
                client
                    .http_client
                    .post("http://localhost:21000/bots/bot-1/chat-async"),
                true,
                1_800_000,
            )
            .build()
            .unwrap();

        assert_eq!(
            request
                .headers()
                .get("traceparent")
                .and_then(|value| value.to_str().ok()),
            Some("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
        );
        assert_eq!(
            request
                .headers()
                .get("x-bcs-client-detach")
                .and_then(|value| value.to_str().ok()),
            Some("true")
        );
        assert_eq!(
            request
                .headers()
                .get("x-bcs-client-wait-timeout-ms")
                .and_then(|value| value.to_str().ok()),
            Some("1800000")
        );
    }

    #[test]
    fn test_status_poll_headers_omit_trace_and_wait_controls() {
        let mut client = BcsClient::new("http://localhost:21000");
        client
            .set_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
            .unwrap();

        let request = client
            .add_chat_headers(
                client
                    .http_client
                    .get("http://localhost:21000/chat/runs/run-1"),
            )
            .build()
            .unwrap();

        assert!(request.headers().get("traceparent").is_none());
        assert!(request.headers().get("x-bcs-client-detach").is_none());
        assert!(
            request
                .headers()
                .get("x-bcs-client-wait-timeout-ms")
                .is_none()
        );
    }

    #[test]
    fn test_set_traceparent_forwards_http_safe_values_without_w3c_semantic_validation() {
        let values = [
            "not-a-traceparent",
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-09",
            "02-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-09-extension",
        ];

        for value in values {
            let mut client = BcsClient::new("http://localhost:21000");
            client.set_traceparent(value).unwrap();
            let request = client
                .add_chat_dispatch_headers(
                    client
                        .http_client
                        .post("http://localhost:21000/bots/bot-1/chat-async"),
                    true,
                    1_800_000,
                )
                .build()
                .unwrap();

            assert_eq!(
                request
                    .headers()
                    .get("traceparent")
                    .and_then(|header| header.to_str().ok()),
                Some(value)
            );
        }
    }

    #[test]
    fn test_set_traceparent_rejects_unsafe_http_header_values() {
        for value in ["bad\ntraceparent", "bad\r\ntraceparent: injected"] {
            let mut client = BcsClient::new("http://localhost:21000");
            assert!(client.set_traceparent(value).is_err(), "accepted {value:?}");
        }
    }

    #[test]
    fn test_chat_version_header_advertises_version_3() {
        assert_eq!(BCS_CHAT_VERSION, "3");
    }

    #[test]
    fn test_non_chat_headers_omit_chat_version() {
        let client = BcsClient::new("http://localhost:21000");
        let request = client
            .add_headers(client.http_client.get("http://localhost:21000/health"))
            .build()
            .unwrap();

        assert!(request.headers().get(BCS_CHAT_VERSION_HEADER).is_none());
    }
