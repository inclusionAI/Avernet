use super::*;

    #[test]
    fn test_chat_run_status_deserializes_submitted_state() {
        let status: ChatRunStatusResponse = serde_json::from_value(serde_json::json!({
            "run_id": "run-1",
            "bot_uuid": "bot-target",
            "from_bot_id": "bot-source",
            "session_id": "session-1",
            "state": "submitted",
            "response": {"content": ""},
            "created_at_ms": 1,
            "updated_at_ms": 2,
            "expires_at_ms": 3,
            "version": 2,
            "is_terminal": false
        }))
        .unwrap();

        assert!(matches!(status.state, ChatRunState::Submitted));
        assert!(!status.is_terminal());
    }

    #[test]
    fn test_chat_run_status_deserializes_unknown_future_state() {
        let status: ChatRunStatusResponse = serde_json::from_value(serde_json::json!({
            "run_id": "run-1",
            "bot_uuid": "bot-target",
            "from_bot_id": "bot-source",
            "session_id": "session-1",
            "state": "provider_accepted",
            "response": {"content": ""},
            "created_at_ms": 1,
            "updated_at_ms": 2,
            "expires_at_ms": 3,
            "version": 2,
            "is_terminal": false
        }))
        .unwrap();

        assert!(matches!(status.state, ChatRunState::Unknown));
        assert!(!status.is_terminal());
    }

    #[tokio::test]
    async fn test_chat_async_returns_transport_error_on_non_listening_port() {
        // Reserve an ephemeral port without listening so another service cannot
        // claim it. Bypass environment proxies to exercise the local transport.
        let socket = tokio::net::TcpSocket::new_v4().unwrap();
        socket.bind("127.0.0.1:0".parse().unwrap()).unwrap();
        let mut client = BcsClient::new(format!("http://{}", socket.local_addr().unwrap()));
        client.http_client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(2))
            .build()
            .unwrap();
        let result = client
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                2_000,
                false,
            )
            .await;
        assert!(
            matches!(result, Err(ChatAsyncError::Transport(_))),
            "expected Transport error on non-listening port, got: {:?}",
            result
        );
    }

    #[tokio::test]
    async fn test_chat_async_returns_invalid_response_on_malformed_202() {
        // A 202 whose body cannot deserialize into ChatRunSubmitResponse: the
        // server has accepted (and may have created a run), but the ID is
        // unreadable. This must classify as InvalidResponse so main.rs maps
        // it to submit_indeterminate (not submit_failed).
        use wiremock::{
            Mock, MockServer, ResponseTemplate,
            matchers::{method, path},
        };

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/bots/bot-target/chat-async"))
            .respond_with(
                ResponseTemplate::new(202)
                    .insert_header("content-type", "application/json")
                    .set_body_string("{not valid json"),
            )
            .mount(&server)
            .await;

        let client = BcsClient::new(server.uri());
        let result = client
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                2_000,
                false,
            )
            .await;
        assert!(
            matches!(result, Err(ChatAsyncError::InvalidResponse(_))),
            "expected InvalidResponse on malformed 202, got: {:?}",
            result
        );
    }

    #[test]
    fn test_durable_admission_distinguishes_queued_from_rejected() {
        let mut submit = queued_submit();
        let admitted = BcsClient::admitted_outcome(&submit);
        assert!(admitted.admission_succeeded());
        assert!(!admitted.delivered);
        assert_eq!(admitted.run_id.as_deref(), Some("run-queued"));
        assert_eq!(admitted.session_id.as_deref(), Some("session-queued"));
        assert_eq!(admitted.delivery.unwrap().wait_reason.as_deref(), Some("lane_busy"));
        for status in ["rejected_capacity", "failed", "expired", "cancelled"] {
            submit.delivery.as_mut().unwrap().status = status.into();
            assert!(!BcsClient::admitted_outcome(&submit).admission_succeeded(), "{status}");
        }
        // Older servers omit queue metadata and may omit the initial status.
        submit.delivery = None;
        submit.status = None;
        assert!(BcsClient::admitted_outcome(&submit).admission_succeeded());
        for state in ["running", "completed"] {
            submit.status = Some(state.into());
            assert!(BcsClient::admitted_outcome(&submit).delivered);
        }
        submit.status = Some("failed".into());
        assert!(!BcsClient::admitted_outcome(&submit).admission_succeeded());
    }

    #[tokio::test]
    async fn test_chat_run_cancel_preserves_unknown_delivery_state() {
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::{method, path}};
        let server = MockServer::start().await;
        Mock::given(method("POST")).and(path("/chat/runs/run-queued/cancel"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "run_id": "run-queued", "cancelled": false, "state": "running",
                "response": {"content": ""}, "version": 3,
                "delivery": {"delivery_id": "delivery-queued", "message_id": "message-queued",
                    "status": "cancel_unknown", "state_version": 3}
            }))).expect(1).mount(&server).await;
        let cancelled = BcsClient::new(server.uri()).chat_run_cancel("run-queued").await.unwrap();
        assert!(!cancelled.cancelled);
        assert_eq!(cancelled.state, ChatRunState::Running);
        assert_eq!(cancelled.delivery.unwrap().status, "cancel_unknown");
    }

    #[test]
    fn test_chat_async_payload_omits_cli_wait_controls() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            Some("after-last-tool-call"),
            None,
        );

        assert!(payload.get("timeout_ms").is_none());
        assert!(payload.get("caller_wait_mode").is_none());
    }

    #[test]
    fn test_chat_async_payload_includes_organization_code_when_present() {
        let payload =
            BcsClient::chat_async_payload("hi", None, None, &[], None, Some(" promo-2026 "));

        assert_eq!(
            payload["organization_code"],
            serde_json::json!("promo-2026")
        );
    }

    #[test]
    fn test_chat_async_payload_omits_blank_organization_code() {
        let payload = BcsClient::chat_async_payload("hi", None, None, &[], None, Some("  "));

        assert!(payload.get("organization_code").is_none());
    }
