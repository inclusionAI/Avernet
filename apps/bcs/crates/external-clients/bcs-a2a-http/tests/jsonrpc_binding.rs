use axum::{
    Json, Router,
    extract::State,
    http::HeaderMap,
    response::{IntoResponse, Response},
    routing::post,
};
use bcs_a2a_http::jsonrpc::JsonRpcClient;
use futures::StreamExt;
use serde_json::{Value, json};
use std::sync::Arc;

async fn rpc(
    State(calls): State<Arc<tokio::sync::Mutex<Vec<Value>>>>,
    headers: HeaderMap,
    Json(request): Json<Value>,
) -> Response {
    assert_eq!(headers["a2a-version"], "1.0");
    assert_eq!(headers["a2a-extensions"], "test-extension");
    assert_eq!(request["jsonrpc"], "2.0");
    assert_eq!(request["params"]["tenant"], "selected-tenant");
    calls.lock().await.push(request.clone());
    if request["params"]["id"] == "header-error" {
        return Json(json!({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32001,"message":"private diagnostic"}})).into_response();
    }
    if request["params"]["id"] == "http-error" {
        return (axum::http::StatusCode::BAD_REQUEST,
            Json(json!({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32001,"message":"private diagnostic"}}))).into_response();
    }
    let task = json!({"id":"任务/1","contextId":"context","status":{"state":"TASK_STATE_WORKING"}});
    let result = match request["method"].as_str().unwrap() {
        "SendMessage" => json!({"task":task}),
        "GetTask" | "CancelTask" => task.clone(),
        "SendStreamingMessage" | "SubscribeToTask" => {
            let mut events = vec![
                json!({"task":task}),
                json!({"artifactUpdate":{"taskId":"任务/1","contextId":"context",
                "artifact":{"artifactId":"text","parts":[{"text":"你好"}]},"append":true}}),
            ];
            if request["params"]["id"] == "bad-stream" {
                events = vec![json!({"task":task})];
            }
            let mut wire = events
                .iter()
                .map(|result| {
                    format!(
                        "data: {}\r\n\r\n",
                        json!({"jsonrpc":"2.0","id":request["id"],"result":result})
                    )
                })
                .collect::<String>();
            if request["params"]["id"] == "bad-stream" {
                wire.push_str(&format!("data: {}\n\n",json!({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32001,"message":"private diagnostic"}})));
            }
            return ([("content-type", "text/event-stream")], wire).into_response();
        }
        method => panic!("unexpected method: {method}"),
    };
    if request["params"]["id"] == "missing" {
        return Json(json!({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32001,"message":"private diagnostic",
            "data":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"TASK_NOT_FOUND","domain":"a2a-protocol.org"}]}})).into_response();
    }
    let id = if request["params"]["id"] == "wrong-id" {
        json!("unrelated")
    } else {
        request["id"].clone()
    };
    Json(json!({"jsonrpc":"2.0","id":id,"result":result})).into_response()
}

async fn fixture() -> (
    JsonRpcClient,
    Arc<tokio::sync::Mutex<Vec<Value>>>,
    tokio::task::JoinHandle<()>,
) {
    let calls = Arc::new(tokio::sync::Mutex::new(vec![]));
    let router = Router::new()
        .route("/agents/orders/rpc", post(rpc))
        .with_state(calls.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!(
        "http://{}/agents/orders/rpc",
        listener.local_addr().unwrap()
    );
    let task = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    let mut headers = HeaderMap::new();
    headers.insert("a2a-extensions", "test-extension".parse().unwrap());
    let http = bcs_network_tracing::ClientWithDefaults::new(
        reqwest::Client::builder().no_proxy().build().unwrap(), headers,
    );
    (
        JsonRpcClient::new(http, url, Some("selected-tenant".into())),
        calls,
        task,
    )
}

#[tokio::test]
async fn all_five_methods_share_the_selected_endpoint_and_standard_models() {
    let (client, calls, server) = fixture().await;
    let send = serde_json::from_value(
        json!({"message":{"messageId":"message-1","role":"ROLE_USER","parts":[{"text":"hello"}]},
        "tenant":"caller-tenant","configuration":{"returnImmediately":true}}),
    )
    .unwrap();
    client.send_message(&send).await.unwrap();
    let events: Vec<_> = client
        .send_streaming_message(&send)
        .await
        .unwrap()
        .collect()
        .await;
    assert_eq!(events.len(), 2);
    assert!(events.iter().all(Result::is_ok));
    let get = serde_json::from_value(json!({"id":"任务/1","historyLength":3})).unwrap();
    assert_eq!(client.get_task(&get).await.unwrap().id, "任务/1");
    client
        .cancel_task(&serde_json::from_value(json!({"id":"任务/1"})).unwrap())
        .await
        .unwrap();
    let events: Vec<_> = client
        .subscribe_to_task(&serde_json::from_value(json!({"id":"任务/1"})).unwrap())
        .await
        .unwrap()
        .collect()
        .await;
    assert_eq!(events.len(), 2);
    assert!(events.iter().all(Result::is_ok));
    let calls = calls.lock().await;
    assert_eq!(
        calls
            .iter()
            .map(|v| v["method"].as_str().unwrap())
            .collect::<Vec<_>>(),
        vec![
            "SendMessage",
            "SendStreamingMessage",
            "GetTask",
            "CancelTask",
            "SubscribeToTask"
        ]
    );
    assert_eq!(calls[2]["params"]["historyLength"], 3);
    assert_eq!(calls[0]["params"]["message"]["messageId"], "message-1");
    assert_eq!(
        calls[0]["params"]["configuration"]["returnImmediately"],
        true
    );
    let ids = calls
        .iter()
        .map(|v| v["id"].as_str().unwrap())
        .collect::<std::collections::HashSet<_>>();
    assert_eq!(ids.len(), 5);
    server.abort();
}

#[tokio::test]
async fn protocol_errors_and_mismatched_response_ids_never_become_success() {
    let (client, _, server) = fixture().await;
    let http_error = client.get_task(&serde_json::from_value(json!({"id":"http-error"})).unwrap()).await.unwrap_err();
    assert_eq!(http_error.code, -32001, "{http_error:?}");
    let error = client
        .get_task(&serde_json::from_value(json!({"id":"missing"})).unwrap())
        .await
        .unwrap_err();
    assert_eq!(error.code, -32001);
    assert!(!error.message.contains("private"));
    assert_eq!(error.details.unwrap().len(), 1);
    assert!(
        client
            .get_task(&serde_json::from_value(json!({"id":"wrong-id"})).unwrap())
            .await
            .is_err()
    );
    let error = client
        .subscribe_to_task(&serde_json::from_value(json!({"id":"header-error"})).unwrap())
        .await
        .err()
        .unwrap();
    assert_eq!(error.code, -32001);
    let events: Vec<_> = client
        .subscribe_to_task(&serde_json::from_value(json!({"id":"bad-stream"})).unwrap())
        .await
        .unwrap()
        .collect()
        .await;
    assert_eq!(events.len(), 2);
    assert!(events[0].is_ok());
    assert_eq!(events[1].as_ref().unwrap_err().code, -32001);
    server.abort();
}
