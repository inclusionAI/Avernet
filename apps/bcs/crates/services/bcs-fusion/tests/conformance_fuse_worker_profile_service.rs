use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use bcs_fuse_client::FuseClient;
use bcs_fusion::FuseWorkerProfileService;
use bcs_service_api::core::{WorkerProfile, WorkerProfileCoreService, WorkerRecommendCommand};
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::time::timeout;

const IO_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, PartialEq)]
struct CapturedRequest {
    method: String,
    path: String,
    body: Value,
}

#[test]
fn fuse_worker_profile_service_declares_the_core_contract_explicitly() {
    let implementation = include_str!("../src/core/fuse_backed_worker_profiles.rs");

    assert!(
        implementation.contains("impl WorkerProfileCoreService for FuseWorkerProfileService"),
        "FuseWorkerProfileService must implement the Core contract directly, not a deprecated alias"
    );
}

async fn spawn_worker_profile_server(
    responses: Vec<Value>,
) -> (String, tokio::task::JoinHandle<Vec<CapturedRequest>>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind recommend test server");
    let address = listener
        .local_addr()
        .expect("recommend test server address");

    let handle = tokio::spawn(async move {
        let mut requests = Vec::new();
        for response in responses {
            let captured = timeout(IO_TIMEOUT, async {
                let (mut socket, _) = listener
                    .accept()
                    .await
                    .expect("accept worker-profile request");
                let mut request = Vec::new();
                let header_end = loop {
                    let mut chunk = [0u8; 1024];
                    let read = socket
                        .read(&mut chunk)
                        .await
                        .expect("read worker-profile request");
                    assert!(
                        read > 0,
                        "worker-profile request ended before headers completed"
                    );
                    request.extend_from_slice(&chunk[..read]);
                    if let Some(position) = request
                        .windows(4)
                        .position(|window| window == b"\r\n\r\n")
                    {
                        break position + 4;
                    }
                };

                let headers = std::str::from_utf8(&request[..header_end])
                    .expect("worker-profile request headers are utf-8");
                let mut request_line = headers
                    .lines()
                    .next()
                    .expect("worker-profile request line")
                    .split_whitespace();
                let method = request_line
                    .next()
                    .expect("worker-profile request method")
                    .to_string();
                let path = request_line
                    .next()
                    .expect("worker-profile request path")
                    .to_string();
                let content_length = headers
                    .lines()
                    .find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().expect("valid content length"))
                    })
                    .expect("worker-profile request has content length");

                while request.len() - header_end < content_length {
                    let mut chunk = [0u8; 1024];
                    let read = socket
                        .read(&mut chunk)
                        .await
                        .expect("read worker-profile body");
                    assert!(
                        read > 0,
                        "worker-profile request ended before body completed"
                    );
                    request.extend_from_slice(&chunk[..read]);
                }

                let body = serde_json::from_slice(
                    &request[header_end..header_end + content_length],
                )
                .expect("worker-profile request body is json");
                let response_body = serde_json::to_vec(&response).expect("serialize response");
                let response_headers = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    response_body.len()
                );
                socket
                    .write_all(response_headers.as_bytes())
                    .await
                    .expect("write worker-profile response headers");
                socket
                    .write_all(&response_body)
                    .await
                    .expect("write worker-profile response body");

                CapturedRequest { method, path, body }
            })
            .await
            .expect("worker-profile fixture timed out");
            requests.push(captured);
        }

        requests
    });

    (format!("http://{address}"), handle)
}

#[tokio::test]
async fn conformance_fuse_worker_profile_service_forwards_core_recommend_command() {
    let response = json!({
        "driver_bot_id": "bot-driver",
        "recommendations": [
            {
                "profile_key": "default:bot-worker:default",
                "worker_id": "bot-worker",
                "score": 0.91,
                "reasons": [],
                "short_profile": "Rust expert"
            }
        ]
    });
    let batch_response = json!({
        "success": true,
        "data": {
            "bot-worker": {
                "name": "Worker",
                "profile_tags": {"language": "rust"}
            }
        },
        "not_found_ids": []
    });
    let (base_url, request_handle) =
        spawn_worker_profile_server(vec![response.clone(), batch_response]).await;
    let client = Arc::new(FuseClient::for_test_with_url(base_url).expect("construct client"));
    let service = FuseWorkerProfileService::new(client);

    let core_service: &dyn WorkerProfileCoreService = &service;
    bcs_test_support::contract::core::worker_profile_core_service_contract_tests(
        core_service,
        WorkerRecommendCommand {
            query: "元歌协作".to_string(),
            top_k: 7,
            min_score: 0.42,
        },
        &[("bot-worker", 0.91, Some("Rust expert"))],
        &response,
        &["bot-worker".to_string()],
        &[WorkerProfile {
            worker_id: "bot-worker".to_string(),
            tags: BTreeMap::from([("language".to_string(), serde_json::json!("rust"))]),
        }],
    )
    .await;

    let requests = timeout(IO_TIMEOUT, request_handle)
        .await
        .expect("worker-profile fixture join timed out")
        .expect("worker-profile requests captured");
    assert_eq!(
        requests,
        vec![
            CapturedRequest {
                method: "POST".to_string(),
                path: "/api/v1/recommend".to_string(),
                body: json!({
                    "question": "元歌协作",
                    "topK": 7,
                    "min_score": 0.42
                }),
            },
            CapturedRequest {
                method: "POST".to_string(),
                path: "/v1/workers/batch".to_string(),
                body: json!({"worker_ids": ["bot-worker"]}),
            },
        ]
    );
}
