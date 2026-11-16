use super::*;

    #[tokio::test]
    async fn test_chat_poll_run_until_running_waits_through_submitted_until_running() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::sync::{
            Arc,
            atomic::{AtomicBool, AtomicUsize, Ordering},
        };
        use std::time::{Duration as StdDuration, Instant};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        // Keep the listener non-blocking so the worker can check the shutdown
        // flag between accepts; accepted streams are forced back to blocking
        // below so the request line can be read reliably.
        listener.set_nonblocking(true).unwrap();
        let addr = listener.local_addr().unwrap();
        let get_count = Arc::new(AtomicUsize::new(0));
        let served_get_count = get_count.clone();
        let shutdown = Arc::new(AtomicBool::new(false));
        let server_shutdown = shutdown.clone();

        let server = std::thread::spawn(move || {
            // Generous backstop: only meant to let the worker exit if the
            // client panics before signalling shutdown. A healthy
            // POST -> GET -> GET flow completes in milliseconds; this never
            // races a passing run.
            let deadline = Instant::now() + StdDuration::from_secs(30);
            while Instant::now() < deadline && !server_shutdown.load(Ordering::SeqCst) {
                let Ok((mut stream, _)) = listener.accept() else {
                    std::thread::sleep(StdDuration::from_millis(5));
                    continue;
                };
                // Force blocking reads on the accepted stream so a single
                // `read` cannot return 0/partial bytes before the request
                // line arrives. Read until at least the first line is whole.
                stream.set_nonblocking(false).ok();
                stream
                    .set_read_timeout(Some(StdDuration::from_secs(2)))
                    .ok();
                let mut buf = [0_u8; 4096];
                let mut size = 0;
                let request = loop {
                    match stream.read(&mut buf[size..]) {
                        Ok(0) | Err(_) => break String::from_utf8_lossy(&buf[..size]).into_owned(),
                        Ok(n) => {
                            size += n;
                            if buf[..size].contains(&b'\n') || size == buf.len() {
                                break String::from_utf8_lossy(&buf[..size]).into_owned();
                            }
                        }
                    }
                };
                let first_line = request.lines().next().unwrap_or_default();
                let body = if first_line.starts_with("POST ")
                    && first_line.contains("/bots/bot-target/chat-async")
                {
                    serde_json::json!({
                        "run_id": "run-1",
                        "bot_uuid": "bot-target",
                        "session_id": "session-1",
                        "status": "submitted",
                        "expires_at_ms": 9_999_999_u64
                    })
                } else if first_line.starts_with("GET ") && first_line.contains("/chat/runs/run-1")
                {
                    let count = served_get_count.fetch_add(1, Ordering::SeqCst) + 1;
                    let state = if count == 1 { "submitted" } else { "running" };
                    serde_json::json!({
                        "run_id": "run-1",
                        "bot_uuid": "bot-target",
                        "from_bot_id": "bot-source",
                        "session_id": "session-1",
                        "state": state,
                        "response": {"content": ""},
                        "created_at_ms": 1_u64,
                        "updated_at_ms": count as u64 + 1,
                        "expires_at_ms": 9_999_999_u64,
                        "version": count as u64 + 1,
                        "content_truncated": false,
                        "is_terminal": false
                    })
                } else {
                    serde_json::json!({"error": "unexpected request", "line": first_line})
                };
                let body = body.to_string();
                let response = format!(
                    "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                // Tolerate write failures instead of panicking the worker; a
                // failed write just closes the connection and the client
                // surfaces the error, which is the intended failure mode.
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.flush();
            }
        });

        let client = BcsClient::new(format!("http://{}", addr));
        let submit = client
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                2_000,
                true,
            )
            .await
            .expect("chat_async submit should succeed");
        let outcome = client
            .chat_poll_run_until_running(&submit, 10, Duration::from_millis(2_000))
            .await
            .expect("poll should return an outcome");
        // Signal the worker to exit whether or not the call succeeded so the
        // join below never waits on the backstop deadline.
        shutdown.store(true, Ordering::SeqCst);

        server.join().unwrap();
        assert_eq!(
            get_count.load(Ordering::SeqCst),
            2,
            "detach should poll twice: submitted then running"
        );
        assert!(outcome.delivered, "running should be delivered");
        assert_eq!(outcome.state, "running");
        assert_eq!(outcome.run_id.as_deref(), Some("run-1"));
        assert_eq!(outcome.session_id.as_deref(), Some("session-1"));
        assert!(outcome.submitted);
    }

    #[tokio::test]
    async fn test_chat_poll_run_times_out_with_ids_from_submit() {
        use wiremock::{
            Mock, MockServer, ResponseTemplate,
            matchers::{method, path},
        };

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/bots/bot-target/chat-async"))
            .respond_with(ResponseTemplate::new(202).set_body_json(serde_json::json!({
                "run_id": "run-local-timeout",
                "bot_uuid": "bot-target",
                "session_id": "session-1",
                "status": "running",
                "expires_at_ms": 9_999_999_u64,
            })))
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/chat/runs/run-local-timeout"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "run_id": "run-local-timeout",
                "bot_uuid": "bot-target",
                "from_bot_id": "bot-source",
                "session_id": "session-1",
                "state": "running",
                "response": {"content": ""},
                "created_at_ms": 1_u64,
                "updated_at_ms": 2_u64,
                "expires_at_ms": 9_999_999_u64,
                "version": 2_u64,
                "content_truncated": false,
                "is_terminal": false,
            })))
            .mount(&server)
            .await;

        let mut client = BcsClient::new(server.uri());
        client
            .set_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
            .unwrap();
        let submit = client
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                20,
                false,
            )
            .await
            .expect("chat_async submit should succeed");
        let outcome = client
            .chat_poll_run(&submit, 5, Duration::from_millis(20))
            .await
            .expect("poll should return a timeout outcome");

        assert!(!outcome.delivered, "timeout should not be delivered");
        assert_eq!(outcome.state, "timeout");
        assert_eq!(
            outcome.run_id.as_deref(),
            Some("run-local-timeout"),
            "IDs should come from submit response"
        );
        assert_eq!(outcome.session_id.as_deref(), Some("session-1"));
        assert!(outcome.submitted);
        let requests = server.received_requests().await.unwrap();
        let submit_req = requests
            .iter()
            .find(|request| request.url.path() == "/bots/bot-target/chat-async")
            .expect("chat submit request");
        assert_eq!(
            submit_req
                .headers
                .get("traceparent")
                .and_then(|value| value.to_str().ok()),
            Some("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
        );
        assert_eq!(
            submit_req
                .headers
                .get("x-bcs-client-detach")
                .and_then(|value| value.to_str().ok()),
            Some("false")
        );
        assert_eq!(
            submit_req
                .headers
                .get("x-bcs-client-wait-timeout-ms")
                .and_then(|value| value.to_str().ok()),
            Some("20")
        );
        let payload: serde_json::Value = serde_json::from_slice(&submit_req.body).unwrap();
        assert!(payload.get("timeout_ms").is_none());
        assert!(payload.get("caller_wait_mode").is_none());
        assert!(
            requests
                .iter()
                .all(|request| request.url.path() != "/chat/runs/run-local-timeout/cancel")
        );
    }

    #[tokio::test]
    async fn test_chat_poll_run_until_running_accepts_completed_as_success() {
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
                "state": "completed",
                "response": {"content": "done"},
                "created_at_ms": 1_u64,
                "updated_at_ms": 2_u64,
                "expires_at_ms": 9_999_999_u64,
                "version": 2_u64,
                "content_truncated": false,
                "is_terminal": true,
            })))
            .mount(&server)
            .await;

        let client = BcsClient::new(server.uri());
        let submit = client
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                60_000,
                true,
            )
            .await
            .expect("chat_async submit should succeed");
        let outcome = client
            .chat_poll_run_until_running(&submit, 10, Duration::from_millis(5_000))
            .await
            .expect("poll should accept completed as detach success");

        assert!(outcome.delivered, "completed should be detach-delivered");
        assert_eq!(outcome.state, "completed");
        assert_eq!(outcome.run_id.as_deref(), Some("run-1"));
    }

    #[tokio::test]
    async fn test_chat_poll_run_returns_poll_error_on_http_500() {
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
                ResponseTemplate::new(500).set_body_json(serde_json::json!({"error": "internal"})),
            )
            .mount(&server)
            .await;

        let client = BcsClient::new(server.uri());
        let submit = client
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                5_000,
                false,
            )
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
    async fn test_chat_poll_run_returns_failed_on_terminal_failed_state() {
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
            .chat_async(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                None,
                5_000,
                false,
            )
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

    #[tokio::test]
    async fn test_queued_run_keeps_polling_to_requested_state() {
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::{method, path}};
        for until_running in [false, true] {
            let server = MockServer::start().await;
            Mock::given(method("GET")).and(path("/chat/runs/run-queued"))
                .respond_with(|request: &wiremock::Request| {
                    let version = request.url.query_pairs()
                        .find(|(key, _)| key == "since_version")
                        .unwrap().1.parse::<u64>().unwrap();
                    let (state, delivery) = match version {
                        0 => ("pending", "queued"),
                        1 => ("submitted", "dispatching"),
                        2 => ("running", "running"),
                        _ => ("completed", "completed"),
                    };
                    ResponseTemplate::new(200).insert_header("connection", "close")
                        .set_body_json(queued_status(state, delivery, version + 1))
                }).mount(&server).await;
            let client = BcsClient::new(server.uri());
            let submit = queued_submit();
            let outcome = if until_running {
                client.chat_poll_run_until_running(&submit, 10, Duration::from_secs(10)).await
            } else {
                client.chat_poll_run(&submit, 10, Duration::from_secs(10)).await
            }.unwrap();
            let expected = if until_running { "running" } else { "completed" };
            assert_eq!(outcome.state, expected, "requests: {:?}", server.received_requests().await.unwrap().iter().map(|r| r.url.as_str()).collect::<Vec<_>>());
            assert!(outcome.delivered);
            assert_eq!(outcome.delivery.unwrap().status, expected);
            assert_eq!(outcome.response_content.as_deref(), if until_running { None } else { Some("answer") });
            let requests = server.received_requests().await.unwrap();
            assert_eq!(requests.len(), if until_running { 3 } else { 4 });
            assert!(requests.iter().all(|r| r.method.as_str() == "GET"));
        }
    }

    #[tokio::test]
    async fn test_queued_run_deadline_bounds_slow_status_without_cancelling() {
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::{method, path}};
        for until_running in [false, true] {
            let server = MockServer::start().await;
            Mock::given(method("GET")).and(path("/chat/runs/run-queued"))
                .respond_with(ResponseTemplate::new(200)
                    .set_body_json(queued_status("pending", "queued", 2))
                    .set_delay(Duration::from_secs(2)))
                .mount(&server).await;
            let client = BcsClient::new(server.uri());
            let submit = queued_submit();
            let start = tokio::time::Instant::now();
            let outcome = if until_running {
                client.chat_poll_run_until_running(&submit, 5000, Duration::from_millis(100)).await
            } else {
                client.chat_poll_run(&submit, 5000, Duration::from_millis(100)).await
            }.unwrap();
            assert!(start.elapsed() < Duration::from_secs(1));
            assert_eq!(outcome.state, "timeout");
            assert!(outcome.submitted);
            assert_eq!(outcome.run_id.as_deref(), Some("run-queued"));
            assert_eq!(outcome.session_id.as_deref(), Some("session-queued"));
            assert_eq!(outcome.delivery.unwrap().delivery_id, "delivery-queued");
            let requests = server.received_requests().await.unwrap();
            assert_eq!(requests.len(), 1);
            assert_eq!(requests[0].method.as_str(), "GET");
        }
    }

    #[tokio::test]
    async fn test_queued_run_poll_errors_preserve_durable_identity() {
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::{method, path}};
        let server = MockServer::start().await;
        Mock::given(method("GET")).and(path("/chat/runs/run-queued"))
            .respond_with(ResponseTemplate::new(503)).mount(&server).await;
        let client = BcsClient::new(server.uri());
        let submit = queued_submit();
        for until_running in [false, true] {
            let outcome = if until_running {
                client.chat_poll_run_until_running(&submit, 10, Duration::from_secs(10)).await
            } else {
                client.chat_poll_run(&submit, 10, Duration::from_secs(10)).await
            }.unwrap();
            assert_eq!(outcome.state, "poll_error");
            assert_eq!(outcome.run_id.as_deref(), Some("run-queued"));
            assert_eq!(outcome.session_id.as_deref(), Some("session-queued"));
            assert_eq!(outcome.delivery.unwrap().message_id, "message-queued");
            assert!(outcome.error_message.unwrap().contains("503"));
        }
    }

    #[tokio::test]
    async fn test_queued_run_terminal_failure_is_not_running_success() {
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::{method, path}};
        for state in ["failed", "cancelled"] {
            let server = MockServer::start().await;
            Mock::given(method("GET")).and(path("/chat/runs/run-queued"))
                .respond_with(ResponseTemplate::new(200).set_body_json(queued_status(state, state, 2)))
                .mount(&server).await;
            let outcome = BcsClient::new(server.uri())
                .chat_poll_run_until_running(&queued_submit(), 10, Duration::from_secs(10)).await.unwrap();
            assert!(!outcome.delivered);
            assert!(!outcome.admission_succeeded());
            assert_eq!(outcome.delivery.unwrap().status, state);
        }
    }
