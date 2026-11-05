use super::*;

    #[tokio::test]
    async fn test_discover_bots_extended_includes_organization_scope_query() {
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
