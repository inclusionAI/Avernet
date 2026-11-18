use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};

use axum::{
    Json, Router,
    body::Bytes,
    extract::State,
    http::{HeaderMap, Uri},
    response::{IntoResponse, Response},
};
use bcs_service_api::RedactedToken;
use serde_json::{Value, json};

use super::*;
use crate::{client::rest_client, jsonrpc::JsonRpcClient};

type Calls = Arc<Mutex<Vec<Option<String>>>>;

async fn respond(
    State(calls): State<Calls>, uri: Uri, headers: HeaderMap, bytes: Bytes,
) -> Response {
    assert_eq!(headers["authorization"], "Bearer fixture");
    assert_eq!(headers["a2a-version"], "1.0");
    assert!(headers.get_all("x-fixture-identity").iter().count() <= 1);
    calls.lock().unwrap().push(
        headers.get("x-fixture-identity").map(|v| v.to_str().unwrap().to_owned()),
    );

    let task = json!({"id":"t","contextId":"c","status":{"state":"TASK_STATE_WORKING"}});
    let rpc: Value = serde_json::from_slice(&bytes).unwrap_or_default();
    let is_rpc = uri.path() == "/rpc";
    let streaming = headers.get("accept").is_some_and(|v| v == "text/event-stream");
    let result = if streaming || uri.path().ends_with("message:send") || rpc["method"] == "SendMessage" {
        json!({"task":task})
    } else {
        task
    };
    let result = if is_rpc {
        json!({"jsonrpc":"2.0","id":rpc["id"],"result":result})
    } else {
        result
    };
    if streaming {
        ([("content-type", "text/event-stream")], format!("data: {result}\n\n")).into_response()
    } else {
        Json(result).into_response()
    }
}

struct Policy {
    origin: String,
    header: &'static str,
    calls: AtomicUsize,
}

impl OutboundAppIdentity for Policy {
    fn credential_for(&self, target: &OutboundTarget<'_>) -> Result<Option<SensitiveHeader>, CredentialError> {
        if target.provider_id != "enabled" {
            return Ok(None);
        }
        if target.origin != self.origin {
            return Err(CredentialError::DestinationRejected);
        }
        let sequence = self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(Some(SensitiveHeader {
            name: self.header.into(),
            value: RedactedToken::new(format!("fixture-{sequence}")),
        }))
    }
}

#[tokio::test]
async fn identity_is_per_request_in_both_bindings_and_failures_send_nothing() {
    let calls = Calls::default();
    let router = Router::new().fallback(respond).with_state(calls.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
    let policy = Arc::new(Policy {
        origin: origin.clone(), header: "x-fixture-identity", calls: AtomicUsize::new(0),
    });
    let mut headers = HeaderMap::new();
    headers.insert("authorization", HeaderValue::from_static("Bearer fixture"));
    let transport = ClientWithDefaults::new(reqwest::Client::builder().no_proxy().build().unwrap(), headers);
    let enabled = || HttpClient::new(transport.clone()).with_identity(policy.clone(), "enabled".into());
    let rest = rest_client(enabled(), origin.clone(), None);
    let rpc = JsonRpcClient::with_http(enabled(), format!("{origin}/rpc"), None);
    let send = serde_json::from_value(
        json!({"message":{"messageId":"m","role":"ROLE_USER","parts":[{"text":"hello"}]}}),
    ).unwrap();
    let get = serde_json::from_value(json!({"id":"t"})).unwrap();
    let cancel = serde_json::from_value(json!({"id":"t"})).unwrap();
    let subscribe = serde_json::from_value(json!({"id":"t"})).unwrap();
    rest.send_message(&send).await.unwrap();
    drop(rest.send_streaming_message(&send).await.unwrap());
    rest.get_task(&get).await.unwrap();
    rest.cancel_task(&cancel).await.unwrap();
    drop(rest.subscribe_to_task(&subscribe).await.unwrap());
    rpc.send_message(&send).await.unwrap();
    drop(rpc.send_streaming_message(&send).await.unwrap());
    rpc.get_task(&get).await.unwrap();
    rpc.cancel_task(&cancel).await.unwrap();
    drop(rpc.subscribe_to_task(&subscribe).await.unwrap());

    let other = HttpClient::new(transport.clone()).with_identity(policy.clone(), "other".into());
    rest_client(other, origin.clone(), None).get_task(&get).await.unwrap();
    rest_client(HttpClient::new(transport.clone()), origin.clone(), None).get_task(&get).await.unwrap();
    for (allowed_origin, header, expected) in [
        ("https://different.invalid", "x-fixture-identity", DESTINATION_REJECTED),
        (origin.as_str(), "authorization", INVALID_CREDENTIAL),
    ] {
        let policy = Arc::new(Policy {
            origin: allowed_origin.into(), header, calls: AtomicUsize::new(0),
        });
        let http = HttpClient::new(transport.clone()).with_identity(policy, "enabled".into());
        let error = rest_client(http, origin.clone(), None).get_task(&get).await.unwrap_err();
        assert_eq!(identity_failure(&error), Some(expected));
    }

    let expected: Vec<_> = (0..10).map(|seq| Some(format!("fixture-{seq}")))
        .chain([None, None]).collect();
    assert_eq!(*calls.lock().unwrap(), expected);
    assert_eq!(policy.calls.load(Ordering::SeqCst), 10);
    server.abort();
}
