use super::*;
use std::time::Duration;

    #[test]
    fn guess_mime_from_extension_known_types() {
        assert_eq!(
            BcsClient::guess_mime_from_extension("report.pdf"),
            Some("application/pdf".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("DATA.CSV"),
            Some("text/csv".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("pic.jpeg"),
            Some("image/jpeg".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("pic.jpg"),
            Some("image/jpeg".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("notes.md"),
            Some("text/markdown".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("data.json"),
            Some("application/json".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("archive.zip"),
            Some("application/zip".into())
        );
        assert_eq!(
            BcsClient::guess_mime_from_extension("model.bin"),
            Some("application/octet-stream".into())
        );
    }

    #[test]
    fn guess_mime_from_extension_unknown_returns_none() {
        assert_eq!(BcsClient::guess_mime_from_extension("file.xyz"), None);
        assert_eq!(BcsClient::guess_mime_from_extension("noext"), None);
        assert_eq!(BcsClient::guess_mime_from_extension(""), None);
        assert_eq!(BcsClient::guess_mime_from_extension(".hidden"), None);
    }

    #[test]
    fn is_text_mime_recognizes_textual_types() {
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
    async fn guess_charset_returns_none_for_ascii() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ascii.txt");
        tokio::fs::write(&path, b"plain ascii text, no non-ascii bytes")
            .await
            .unwrap();
        assert_eq!(
            BcsClient::guess_charset(path.to_str().unwrap(), 4096).await,
            None
        );
    }

    #[tokio::test]
    async fn guess_charset_detects_utf8_and_cjk() {
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
        assert!(
            !cs_gbk.contains("utf-8"),
            "GBK must not be misdetected as utf-8: {cs_gbk}"
        );
    }

    #[tokio::test]
    async fn guess_charset_detects_large_utf8_truncated() {
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
    async fn guess_charset_handles_utf16_bom() {
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
    async fn explicit_text_mime_still_eligible_for_charset() {
        // The charset-enrichment contract for upload_session_file: an explicit
        // text MIME (e.g. --mime text/markdown on a UTF-8 Chinese file) is
        // treated as textual and gets a charset appended, unless it already
        // carries one. Express via the composing helpers.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("cn.md");
        tokio::fs::write(&path, "你好，世界".as_bytes())
            .await
            .unwrap();

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

    #[tokio::test]
    async fn delete_session_file_tolerates_204_no_content() {
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
                .write_all(
                    b"HTTP/1.1 204 No Content\r\ncontent-length: 0\r\nconnection: close\r\n\r\n",
                )
                .unwrap();
            stream.flush().unwrap();
        });

        let client = BcsClient::with_token(format!("http://{}", addr), "bot-token");
        let result = client
            .delete_session_file("g1:abcd1234", "01KYFILEID")
            .await;
        server.join().unwrap();
        assert!(result.is_ok(), "delete should succeed on 204: {result:?}");
        assert_eq!(result.unwrap(), serde_json::Value::Null);
    }

    // ========================================================================
    // Cross-host Authorization isolation regression test
    // ========================================================================

    /// Ensures that `put_session_file_bytes` does NOT send an `Authorization`
    /// header when the upload URL points to a different host than the BCS
    /// base URL. OSS presigned URLs self-authenticate and must never leak the
    /// bearer token across host boundaries.
    #[tokio::test]
    async fn cross_host_put_omits_authorization() {
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

            let response = "HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n";
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
        let result = client
            .put_session_file_bytes(&upload_url, body, "application/octet-stream")
            .await;

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

    // Regression: `share --ttl N` must send `ttl_seconds` (the ShareRequest DTO
    // field), not `ttl` — serde ignores unknown fields, so `ttl` would be
    // silently dropped and the service would fall back to its default expiry.
    #[tokio::test]
    async fn share_session_file_sends_ttl_seconds_field() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/sessions/s1/files/f1/share"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "share_url": "http://bcs/sessions/shared-file?token=x",
                "share_token": "x",
                "expires_at": 0u64,
            })))
            .mount(&server)
            .await;

        let client = BcsClient::with_token(&server.uri(), "bot-token");
        let _ = client
            .share_session_file("s1", "f1", Some(120))
            .await
            .unwrap();

        let requests = server.received_requests().await.expect("captured requests");
        let req = requests
            .iter()
            .find(|r| r.method.as_str() == "POST" && r.url.path() == "/sessions/s1/files/f1/share")
            .expect("share POST request");
        let body: serde_json::Value = serde_json::from_slice(&req.body).unwrap();
        assert_eq!(
            body["ttl_seconds"], 120,
            "body must carry ttl_seconds, was: {body}"
        );
        assert!(
            body.get("ttl").is_none(),
            "legacy `ttl` key must not be sent (silently ignored by the DTO), was: {body}"
        );
    }
