use bcs_cli::{BcsClient, CallerContext};

#[test]
fn parent_context_requires_actor_and_ignores_shared_tokens() {
    assert!(CallerContext::new(Some("agentpass-subbots"), None).is_err());
    let caller = CallerContext::new(Some("agentpass-subbots"), Some("bot-a")).unwrap();
    assert_eq!(caller.select_token(None, Some("ambient"), Some("session")), None);
    assert_eq!(caller.select_token(Some("explicit"), Some("ambient"), None), Some("explicit"));
    assert_eq!(caller.actor_id(), Some("bot-a"));
    assert!(CallerContext::new(None, Some(" bad ")).is_err());
}

#[tokio::test]
async fn actor_is_attached_to_authenticated_requests() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut bytes = vec![0; 8192];
        let count = socket.read(&mut bytes).await.unwrap();
        let request = String::from_utf8_lossy(&bytes[..count]).to_lowercase();
        socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}").await.unwrap();
        request
    });
    let mut client = BcsClient::with_token(&url, "static-a");
    client.set_actor_id(Some("bot-a")).unwrap();
    let _ = client.health_check().await;
    let request = server.await.unwrap();
    assert!(request.contains("x-bcn-actor-bot-id: bot-a"));
    assert!(request.contains("authorization: bearer static-a"));
}

#[tokio::test]
async fn static_token_resolves_its_own_bot_without_a_shared_session_uuid() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url=format!("http://{}/deployment/api/v1/collaboration",listener.local_addr().unwrap());
    let task=tokio::spawn(async move {
        let (mut socket,_)=listener.accept().await.unwrap();
        let mut bytes=vec![0;8192];let count=socket.read(&mut bytes).await.unwrap();
        assert!(String::from_utf8_lossy(&bytes[..count]).contains("GET /deployment/api/v1/collaboration/bots/me "));
        assert_eq!(String::from_utf8_lossy(&bytes[..count]).to_lowercase().matches("\nauthorization:").count(),1);
        let body=r#"{"code":20000,"data":{"bot":{"bot_id":"bot-a"}}}"#;
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),body).as_bytes()).await.unwrap();
    });
    assert_eq!(BcsClient::with_token(url,"static-a").current_bot_uuid().await.unwrap(),"bot-a");
    task.await.unwrap();
}
