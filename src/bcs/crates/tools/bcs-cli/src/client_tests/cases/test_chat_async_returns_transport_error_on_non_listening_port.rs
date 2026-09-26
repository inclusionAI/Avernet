//! cases implementation.
use super::*;


    #[tokio::test]
    pub(super) async fn test_chat_async_returns_transport_error_on_non_listening_port() {
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
            .chat_async("bot-target", "hello", None, None, &[], None, None, 2_000, false)
            .await;
        assert!(
            matches!(result, Err(ChatAsyncError::Transport(_))),
            "expected Transport error on non-listening port, got: {:?}",
            result
        );
    }



    #[tokio::test]
    pub(super) async fn test_chat_async_returns_invalid_response_on_malformed_202() {
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
            .chat_async("bot-target", "hello", None, None, &[], None, None, 2_000, false)
            .await;
        assert!(
            matches!(result, Err(ChatAsyncError::InvalidResponse(_))),
            "expected InvalidResponse on malformed 202, got: {:?}",
            result
        );
    }



    #[tokio::test]
    pub(super) async fn test_chat_poll_run_returns_poll_error_on_http_500() {
        use wiremock::{
            Mock, MockServer, ResponseTemplate,
            matchers::{method, path},
        };

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/bots/bot-target/chat-async"))
            .respond_with(ResponseTemplate::new(202).set_body_json(serde_json::json!({
                "run_id": "run-1",
                "bot_uuid": "bot-target",
                "session_id": "session-1",
                "status": "submitted",
                "expires_at_ms": 9_999_999_u64,
            })))
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/chat/runs/run-1"))
            .respond_with(
                ResponseTemplate::new(500)
                    .set_body_json(serde_json::json!({"error": "internal"})),
            )
            .mount(&server)
            .await;

        let client = BcsClient::new(server.uri());
        let submit = client
            .chat_async("bot-target", "hello", None, None, &[], None, None, 5_000, false)
            .await
            .expect("chat_async submit should succeed");
        let outcome = client
            .chat_poll_run(&submit, 10, Duration::from_millis(5_000))
            .await
            .expect("poll error should return a structured outcome");

        assert!(!outcome.delivered);
        assert_eq!(outcome.state, "poll_error");
        assert_eq!(
            outcome.run_id.as_deref(),
            Some("run-1"),
            "IDs should come from submit response"
        );
        assert_eq!(outcome.session_id.as_deref(), Some("session-1"));
        assert!(outcome.submitted);
    }



    #[tokio::test]
    pub(super) async fn test_chat_poll_run_returns_failed_on_terminal_failed_state() {
        use wiremock::{
            Mock, MockServer, ResponseTemplate,
            matchers::{method, path},
        };

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/bots/bot-target/chat-async"))
            .respond_with(ResponseTemplate::new(202).set_body_json(serde_json::json!({
                "run_id": "run-1",
                "bot_uuid": "bot-target",
                "session_id": "session-1",
                "status": "submitted",
                "expires_at_ms": 9_999_999_u64,
            })))
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/chat/runs/run-1"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "run_id": "run-1",
                "bot_uuid": "bot-target",
                "from_bot_id": "bot-source",
                "session_id": "session-1",
                "state": "failed",
                "response": {"content": ""},
                "created_at_ms": 1_u64,
                "updated_at_ms": 2_u64,
                "expires_at_ms": 9_999_999_u64,
                "version": 2_u64,
                "content_truncated": false,
                "is_terminal": true,
                "error_message": "provider returned error: timeout"
            })))
            .mount(&server)
            .await;

        let client = BcsClient::new(server.uri());
        let submit = client
            .chat_async("bot-target", "hello", None, None, &[], None, None, 5_000, false)
            .await
            .expect("chat_async submit should succeed");
        let outcome = client
            .chat_poll_run(&submit, 10, Duration::from_millis(5_000))
            .await
            .expect("poll should return a failed terminal outcome");

        assert!(!outcome.delivered);
        assert_eq!(outcome.state, "failed");
        assert!(outcome.submitted);
        assert_eq!(outcome.run_id.as_deref(), Some("run-1"));
        assert_eq!(outcome.session_id.as_deref(), Some("session-1"));
    }



    #[test]
    pub(super) fn test_non_chat_headers_omit_chat_version() {
        let client = BcsClient::new("http://localhost:21000");
        let request = client
            .add_headers(client.http_client.get("http://localhost:21000/health"))
            .build()
            .unwrap();

        assert!(request.headers().get(BCS_CHAT_VERSION_HEADER).is_none());
    }



    #[tokio::test]
    pub(super) async fn list_channel_conversations_by_session_encodes_query_parameters() {
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
                ResponseTemplate::new(200)
                    .set_body_json(serde_json::json!({ "items": [] })),
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




    #[test]
    pub(super) fn test_chat_async_payload_omits_cli_wait_controls() {
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
    pub(super) fn test_chat_async_payload_includes_organization_code_when_present() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            None,
            Some(" promo-2026 "),
        );

        assert_eq!(payload["organization_code"], serde_json::json!("promo-2026"));
    }



    #[test]
    pub(super) fn test_chat_async_payload_omits_blank_organization_code() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            None,
            Some("  "),
        );

        assert!(payload.get("organization_code").is_none());
    }




    // ========================================================================
    // Cross-host Authorization isolation regression test
    // ========================================================================

    /// Ensures that `put_session_file_bytes` does NOT send an `Authorization`
    /// header when the upload URL points to a different host than the BCS
    /// base URL. OSS presigned URLs self-authenticate and must never leak the
    /// bearer token across host boundaries.
    #[tokio::test]
    pub(super) async fn cross_host_put_omits_authorization() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::sync::{Arc, Mutex};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let captured_headers = Arc::new(Mutex::new(String::new()));
        let server_headers = captured_headers.clone();

        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
            let mut buf = [0_u8; 4096];
            let size = stream.read(&mut buf).unwrap_or(0);
            let request = String::from_utf8_lossy(&buf[..size]);
            *server_headers.lock().unwrap() = request.to_string();

            let response =
                "HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n";
            let _ = stream.write_all(response.as_bytes());
            let _ = stream.flush();
        });

        // BCS base on localhost (different host from 127.0.0.1 per reqwest)
        let client = BcsClient::with_token(
            format!("http://localhost:{}", addr.port().wrapping_add(1)),
            "test-bearer-token",
        );
        let upload_url = format!("http://127.0.0.1:{}/put", addr.port());
        let body = reqwest::Body::from("test-body");
        let result = client.put_session_file_bytes(&upload_url, body, "application/octet-stream").await;

        server.join().unwrap();
        assert!(result.is_ok(), "PUT should succeed: {:?}", result.err());
        let request = captured_headers.lock().unwrap();
        let request_lower = request.to_lowercase();
        assert!(
            !request_lower.contains("authorization:"),
            "Authorization header MUST NOT be sent cross-host, but was:\n{}",
            request
        );
        assert!(
            request_lower.contains("content-type: application/octet-stream"),
            "Content-Type must match the prepared upload content type, but the request was:\n{}",
            request
        );
    }



    #[tokio::test]
    pub(super) async fn create_session_sends_group_context_delivery() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/groups/g-1/sessions"))
            .respond_with(
                ResponseTemplate::new(201).set_body_json(serde_json::json!({
                    "id": "g-1:abcdef12",
                    "session_id": "g-1:abcdef12",
                })),
            )
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



    // Regression: `share --ttl N` must send `ttl_seconds` (the ShareRequest DTO
    // field), not `ttl` — serde ignores unknown fields, so `ttl` would be
    // silently dropped and the service would fall back to its default expiry.
    #[tokio::test]
    pub(super) async fn share_session_file_sends_ttl_seconds_field() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/sessions/s1/files/f1/share"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "share_url": "http://bcs/sessions/shared-file?token=x",
                    "share_token": "x",
                    "expires_at": 0u64,
                })),
            )
            .mount(&server)
            .await;

        let client = BcsClient::with_token(&server.uri(), "bot-token");
        let _ = client.share_session_file("s1", "f1", Some(120)).await.unwrap();

        let requests = server.received_requests().await.expect("captured requests");
        let req = requests
            .iter()
            .find(|r| r.method.as_str() == "POST" && r.url.path() == "/sessions/s1/files/f1/share")
            .expect("share POST request");
        let body: serde_json::Value = serde_json::from_slice(&req.body).unwrap();
        assert_eq!(body["ttl_seconds"], 120, "body must carry ttl_seconds, was: {body}");
        assert!(
            body.get("ttl").is_none(),
            "legacy `ttl` key must not be sent (silently ignored by the DTO), was: {body}"
        );
    }
