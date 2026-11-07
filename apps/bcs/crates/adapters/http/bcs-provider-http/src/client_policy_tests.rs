use super::*;
    use bcs_domain::RedactedToken;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[test]
    fn provider_message_prepends_opted_in_sender_json_without_mutating_frame_params() {
        let params = serde_json::json!({
            "channel": {
                "identity_forwarding": true,
                "user_id": "410025",
                "actor_id": "human_410025",
                "actor_name": "张\"三"
            },
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "hello" }],
                "timestamp": 1710960000000_u64
            }
        });

        let first = provider_message_for_method("chat.send", &params).unwrap();
        let retry = provider_message_for_method("chat.send", &params).unwrap();
        assert_eq!(first, retry, "retry must rebuild from the unchanged frame");
        assert_eq!(
            first["content"][0]["text"],
            "{\"sender\":{\"id\":\"410025\",\"name\":\"张\\\"三\"}}\n\nhello"
        );
        assert_eq!(params["message"]["content"][0]["text"], "hello");
    }

    #[test]
    fn provider_message_limits_sender_prefix_to_chat_methods() {
        let params = serde_json::json!({
            "channel": {
                "identity_forwarding": true,
                "user_id": "410025",
                "actor_name": "张三"
            },
            "message": {
                "role": "user",
                "content": [{ "type": "text", "text": "hello" }],
                "timestamp": 1710960000000_u64
            }
        });

        let send = provider_message_for_method("chat.send", &params).unwrap();
        let inject = provider_message_for_method("chat.inject", &params).unwrap();
        assert_eq!(send, inject);
        assert_ne!(send, params["message"]);
        assert_eq!(
            provider_message_for_method("chat.history", &params),
            params.get("message").cloned()
        );
        assert_eq!(
            provider_message_for_method("task.dispatch", &params),
            params.get("message").cloned()
        );
    }

    #[test]
    fn provider_message_ignores_legacy_channel_identity_and_supports_content_blocks() {
        let legacy = serde_json::json!({
            "channel": { "user_id": "410025", "actor_name": "张三" },
            "message": { "text": "hello" }
        });
        assert_eq!(
            provider_message_from_params(&legacy),
            legacy.get("message").cloned()
        );

        let opted_in = serde_json::json!({
            "channel": {
                "identity_forwarding": true,
                "user_id": "410025",
                "actor_name": "张三"
            },
            "message": {
                "content": [
                    { "type": "image", "url": "https://example.invalid/image" },
                    { "type": "text", "text": "hello" }
                ]
            }
        });
        let message = provider_message_from_params(&opted_in).unwrap();
        assert_eq!(
            message["content"][1]["text"],
            "{\"sender\":{\"id\":\"410025\",\"name\":\"张三\"}}\n\nhello"
        );
    }

    async fn spawn_http1_server() -> std::net::SocketAddr {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request).await.unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                .await
                .unwrap();
        });
        addr
    }

    async fn spawn_delayed_http1_server(delay: Duration) -> std::net::SocketAddr {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request).await.unwrap();
            tokio::time::sleep(delay).await;
            let _ = stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                .await;
        });
        addr
    }

    async fn spawn_stalled_json_body_server(delay: Duration) -> std::net::SocketAddr {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request).await.unwrap();
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\n\
Content-Type: application/json\r\n\
Content-Length: 64\r\n\
Connection: keep-alive\r\n\
\r\n\
{\"ok\":",
                )
                .await
                .unwrap();
            tokio::time::sleep(delay).await;
        });
        addr
    }

    #[test]
    fn sse_policy_allows_http1_fallback_without_total_timeout() {
        let policy = ProviderClientPolicy::for_request(true);

        assert_eq!(policy.total_timeout, None);
        assert_eq!(policy.read_timeout, None);
        assert_eq!(
            policy.response_header_timeout,
            Some(Duration::from_secs(125))
        );
        assert!(!policy.http2_only);
    }

    #[test]
    fn sse_idle_timeout_is_fifteen_minutes() {
        assert_eq!(SSE_IDLE_TIMEOUT_MS, 15 * 60 * 1_000);
    }

    #[test]
    fn sse_read_wait_is_capped_by_run_deadline_and_frame_memory_is_bounded() {
        let (deadline_wait, deadline_limited) = sse_next_read_timeout(1_000, 1_250);
        assert_eq!(deadline_wait, Duration::from_millis(250));
        assert!(deadline_limited);

        let (idle_wait, deadline_limited) = sse_next_read_timeout(1_000, u64::MAX);
        assert_eq!(idle_wait, Duration::from_millis(SSE_IDLE_TIMEOUT_MS));
        assert!(!deadline_limited);
        assert_eq!(SSE_MAX_FRAME_BYTES, 8 * 1024 * 1024);
    }

    #[test]
    fn callback_policy_keeps_total_timeout_and_protocol_negotiation() {
        let policy = ProviderClientPolicy::for_request(false);

        assert_eq!(policy.total_timeout, Some(Duration::from_secs(65)));
        assert_eq!(policy.read_timeout, None);
        assert_eq!(policy.response_header_timeout, None);
        assert!(!policy.http2_only);
    }

    #[tokio::test]
    async fn provider_request_times_out_waiting_for_response_headers() {
        let addr = spawn_delayed_http1_server(Duration::from_millis(100)).await;
        let policy = ProviderClientPolicy {
            response_header_timeout: Some(Duration::from_millis(10)),
            http2_only: false,
            ..ProviderClientPolicy::for_request(true)
        };
        let client = provider_client_builder(policy).build().unwrap();
        let url_guard = OutboundUrlGuard::allowing_private_networks_for_tests();
        let target = BotDeliveryTarget::HttpProvider {
            bot_id: "bot-1".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "bot-1".to_string(),
            webhook_url: format!("http://{addr}"),
            bcs_to_provider_token: RedactedToken::new("secret"),
            protocol_version: "2.0".to_string(),
        };
        let body = ProviderWebhookRequest {
            frame_type: "request".to_string(),
            id: "frame-timeout".to_string(),
            method: "chat.send".to_string(),
            params: None,
            session_id: "session-1".to_string(),
            bcn_group_id: "group-1".to_string(),
            to_bot: ProviderWebhookBotRef {
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-1".to_string(),
                tags: Vec::new(),
            },
            from: None,
            message: None,
            attachments: Vec::new(),
            before: None,
            after: None,
            limit: None,
            timeout_ms: 1_000,
            extensions: None,
        };

        let error = send_provider_request_with_policy(
            &client,
            &url_guard,
            &target,
            &body,
            true,
            &[],
            policy,
            ProviderStatusPolicy::RequireSuccess,
        )
        .await
        .unwrap_err();

        assert!(
            error
                .to_string()
                .contains("provider response header timeout after 10ms")
        );
    }

    #[tokio::test]
    async fn json_fallback_body_timeout_bounds_incomplete_response() {
        let addr = spawn_stalled_json_body_server(Duration::from_secs(1)).await;
        let response = reqwest::Client::new()
            .get(format!("http://{addr}"))
            .send()
            .await
            .unwrap();

        let result = tokio::time::timeout(
            Duration::from_millis(250),
            read_provider_ack_body(response, Duration::from_millis(10)),
        )
        .await
        .expect("ack body reader must not remain pending");

        assert!(matches!(result, Err(ProviderAckBodyError::Timeout)));
    }

    #[test]
    fn provider_body_log_redacts_temporary_attachment_urls() {
        let body = ProviderWebhookRequest {
            frame_type: "event".to_string(),
            id: "frame-1".to_string(),
            method: "chat.send".to_string(),
            params: None,
            session_id: "session-1".to_string(),
            bcn_group_id: "group-1".to_string(),
            to_bot: ProviderWebhookBotRef {
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-1".to_string(),
                tags: Vec::new(),
            },
            from: None,
            message: None,
            attachments: vec![Attachment {
                attachment_id: "att-1".to_string(),
                attachment_type: bcs_protocol::AttachmentType::Image,
                file_name: "image".to_string(),
                mime_type: None,
                size: None,
                sha256: None,
                url: "https://download.example.com/image?token=secret".to_string(),
                expires_at: None,
            }],
            before: None,
            after: None,
            limit: None,
            timeout_ms: 1_000,
            extensions: Some(serde_json::json!({"caller_execution_token":{"type":"iam-token","token":"test-token"}, "other":"keep"})),
        };

        let logged = provider_body_log(&body);

        assert!(!logged.contains("test-token"));
        assert!(logged.contains("keep"));
        assert!(logged.contains("\"url\":\"<redacted>\""));
        assert!(!logged.contains("token=secret"));
        assert_eq!(
            body.attachments[0].url,
            "https://download.example.com/image?token=secret"
        );
    }

    #[test]
    fn provider_body_log_preserves_interaction_business_payload() {
        let body = ProviderWebhookRequest {
            frame_type: "req".to_string(),
            id: "resolve-frame-1".to_string(),
            method: "interaction.resolve".to_string(),
            params: Some(serde_json::json!({
                "bcsRunId": "bcs-run-1",
                "runId": "provider-run-1",
                "interactionId": "interaction-1",
                "kind": "ask_user",
                "idempotencyKey": "idem-1",
                "action": "submit",
                "answers": {"customer_name": {"values": ["sensitive answer"]}},
                "providerExtension": {"feedback": "sensitive extension"}
            })),
            session_id: "session-1".to_string(),
            bcn_group_id: "group-1".to_string(),
            to_bot: ProviderWebhookBotRef {
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-1".to_string(),
                tags: Vec::new(),
            },
            from: None,
            message: None,
            attachments: Vec::new(),
            before: None,
            after: None,
            limit: None,
            timeout_ms: 1_000,
            extensions: None,
        };

        let logged = provider_body_log(&body);

        assert!(logged.contains("\"action\":\"submit\""));
        assert!(logged.contains("sensitive answer"));
        assert!(logged.contains("sensitive extension"));
        assert!(!logged.contains("<redacted>"));
    }

    #[test]
    fn sse_detail_log_preserves_interaction_business_payload() {
        let logged = sse_data_log(
            "interaction",
            r#"{"runId":"run-1","seq":7,"phase":"resolved","interactionId":"i-1","kind":"ask_user","command":"sensitive command","answers":{"name":{"values":["sensitive answer"]}}}"#,
        );

        assert!(logged.contains("\"command\":\"sensitive command\""));
        assert!(logged.contains("sensitive answer"));

        let malformed = sse_data_log("interaction", "not-json interaction payload");
        assert_eq!(malformed, "not-json interaction payload");
    }

    #[tokio::test]
    async fn sse_and_callback_builders_accept_http1() {
        let callback_addr = spawn_http1_server().await;
        let callback_client = provider_client_builder(ProviderClientPolicy::for_request(false))
            .build()
            .unwrap();
        let callback_response = callback_client
            .get(format!("http://{callback_addr}"))
            .send()
            .await
            .unwrap();
        assert_eq!(callback_response.version(), reqwest::Version::HTTP_11);

        let sse_addr = spawn_http1_server().await;
        let sse_client = provider_client_builder(ProviderClientPolicy::for_request(true))
            .build()
            .unwrap();
        let sse_response = sse_client
            .get(format!("http://{sse_addr}"))
            .send()
            .await
            .unwrap();
        assert_eq!(sse_response.version(), reqwest::Version::HTTP_11);
    }
