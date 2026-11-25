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
    for http in [
        HttpClient::new(transport.clone()),
        HttpClient::new(transport.clone()).with_identity(policy.clone(), "other".into()),
    ] {
        let error = rest_client(http.require_identity(), origin.clone(), None).get_task(&get).await.unwrap_err();
        assert_eq!(identity_failure(&error), Some(CREDENTIAL_UNAVAILABLE));
    }
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

#[tokio::test]
async fn identity_and_user_id_are_sent_without_authorization_on_streaming_request() {
    use tracing::{
        Event, Metadata, Subscriber,
        field::{Field, Visit},
        span::{Attributes, Id, Record},
        subscriber::Interest,
    };
    use std::collections::BTreeMap;

    #[derive(Clone, Default)]
    struct ResponseLogs(Arc<Mutex<Vec<BTreeMap<String, String>>>>);

    impl Subscriber for ResponseLogs {
        fn register_callsite(&self, _: &'static Metadata<'static>) -> Interest {
            Interest::sometimes()
        }

        fn enabled(&self, metadata: &Metadata<'_>) -> bool {
            metadata.fields().field("response_fields").is_some()
        }
        fn new_span(&self, _: &Attributes<'_>) -> Id { Id::from_u64(1) }
        fn record(&self, _: &Id, _: &Record<'_>) {}
        fn record_follows_from(&self, _: &Id, _: &Id) {}
        fn enter(&self, _: &Id) {}
        fn exit(&self, _: &Id) {}
        fn event(&self, event: &Event<'_>) {
            struct Fields(BTreeMap<String, String>);
            impl Visit for Fields {
                fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
                    self.0.insert(field.name().into(), format!("{value:?}"));
                }

                fn record_str(&mut self, field: &Field, value: &str) {
                    self.0.insert(field.name().into(), value.into());
                }
            }
            let mut fields = Fields(BTreeMap::new());
            event.record(&mut fields);
            self.0.lock().unwrap().push(fields.0);
        }
    }

    async fn peer(uri: Uri, headers: HeaderMap, Json(rpc): Json<Value>) -> Response {
        assert!(!headers.contains_key("authorization"));
        if uri.path() == "/error" {
            let json = r#"{"code":"DENIED","message":"invalid identity","errorMsg":"echo fixture-1"}"#;
            return ([("content-type", "application/json")], format!("{json:<88}")).into_response();
        }
        if uri.path() == "/chunked" {
            let chunks = [
                Ok::<_, std::io::Error>(Bytes::from_static(br#"{"error":{"code":410,"#)),
                Ok(Bytes::from_static(br#""message":"stream rejected"}}"#)),
            ];
            let body = axum::body::Body::from_stream(futures::stream::iter(chunks));
            return ([("content-type", "application/json")], body).into_response();
        }
        if uri.path() == "/plain" {
            return ([("content-type", "text/plain")], "never-log-unstructured-body").into_response();
        }
        if uri.path() == "/incomplete-sse" {
            return ([("content-type", "text/event-stream")], "data: {\"code\":410}\n").into_response();
        }
        assert_eq!(headers["x-one-id"], "fixture-0");
        assert_eq!(headers["x-example-agent-user"], "12345678");
        assert_eq!(rpc["method"], "SendStreamingMessage");
        let event = json!({"jsonrpc":"2.0","id":rpc["id"],"result":{"task":{
            "id":"task","contextId":"context","status":{"state":"TASK_STATE_COMPLETED"}
        }}});
        ([("content-type", "text/event-stream")], format!("data: {event}\n\n")).into_response()
    }

    use futures::StreamExt;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, Router::new().fallback(peer)).await.unwrap(); });
    let policy = Arc::new(Policy {
        origin: origin.clone(), header: "x-one-id", calls: AtomicUsize::new(0),
    });
    let mut headers = HeaderMap::new();
    headers.insert("x-example-agent-user", HeaderValue::from_static("12345678"));
    let transport = ClientWithDefaults::new(reqwest::Client::builder().no_proxy().build().unwrap(), headers);
    let scoped = || HttpClient::new(transport.clone())
        .with_identity(policy.clone(), "enabled".into()).require_identity();
    let logs = ResponseLogs::default();
    let _subscriber = tracing::subscriber::set_default(logs.clone());
    let rpc = JsonRpcClient::with_http(scoped(), format!("{origin}/rpc"), None);
    let send = serde_json::from_value(
        json!({"message":{"messageId":"m","role":"ROLE_USER","parts":[{"text":"hello"}]}}),
    ).unwrap();
    let mut stream = rpc.send_streaming_message(&send).await.unwrap();
    let Some(Ok(a2a::StreamResponse::Task(task))) = stream.next().await else { panic!("expected a Task") };
    assert_eq!(task.status.state, a2a::TaskState::Completed);
    assert!(stream.next().await.is_none());

    for http in [HttpClient::new(transport.clone()), scoped()] {
        let rpc = JsonRpcClient::with_http(http, format!("{origin}/error"), None);
        assert!(rpc.send_streaming_message(&send).await.is_err());
    }
    for path in ["chunked", "plain"] {
        let rpc = JsonRpcClient::with_http(scoped(), format!("{origin}/{path}"), None);
        assert!(rpc.send_streaming_message(&send).await.is_err());
    }
    let rpc = JsonRpcClient::with_http(scoped(), format!("{origin}/incomplete-sse"), None);
    let mut stream = rpc.send_streaming_message(&send).await.unwrap();
    let error = stream.next().await.unwrap().unwrap_err();
    assert_eq!(crate::test_response::submission_failure(&error), "incomplete_sse_event");

    let captured = logs.0.lock().unwrap();
    assert_eq!(captured.len(), 5);
    assert_eq!(captured[1]["body_bytes_seen"], "88");
    assert_eq!(captured[1]["http_status"], "200");
    let fields: Value = serde_json::from_str(&captured[1]["response_fields"]).unwrap();
    assert_eq!(fields, json!({"code":"DENIED","message":"invalid identity","errorMsg":"[redacted]"}));
    assert_eq!(captured[2]["body_state"], "complete");
    assert!(!captured[2].contains_key("declared_body_bytes"));
    let fields: Value = serde_json::from_str(&captured[2]["response_fields"]).unwrap();
    assert_eq!(fields, json!({"error":{"code":410,"message":"stream rejected"}}));
    assert_eq!(captured[3]["body_format"], "unstructured");
    assert_eq!(captured[3]["response_fields"], "null");
    assert!(!format!("{:?}", captured[3]).contains("never-log-unstructured-body"));
    assert_eq!(captured[4]["body_format"], "sse_json");
    server.abort();
}
