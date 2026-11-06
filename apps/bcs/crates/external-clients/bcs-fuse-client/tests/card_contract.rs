use bcs_fuse_client::{CardResourceType, CardSearchRequest, CardWriteRequest, FuseClient};
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

async fn endpoint(status: &str, body: Value) -> (FuseClient, tokio::task::JoinHandle<(String, Value)>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut config = bcs_fuse_client::BcsFuseConfig {
        url: format!("http://{}/", listener.local_addr().unwrap()), ..Default::default()
    };
    config.set_resolved_authorization("test".into());
    let client = FuseClient::new(&config).unwrap();
    let status = status.to_string();
    let task = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut bytes = Vec::new();
        let end = loop {
            let mut chunk = [0; 1024];
            let count = socket.read(&mut chunk).await.unwrap();
            assert!(count > 0);
            bytes.extend_from_slice(&chunk[..count]);
            if let Some(end) = bytes.windows(4).position(|v| v == b"\r\n\r\n") { break end + 4; }
        };
        let headers = String::from_utf8(bytes[..end].to_vec()).unwrap();
        assert!(headers.lines().any(|line| line.eq_ignore_ascii_case("authorization: Bearer test")),
            "Card requests must preserve configured authorization headers");
        let size: usize = headers.lines().find_map(|line| {
            let (key, value) = line.split_once(':')?;
            key.eq_ignore_ascii_case("content-length").then(|| value.trim().parse().unwrap())
        }).unwrap_or(0);
        while bytes.len() < end + size {
            let mut chunk = [0; 1024];
            let count = socket.read(&mut chunk).await.unwrap();
            assert!(count > 0);
            bytes.extend_from_slice(&chunk[..count]);
        }
        let request = if size == 0 { Value::Null } else { serde_json::from_slice(&bytes[end..end + size]).unwrap() };
        let body = body.to_string();
        socket.write_all(format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
        (headers.lines().next().unwrap().to_owned(), request)
    });
    (client, task)
}

#[tokio::test]
async fn card_write_uses_v2_path_and_fuse_envelope() {
    let (client, task) = endpoint("202 Accepted", json!({"saved":true,"card_version":7})).await;
    let response = client.put_card("group:billing", &CardWriteRequest {
        resource_type: CardResourceType::Group,
        labels: [("visibility".into(), vec!["public".into()])].into(),
        card: json!({"name":"Billing","skills":[]}),
    }).await.unwrap();
    assert_eq!(response.card_version, 7);
    let (line, body) = task.await.unwrap();
    assert_eq!(line, "PUT /api/v2/agent-cards/group:billing HTTP/1.1");
    assert_eq!(body, json!({"resource_type":"group","labels":{"visibility":["public"]},"card":{"name":"Billing","skills":[]}}));
}

#[tokio::test]
async fn search_uses_fuse_v2_contract() {
    let (client, task) = endpoint("200 OK", json!({
        "items":[{"id":"bot:1","name":"Billing","summary":"Invoices","score":0.9,"score_source":"rerank","extensions":{}}],
        "status":"degraded","reasons":["vector_unavailable"],"effective":{"limit":5}
    })).await;
    let response = client.search_cards(&CardSearchRequest {
        query:"billing".into(), limit:5,
        filters: [("resource_type".into(), vec!["agent".into()])].into(),
    }).await.unwrap();
    assert_eq!(response.items[0].summary, "Invoices");
    assert_eq!(response.status, "degraded");
    let (line, body) = task.await.unwrap();
    assert_eq!(line, "POST /api/v2/search HTTP/1.1");
    assert_eq!(body, json!({"query":"billing","limit":5,"filters":{"resource_type":["agent"]}}));
}

#[tokio::test]
async fn delete_accepts_fuse_ack_but_not_missing_route() {
    let (client, task) = endpoint("202 Accepted", json!({"deleted":true})).await;
    client.delete_card("bot:1").await.unwrap();
    let (line, body) = task.await.unwrap();
    assert_eq!(line, "DELETE /api/v2/agent-cards/bot:1 HTTP/1.1");
    assert!(body.is_null());
    let (client, task) = endpoint("404 Not Found", json!({"detail":"Not Found"})).await;
    assert!(client.delete_card("bot:1").await.is_err());
    task.await.unwrap();
}
