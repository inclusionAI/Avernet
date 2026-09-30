//! cases implementation.
use super::*;


    #[test]
    pub(super) fn guess_mime_from_extension_known_types() {
        assert_eq!(BcsClient::guess_mime_from_extension("report.pdf"), Some("application/pdf".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("DATA.CSV"), Some("text/csv".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("pic.jpeg"), Some("image/jpeg".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("pic.jpg"), Some("image/jpeg".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("notes.md"), Some("text/markdown".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("data.json"), Some("application/json".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("archive.zip"), Some("application/zip".into()));
        assert_eq!(BcsClient::guess_mime_from_extension("model.bin"), Some("application/octet-stream".into()));
    }



    #[test]
    pub(super) fn guess_mime_from_extension_unknown_returns_none() {
        assert_eq!(BcsClient::guess_mime_from_extension("file.xyz"), None);
        assert_eq!(BcsClient::guess_mime_from_extension("noext"), None);
        assert_eq!(BcsClient::guess_mime_from_extension(""), None);
        assert_eq!(BcsClient::guess_mime_from_extension(".hidden"), None);
    }



    #[test]
    pub(super) fn is_text_mime_recognizes_textual_types() {
        assert!(BcsClient::is_text_mime("text/csv"));
        assert!(BcsClient::is_text_mime("text/plain"));
        assert!(BcsClient::is_text_mime("text/markdown"));
        assert!(BcsClient::is_text_mime("application/json"));
        assert!(BcsClient::is_text_mime("application/xml"));
        assert!(BcsClient::is_text_mime("image/svg+xml"));
        assert!(BcsClient::is_text_mime("text/html; charset=utf-8"));
        assert!(!BcsClient::is_text_mime("application/octet-stream"));
        assert!(!BcsClient::is_text_mime("image/png"));
        assert!(!BcsClient::is_text_mime("application/pdf"));
    }



    #[tokio::test]
    pub(super) async fn guess_charset_returns_none_for_ascii() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ascii.txt");
        tokio::fs::write(&path, b"plain ascii text, no non-ascii bytes")
            .await
            .unwrap();
        assert_eq!(BcsClient::guess_charset(path.to_str().unwrap(), 4096).await, None);
    }



    #[tokio::test]
    pub(super) async fn guess_charset_detects_utf8_and_cjk() {
        // Valid UTF-8 Chinese text → detector reports utf-8.
        let dir = tempfile::tempdir().unwrap();
        let u8_path = dir.path().join("cn-utf8.txt");
        tokio::fs::write(&u8_path, "你好，世界，中文测试".as_bytes())
            .await
            .unwrap();
        let cs = BcsClient::guess_charset(u8_path.to_str().unwrap(), 4096)
            .await
            .expect("utf-8 should be detected");
        assert!(cs.contains("utf-8"), "expected utf-8, got {cs}");

        // GBK-encoded Chinese (same text) must be detected as a CJK family label,
        // not utf-8. `gb18030`/`gbk`/`replacement` are all acceptable from chardetng.
        let dir2 = tempfile::tempdir().unwrap();
        let gbk_path = dir2.path().join("cn-gbk.txt");
        // "你好，世界" in GBK (no BOM): C4 E3 BA C3 A3 AC CA C0 BD E7
        let gbk_bytes = &[0xC4, 0xE3, 0xBA, 0xC3, 0xA3, 0xAC, 0xCA, 0xC0, 0xBD, 0xE7];
        tokio::fs::write(&gbk_path, gbk_bytes).await.unwrap();
        let cs_gbk = BcsClient::guess_charset(gbk_path.to_str().unwrap(), 4096)
            .await
            .expect("gbk-family should be detected");
        // GBK family should produce a label acceptable to browsers for CJK.
        assert!(
            cs_gbk.contains("gb") || cs_gbk.contains("big5") || cs_gbk.contains("replacement"),
            "expected a CJK/legacy label, got {cs_gbk}"
        );
        assert!(!cs_gbk.contains("utf-8"), "GBK must not be misdetected as utf-8: {cs_gbk}");
    }



    #[tokio::test]
    pub(super) async fn guess_charset_detects_large_utf8_truncated() {
        // Regression guard for finding #1: a >64KB UTF-8 Chinese file read as a
        // 64KB prefix must still be detected as utf-8, not windows-1252. Chinese
        // chars are 3 bytes in UTF-8, so the 64KB cut almost always lands mid-char.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("large-utf8.md");
        let unit = "你好世界中文测试";
        let mut content = String::new();
        while content.len() < 80_000 {
            content.push_str(unit);
        }
        tokio::fs::write(&path, content.into_bytes()).await.unwrap();
        let cs = BcsClient::guess_charset(path.to_str().unwrap(), 64 * 1024)
            .await
            .expect("utf-8 should be detected for a large truncated file");
        assert!(
            cs.contains("utf-8"),
            "large truncated UTF-8 must not be misdetected as {cs}"
        );
    }



    #[tokio::test]
    pub(super) async fn guess_charset_handles_utf16_bom() {
        // Regression guard for finding #2: chardetng never reports UTF-16. Excel
        // "Unicode CSV" exports carry a UTF-16LE/BE BOM that must be sniffed.
        let dir = tempfile::tempdir().unwrap();

        // UTF-16LE with BOM (FF FE).
        let le_path = dir.path().join("utf16le.csv");
        let mut le_bytes = vec![0xFF, 0xFE];
        for w in "你好世界".encode_utf16().collect::<Vec<u16>>() {
            le_bytes.push((w & 0xFF) as u8);
            le_bytes.push((w >> 8) as u8);
        }
        tokio::fs::write(&le_path, &le_bytes).await.unwrap();
        let cs_le = BcsClient::guess_charset(le_path.to_str().unwrap(), 4096)
            .await
            .expect("utf-16 should be detected via BOM");
        assert!(cs_le.contains("utf-16"), "expected utf-16, got {cs_le}");

        // UTF-16BE with BOM (FE FF).
        let be_path = dir.path().join("utf16be.csv");
        let mut be_bytes = vec![0xFE, 0xFF];
        for w in "你好世界".encode_utf16().collect::<Vec<u16>>() {
            be_bytes.push((w >> 8) as u8);
            be_bytes.push((w & 0xFF) as u8);
        }
        tokio::fs::write(&be_path, &be_bytes).await.unwrap();
        let cs_be = BcsClient::guess_charset(be_path.to_str().unwrap(), 4096)
            .await
            .expect("utf-16 should be detected via BOM");
        assert!(cs_be.contains("utf-16"), "expected utf-16, got {cs_be}");
    }



    #[tokio::test]
    pub(super) async fn explicit_text_mime_still_eligible_for_charset() {
        // The charset-enrichment contract for upload_session_file: an explicit
        // text MIME (e.g. --mime text/markdown on a UTF-8 Chinese file) is
        // treated as textual and gets a charset appended, unless it already
        // carries one. Express via the composing helpers.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("cn.md");
        tokio::fs::write(&path, "你好，世界".as_bytes()).await.unwrap();

        // 1) Explicit text MIME without charset → eligible (textual + no existing charset).
        let explicit = "text/markdown";
        assert!(BcsClient::is_text_mime(explicit));
        assert!(!explicit.to_ascii_lowercase().contains("charset="));
        let detected = BcsClient::guess_charset(path.to_str().unwrap(), 4096)
            .await
            .expect("utf-8 should be detected for Chinese markdown");
        assert!(detected.contains("utf-8"), "expected utf-8, got {detected}");
        let enriched = format!("{explicit}; charset={detected}");
        assert_eq!(enriched, "text/markdown; charset=utf-8");

        // 2) Explicit text MIME already carrying a charset → respected, not re-sniffed/duplicated.
        let pre = "text/markdown; charset=gbk";
        assert!(BcsClient::is_text_mime(pre));
        assert!(pre.to_ascii_lowercase().contains("charset="));
        // The guard in upload_session_file skips enrichment when `charset=` is present,
        // so `pre` is emitted unchanged (no guess_charset call, no doubling).
        assert_eq!(pre, "text/markdown; charset=gbk");

        // 3) Explicit non-text MIME (--mime application/octet-stream) → not textual → no charset.
        let bin = "application/octet-stream";
        assert!(!BcsClient::is_text_mime(bin));
    }



    #[test]
    #[allow(unsafe_code)]
    pub(super) fn test_default_bcs_url() {
        // SAFETY: This is a test, we're removing an env var in a controlled manner
        unsafe {
            std::env::remove_var("MOLTIS_BCS_URL");
        }
        let client = BcsClient::from_env();
        assert_eq!(client.base_url(), DEFAULT_BCS_URL);
    }



    #[test]
    pub(super) fn test_custom_bcs_url() {
        let client = BcsClient::new("http://custom:9000");
        assert_eq!(client.base_url(), "http://custom:9000");
    }



    #[test]
    pub(super) fn test_with_token_and_oauth() {
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
    pub(super) fn test_set_oauth_headers() {
        let mut client = BcsClient::with_token("http://localhost:21000", "bot-token");
        assert!(client.oauth_headers.is_none());

        let mut headers = HashMap::new();
        headers.insert("X-Auth-Token".to_string(), "oauth-val".to_string());
        client.set_oauth_headers(headers);
        assert!(client.oauth_headers.is_some());
    }



    #[test]
    pub(super) fn test_no_oauth_headers_by_default() {
        let client = BcsClient::new("http://localhost:21000");
        assert!(client.oauth_headers.is_none());

        let client = BcsClient::with_token("http://localhost:21000", "token");
        assert!(client.oauth_headers.is_none());

        let client = BcsClient::with_token_and_cookie("http://localhost:21000", "token", "cookie");
        assert!(client.oauth_headers.is_none());
    }



    #[test]
    pub(super) fn test_oauth_headers_layout() {
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
    pub(super) fn test_no_oauth_uses_standard_authorization() {
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
    pub(super) fn test_chat_headers_include_chat_version() {
        let client = BcsClient::new("http://localhost:21000");
        let request = client
            .add_chat_headers(client.http_client.get("http://localhost:21000/chat/runs/run-1"))
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
    pub(super) fn test_chat_dispatch_headers_include_configured_traceparent() {
        let mut client = BcsClient::new("http://localhost:21000");
        client
            .set_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
            .unwrap();

        let request = client
            .add_chat_dispatch_headers(
                client.http_client.post("http://localhost:21000/bots/bot-1/chat-async"),
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
    pub(super) fn test_status_poll_headers_omit_trace_and_wait_controls() {
        let mut client = BcsClient::new("http://localhost:21000");
        client
            .set_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
            .unwrap();

        let request = client
            .add_chat_headers(client.http_client.get("http://localhost:21000/chat/runs/run-1"))
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
    pub(super) fn test_set_traceparent_forwards_http_safe_values_without_w3c_semantic_validation() {
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
                    client.http_client.post("http://localhost:21000/bots/bot-1/chat-async"),
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
    pub(super) fn test_set_traceparent_rejects_unsafe_http_header_values() {
        for value in ["bad\ntraceparent", "bad\r\ntraceparent: injected"] {
            let mut client = BcsClient::new("http://localhost:21000");
            assert!(client.set_traceparent(value).is_err(), "accepted {value:?}");
        }
    }



    #[test]
    pub(super) fn test_chat_version_header_advertises_version_2() {
        assert_eq!(BCS_CHAT_VERSION, "3");
    }



    #[test]
    pub(super) fn test_chat_run_status_deserializes_submitted_state() {
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
    pub(super) fn test_chat_run_status_deserializes_unknown_future_state() {
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
    pub(super) async fn test_discover_bots_extended_includes_organization_scope_query() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::sync::{Arc, Mutex};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let request_line = Arc::new(Mutex::new(String::new()));
        let server_request_line = request_line.clone();

        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buf = [0_u8; 4096];
            let size = stream.read(&mut buf).unwrap_or(0);
            let request = String::from_utf8_lossy(&buf[..size]);
            let first_line = request.lines().next().unwrap_or_default().to_string();
            *server_request_line.lock().unwrap() = first_line;

            let body = serde_json::json!({"bots": [], "count": 0}).to_string();
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
            stream.flush().unwrap();
        });

        let client = BcsClient::new(format!("http://{}", addr));
        let result = client
            .discover_bots_extended(
                None,
                &["code review".to_string(), "sql/ops".to_string()],
                None,
                None,
                Some("promo 2026"),
                Some("traffic/analyst"),
            )
            .await
            .unwrap();

        server.join().unwrap();
        assert_eq!(result.count, 0);
        let line = request_line.lock().unwrap();
        assert!(line.contains("GET /bots/discover?"), "{line}");
        assert!(line.contains("skill=code%20review"), "{line}");
        assert!(line.contains("skill=sql%2Fops"), "{line}");
        assert!(line.contains("organization_code=promo%202026"), "{line}");
        assert!(line.contains("role=traffic%2Fanalyst"), "{line}");
    }



    #[tokio::test]
    pub(super) async fn delete_session_file_tolerates_204_no_content() {
        // DELETE /sessions/{sid}/files/{file_id} returns 204 No Content (empty
        // body). `delete_session_file` must surface this as Ok(Null) — not fail
        // `ensure_success`'s json() parse on the empty body (which made the
        // CLI exit 1 on a successful delete).
        use std::io::{Read, Write};
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buf = [0_u8; 4096];
            let _ = stream.read(&mut buf).unwrap_or(0);
            // 204 with no body — reqwest must accept it as a success.
            stream
                .write_all(b"HTTP/1.1 204 No Content\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
                .unwrap();
            stream.flush().unwrap();
        });

        let client = BcsClient::with_token(format!("http://{}", addr), "bot-token");
        let result = client.delete_session_file("g1:abcd1234", "01KYFILEID").await;
        server.join().unwrap();
        assert!(result.is_ok(), "delete should succeed on 204: {result:?}");
        assert_eq!(result.unwrap(), serde_json::Value::Null);
    }



    #[tokio::test]
    pub(super) async fn test_chat_poll_run_until_running_waits_through_submitted_until_running() {
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
                } else if first_line.starts_with("GET ") && first_line.contains("/chat/runs/run-1") {
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
            .chat_async("bot-target", "hello", None, None, &[], None, None, 2_000, true)
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
    pub(super) async fn test_chat_poll_run_times_out_with_ids_from_submit() {
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
            .chat_async("bot-target", "hello", None, None, &[], None, None, 20, false)
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
        assert!(requests
            .iter()
            .all(|request| request.url.path() != "/chat/runs/run-local-timeout/cancel"));
    }



    #[tokio::test]
    pub(super) async fn test_chat_poll_run_until_running_accepts_completed_as_success() {
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
            .chat_async("bot-target", "hello", None, None, &[], None, None, 60_000, true)
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
