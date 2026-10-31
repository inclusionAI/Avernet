use a2a::StreamResponse;
use bcs_a2a_http::client::bind_rest_client;
use futures::StreamExt;
use serde_json::json;
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::TcpListener};

async fn server(status: &str, body: String) -> (String, tokio::task::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}/a2a/bots/demo", listener.local_addr().unwrap());
    let header = format!("HTTP/1.1 {status}\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = Vec::new();
        loop {
            let mut chunk = [0; 4096]; let size = stream.read(&mut chunk).await.unwrap();
            assert_ne!(size, 0); request.extend_from_slice(&chunk[..size]);
            let text = String::from_utf8_lossy(&request);
            if let Some((head, content)) = text.split_once("\r\n\r\n") {
                let length = head.lines().filter_map(|line|line.split_once(':'))
                    .find(|(name, _)|name.eq_ignore_ascii_case("content-length"))
                    .map(|(_, value)|value.trim().parse::<usize>().unwrap()).unwrap_or(0);
                if content.len() >= length { break; }
            }
            assert!(request.len() < 16384);
        }
        stream.write_all(header.as_bytes()).await.unwrap();
        // Force UTF-8 and CRLF framing across network writes.
        for chunk in body.as_bytes().chunks(7) {
            if stream.write_all(chunk).await.is_err() { break; }
            tokio::task::yield_now().await;
        }
        String::from_utf8(request).unwrap()
    });
    (base, task)
}

#[tokio::test]
async fn canonical_post_preserves_resource_identity_and_selected_tenant() {
    let body = ": heartbeat\r\n\r\ndata: {\r\ndata: \"task\":{\"id\":\"任务/a\",\"contextId\":\"context\",\"status\":{\"state\":\"TASK_STATE_WORKING\"}}}\r\n\r\n";
    let (base, task) = server("200 OK", body.into()).await;
    let client = bind_rest_client(reqwest::Client::new(), base, Some("selected-tenant".into()));
    let request = serde_json::from_value(json!({"id":"任务/a", "tenant":"untrusted-tenant"})).unwrap();
    let events: Vec<_> = client.subscribe_to_task(&request).await.unwrap().collect().await;
    assert_eq!(events.len(), 1);
    assert!(matches!(&events[0], Ok(StreamResponse::Task(task)) if task.id == "任务/a"));
    let request = task.await.unwrap();
    assert!(request.starts_with("POST /a2a/bots/demo/tasks/%E4%BB%BB%E5%8A%A1%2Fa:subscribe HTTP/1.1"));
    assert!(request.to_ascii_lowercase().contains("a2a-version: 1.0"));
    let body: serde_json::Value = serde_json::from_str(request.split_once("\r\n\r\n").unwrap().1).unwrap();
    assert_eq!(body["id"], "任务/a");
    assert_eq!(body["tenant"], "selected-tenant");
}

#[tokio::test]
async fn bare_cr_event_ending_is_accepted_and_incomplete_event_is_not() {
    for (body, valid) in [
        ("data: {\"task\":{\"id\":\"task\",\"contextId\":\"ctx\",\"status\":{\"state\":\"TASK_STATE_WORKING\"}}}\r\r", true),
        ("data: {\"task\":{\"id\":\"task\",\"contextId\":\"ctx\",\"status\":{\"state\":\"TASK_STATE_WORKING\"}}}\n", false),
    ] {
        let (base, task) = server("200 OK", body.into()).await;
        let client = bind_rest_client(reqwest::Client::new(), base, None);
        let events: Vec<_> = client.subscribe_to_task(&serde_json::from_value(json!({"id":"task"})).unwrap()).await.unwrap().collect().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].is_ok(), valid);
        task.await.unwrap();
    }
}

#[tokio::test]
async fn subscription_errors_do_not_fall_back_to_get_or_legacy_route() {
    let (base, task) = server("404 Not Found", String::new()).await;
    let client = bind_rest_client(reqwest::Client::new(), base, None);
    assert!(client.subscribe_to_task(&serde_json::from_value(json!({"id":"task"})).unwrap()).await.is_err());
    assert!(task.await.unwrap().starts_with("POST /a2a/bots/demo/tasks/task:subscribe "));
}

#[tokio::test]
async fn task_and_subscription_preserve_nested_protocol_error_reasons() {
    for operation in ["get", "cancel", "subscribe", "send", "async-send"] {
        for nested in [true, false] {
            let reason = if operation == "cancel" { "TASK_NOT_CANCELABLE" } else { "TASK_NOT_FOUND" };
            let expected = if operation == "cancel" { a2a::error_code::TASK_NOT_CANCELABLE } else { a2a::error_code::TASK_NOT_FOUND };
            let status = json!({"code":5,"message":"untrusted diagnostic text", "details":[
                {"unsupported":"detail is ignored individually"},
                {"@type":"type.googleapis.com/google.rpc.ErrorInfo", "reason":reason,"domain":"a2a-protocol.org"}
            ]});
            let body = if nested { json!({"error":status}) } else { status };
            let (base, task) = server("404 Not Found", body.to_string()).await;
            let client = bind_rest_client(reqwest::Client::new(), base, None);
            let error = match operation {
                "get" => client.get_task(&serde_json::from_value(json!({"id":"task"})).unwrap()).await.err().unwrap(),
                "cancel" => client.cancel_task(&serde_json::from_value(json!({"id":"task"})).unwrap()).await.err().unwrap(),
                "send" => client.send_streaming_message(&serde_json::from_value(json!({"message":{"messageId":"first","role":"ROLE_USER","parts":[{"text":"hello"}]}})).unwrap()).await.err().unwrap(),
                "async-send" => client.send_message(&serde_json::from_value(json!({"message":{"messageId":"first","role":"ROLE_USER","parts":[{"text":"hello"}]}})).unwrap()).await.err().unwrap(),
                _ => client.subscribe_to_task(&serde_json::from_value(json!({"id":"task"})).unwrap()).await.err().unwrap(),
            };
            assert_eq!(error.code, expected, "{operation}, nested={nested}");
            assert!(!error.message.contains("untrusted"));
            assert_eq!(error.details.unwrap().len(), 1);
            task.await.unwrap();
        }
    }
}

#[tokio::test]
async fn untyped_reason_and_http_status_do_not_invent_protocol_error() {
    for body in [
        json!({"error":{"details":[{"@type":"example.invalid/Other","reason":"TASK_NOT_FOUND"}]}}).to_string(),
        json!({"error":{"code":5,"message":"TASK_NOT_FOUND"}}).to_string(),
        "not json".into(),
    ] {
        let (base, task) = server("404 Not Found", body).await;
        let client = bind_rest_client(reqwest::Client::new(), base, None);
        let error = client.get_task(&serde_json::from_value(json!({"id":"task"})).unwrap()).await.unwrap_err();
        assert_eq!(error.code, a2a::error_code::INTERNAL_ERROR);
        task.await.unwrap();
    }
}

#[tokio::test]
async fn captured_java_sdk_error_fixture_retains_task_not_found() {
    let (base, task) = server("404 Not Found", include_str!("fixtures/java-task-not-found.json").into()).await;
    let client = bind_rest_client(reqwest::Client::new(), base, None);
    let error = client.get_task(&serde_json::from_value(json!({"id":"missing"})).unwrap()).await.unwrap_err();
    assert_eq!(error.code,a2a::error_code::TASK_NOT_FOUND);
    assert_eq!(error.details.unwrap()[0].value["domain"],"a2a-protocol.org");
    task.await.unwrap();
}
