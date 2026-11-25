//! The low-level clients keep one fact: whether a connection was established.
//! A refused connection certainly did not reach the peer; a connection that
//! broke after the request was read is ambiguous.
use bcs_a2a_http::client::bind_rest_client;
use bcs_a2a_http::grpc_client::GrpcClient;
use bcs_a2a_http::jsonrpc::JsonRpcClient;
use serde_json::json;
use tokio::io::AsyncReadExt;

const NOT_CONNECTED: &str = "A2A connection not established";

/// A loopback address nothing listens on.
async fn closed_port() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    format!("http://{address}")
}

/// A server that reads the request and closes without answering.
async fn closes_after_reading() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        while let Ok((mut socket, _)) = listener.accept().await {
            let mut buffer = [0u8; 4096];
            let _ = socket.read(&mut buffer).await;
        }
    });
    format!("http://{address}")
}

fn http() -> reqwest::Client {
    reqwest::Client::builder().no_proxy().build().unwrap()
}

fn send_request() -> a2a::SendMessageRequest {
    serde_json::from_value(json!({"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}})).unwrap()
}

fn task_request() -> a2a::GetTaskRequest {
    serde_json::from_value(json!({"id": "rt-1"})).unwrap()
}

#[tokio::test]
async fn a_refused_connection_is_marked_on_every_http_path() {
    let base = closed_port().await;
    let rest = bind_rest_client(http(), base.clone(), None);
    assert_eq!(rest.send_message(&send_request()).await.unwrap_err().message, NOT_CONNECTED);
    assert_eq!(rest.get_task(&task_request()).await.unwrap_err().message, NOT_CONNECTED);
    assert_eq!(rest.send_streaming_message(&send_request()).await.err().unwrap().message, NOT_CONNECTED);

    let rpc = JsonRpcClient::new(http(), format!("{base}/rpc"), None);
    assert_eq!(rpc.send_message(&send_request()).await.unwrap_err().message, NOT_CONNECTED);
    assert_eq!(rpc.send_streaming_message(&send_request()).await.err().unwrap().message, NOT_CONNECTED);
}

#[tokio::test]
async fn a_connection_that_breaks_after_the_request_keeps_the_generic_message() {
    let base = closes_after_reading().await;
    let rest = bind_rest_client(http(), base.clone(), None);
    assert_eq!(rest.get_task(&task_request()).await.unwrap_err().message, "A2A Task request interrupted");
    assert_eq!(
        rest.send_streaming_message(&send_request()).await.err().unwrap().message,
        "A2A subscription connection failed"
    );
    let rpc = JsonRpcClient::new(http(), format!("{base}/rpc"), None);
    assert_eq!(rpc.send_message(&send_request()).await.unwrap_err().message, "A2A JSON-RPC request interrupted");
}

#[tokio::test]
async fn a_refused_grpc_connection_is_marked() {
    let base = closed_port().await;
    let channel = tonic::transport::Endpoint::from_shared(base).unwrap().connect_lazy();
    let client = GrpcClient::new(channel, tonic::metadata::MetadataMap::new(), None);
    assert_eq!(client.get_task(&task_request()).await.unwrap_err().message, NOT_CONNECTED);
    assert_eq!(client.send_streaming_message(&send_request()).await.err().unwrap().message, NOT_CONNECTED);
}
