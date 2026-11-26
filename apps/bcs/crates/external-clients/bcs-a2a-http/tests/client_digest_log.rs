use axum::{
    Json, Router,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use bcs_a2a_http::{
    client::bind_rest_client,
    digest::{A2A_HTTP_CLIENT_DIGEST_COLUMNS, A2A_HTTP_CLIENT_DIGEST_TARGET, A2aCallContext},
    jsonrpc::JsonRpcClient,
};
use bcs_tracing_api::{Carrier, Context, LogCorrelation, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace};
use futures::StreamExt;
use serde_json::{Value, json};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicU64, Ordering},
};
use tracing_subscriber::layer::SubscriberExt;

#[derive(Clone, Default)]
struct DigestCapture(Arc<Mutex<Vec<String>>>);

impl DigestCapture {
    fn take(&self) -> Vec<String> {
        let mut guard = self.0.lock().unwrap_or_else(|e| e.into_inner());
        std::mem::take(&mut *guard)
    }
}

impl<S: tracing::Subscriber> tracing_subscriber::Layer<S> for DigestCapture {
    fn on_event(&self, event: &tracing::Event<'_>, _ctx: tracing_subscriber::layer::Context<'_, S>) {
        if event.metadata().target() != A2A_HTTP_CLIENT_DIGEST_TARGET {
            return;
        }
        #[derive(Default)]
        struct Msg(String);
        impl tracing::field::Visit for Msg {
            fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
                if field.name() == "message" {
                    self.0 = format!("{value:?}");
                }
            }
            fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
                if field.name() == "message" {
                    self.0 = value.to_owned();
                }
            }
        }
        let mut msg = Msg::default();
        event.record(&mut msg);
        if let Ok(mut lines) = self.0.lock() {
            lines.push(msg.0);
        }
    }
}

fn cols(line: &str) -> Vec<&str> {
    let parts: Vec<&str> = line.split(',').collect();
    assert_eq!(parts.len(), A2A_HTTP_CLIENT_DIGEST_COLUMNS, "unexpected column count: {line}");
    parts
}

async fn rest_send() -> Response {
    let task = json!({"id":"task-1","contextId":"ctx-1","status":{"state":"TASK_STATE_WORKING"}});
    Json(json!({"task":task})).into_response()
}

async fn rest_stream() -> Response {
    let task = json!({"id":"task-1","contextId":"ctx-1","status":{"state":"TASK_STATE_WORKING"}});
    let body = format!(
        "data: {}\n\ndata: {}\n\n",
        json!({"task":task}),
        json!({"artifactUpdate":{"taskId":"task-1","contextId":"ctx-1","artifact":{"artifactId":"t","parts":[{"text":"hi"}]},"append":true}})
    );
    ([("content-type", "text/event-stream")], body).into_response()
}

async fn rest_get_task(axum::extract::Path(id): axum::extract::Path<String>) -> Response {
    match id.as_str() {
        "not-found" => (
            StatusCode::NOT_FOUND,
            Json(json!({"error":{"code":5,"message":"sensitive server text","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"TASK_NOT_FOUND","domain":"a2a-protocol.org"}]}})),
        ).into_response(),
        "auth-denied" => (StatusCode::UNAUTHORIZED, "denied").into_response(),
        "rate-limited" => (StatusCode::TOO_MANY_REQUESTS, "slow down").into_response(),
        "server-err" => (StatusCode::INTERNAL_SERVER_ERROR, "boom").into_response(),
        _ => Json(json!({"id":id,"contextId":"ctx-1","status":{"state":"TASK_STATE_WORKING"}})).into_response(),
    }
}

async fn rest_cancel_or_subscribe(axum::extract::Path(tail): axum::extract::Path<String>) -> Response {
    if tail.ends_with(":subscribe") {
        if tail.starts_with("hang:") {
            let body = axum::body::Body::from_stream(futures::stream::pending::<Result<Vec<u8>, std::io::Error>>());
            return Response::builder()
                .status(StatusCode::OK)
                .header("content-type", "text/event-stream")
                .body(body)
                .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response());
        }
        return rest_stream().await;
    }
    let id = tail.strip_suffix(":cancel").unwrap_or(&tail);
    Json(json!({"id":id,"contextId":"ctx-1","status":{"state":"TASK_STATE_CANCELED"}})).into_response()
}

async fn jsonrpc_handler(State(_): State<()>, Json(req): Json<Value>) -> Response {
    let id = req["id"].clone();
    let method = req["method"].as_str().unwrap_or("");
    let param_id = req["params"]["id"].as_str().unwrap_or("");
    if param_id == "rpc-error" {
        return Json(json!({"jsonrpc":"2.0","id":id,"error":{"code":-32001,"message":"secret diagnostic"}})).into_response();
    }
    if param_id == "wrong-id" {
        return Json(json!({"jsonrpc":"2.0","id":"other","result":{"id":"x","contextId":"c","status":{"state":"TASK_STATE_WORKING"}}})).into_response();
    }
    let task = json!({"id":"task-rpc","contextId":"ctx-1","status":{"state":"TASK_STATE_WORKING"}});
    match method {
        "SendMessage" => Json(json!({"jsonrpc":"2.0","id":id,"result":{"task":task}})).into_response(),
        "GetTask" | "CancelTask" => Json(json!({"jsonrpc":"2.0","id":id,"result":task})).into_response(),
        "SendStreamingMessage" | "SubscribeToTask" => {
            if param_id == "mid-stream-error" {
                let wire = format!(
                    "data: {}\n\ndata: {}\n\n",
                    json!({"jsonrpc":"2.0","id":id,"result":{"task":task}}),
                    json!({"jsonrpc":"2.0","id":id,"error":{"code":-32001,"message":"secret mid-stream"}}),
                );
                return ([("content-type", "text/event-stream")], wire).into_response();
            }
            let wire = format!(
                "data: {}\n\ndata: {}\n\n",
                json!({"jsonrpc":"2.0","id":id,"result":{"task":task}}),
                json!({"jsonrpc":"2.0","id":id,"result":{"artifactUpdate":{"taskId":"task-rpc","contextId":"ctx-1","artifact":{"artifactId":"t","parts":[{"text":"ok"}]},"append":true}}}),
            );
            ([("content-type", "text/event-stream")], wire).into_response()
        }
        _ => StatusCode::BAD_REQUEST.into_response(),
    }
}

async fn start_server() -> (String, tokio::task::JoinHandle<()>) {
    let router = Router::new()
        .route("/a2a/message:send", post(rest_send))
        .route("/a2a/message:stream", post(rest_stream))
        .route("/a2a/tasks/{tail}", get(rest_get_task).post(rest_cancel_or_subscribe))
        .route("/rpc", post(jsonrpc_handler))
        .with_state(());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("local_addr");
    let handle = tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    (format!("http://{addr}"), handle)
}

#[tokio::test]
async fn rest_and_jsonrpc_emit_standard_digest_records_for_success_errors_and_streams() {
    let (origin, server) = start_server().await;
    let capture = DigestCapture::default();
    let subscriber = tracing_subscriber::registry().with(capture.clone());
    use tracing::instrument::WithSubscriber;

    let ctx = A2aCallContext {
        bot_id: "bot_demo".into(),
        provider_id: "prv_demo".into(),
        run_id: "run_demo".into(),
    };
    let rest = bind_rest_client(
        reqwest::Client::builder().no_proxy().build().expect("client"),
        format!("{origin}/a2a"),
        Some("tenant-a".into()),
    )
    .with_call_context(ctx.clone());

    let send_req: a2a::SendMessageRequest = serde_json::from_value(json!({
        "message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}
    }))
    .expect("send req");

    bcs_observability::with_request_id("req-42".into(), async {
        // 1. All 5 HTTP+JSON methods succeed
        rest.send_message(&send_req).await.expect("rest send");
        let events: Vec<_> = rest.send_streaming_message(&send_req).await.expect("rest stream").collect().await;
        assert_eq!(events.len(), 2);
        rest.get_task(&serde_json::from_value(json!({"id": "secret-task-99"})).expect("get")).await.expect("rest get");
        rest.cancel_task(&serde_json::from_value(json!({"id": "secret-task-99"})).expect("cancel")).await.expect("rest cancel");
        let sub_events: Vec<_> = rest.subscribe_to_task(&serde_json::from_value(json!({"id": "secret-task-99"})).expect("sub")).await.expect("rest sub").collect().await;
        assert_eq!(sub_events.len(), 2);

        let lines = capture.take();
        assert_eq!(lines.len(), 5);
        let expected = [
            ("SYNC", "SendMessage", "/a2a/message:send", "0"),
            ("STREAM", "SendStreamingMessage", "/a2a/message:stream", "2"),
            ("SYNC", "GetTask", "/a2a/tasks/{id}", "0"),
            ("SYNC", "CancelTask", "/a2a/tasks/{id}:cancel", "0"),
            ("STREAM", "SubscribeToTask", "/a2a/tasks/{id}:subscribe", "2"),
        ];
        for (line, (invoke, method, path, ev_count)) in lines.iter().zip(expected) {
            let c = cols(line);
            assert_eq!(c[1], "");
            assert_eq!(c[2], "");
            assert_eq!(c[3], "req-42");
            assert_eq!(c[4], "run_demo");
            assert_eq!(c[5], "HTTP+JSON");
            assert_eq!(c[6], invoke);
            assert_eq!(c[7], method);
            assert_eq!(c[9], path);
            assert!(!line.contains("secret-task-99"), "raw task id leaked: {line}");
            assert_eq!(c[10], "prv_demo");
            assert_eq!(c[11], "bot_demo");
            assert_eq!(c[12], "tenant-a");
            assert_eq!(c[13], "Y");
            assert_eq!(c[14], "00");
            assert_eq!(c[15], "200");
            assert_eq!(c[16], "");
            assert!(c[20].parse::<i64>().expect("ttfb") >= 0);
            assert_eq!(c[21], ev_count);
        }

        // 2. HTTP+JSON error classification (404 typed, 401 auth, 429 rate-limit, 500 server)
        assert!(rest.get_task(&serde_json::from_value(json!({"id": "not-found"})).expect("req")).await.is_err());
        assert!(rest.get_task(&serde_json::from_value(json!({"id": "auth-denied"})).expect("req")).await.is_err());
        assert!(rest.get_task(&serde_json::from_value(json!({"id": "rate-limited"})).expect("req")).await.is_err());
        assert!(rest.get_task(&serde_json::from_value(json!({"id": "server-err"})).expect("req")).await.is_err());

        let err_lines = capture.take();
        assert_eq!(err_lines.len(), 4);
        assert!(!err_lines[0].contains("sensitive server text"));
        assert_eq!((cols(&err_lines[0])[13], cols(&err_lines[0])[14], cols(&err_lines[0])[15], cols(&err_lines[0])[16]), ("N", "01", "404", "TASK_NOT_FOUND"));
        assert_eq!((cols(&err_lines[1])[13], cols(&err_lines[1])[14], cols(&err_lines[1])[15], cols(&err_lines[1])[16]), ("N", "06", "401", "HTTP_401"));
        assert_eq!((cols(&err_lines[2])[13], cols(&err_lines[2])[14], cols(&err_lines[2])[15], cols(&err_lines[2])[16]), ("N", "05", "429", "HTTP_429"));
        assert_eq!((cols(&err_lines[3])[13], cols(&err_lines[3])[14], cols(&err_lines[3])[15], cols(&err_lines[3])[16]), ("N", "02", "500", "HTTP_500"));

        // 3. Stream early drop: after 1 event => 00/Y; before any event => 07/N/CANCELLED
        {
            let mut s = rest.send_streaming_message(&send_req).await.expect("stream");
            let first = s.next().await.expect("first event");
            assert!(first.is_ok());
            // Drop without draining EOF
        }
        {
            let s = rest.subscribe_to_task(&serde_json::from_value(json!({"id": "hang"})).expect("req")).await.expect("stream");
            drop(s);
        }
        let drop_lines = capture.take();
        assert_eq!(drop_lines.len(), 2);
        assert_eq!((cols(&drop_lines[0])[13], cols(&drop_lines[0])[14], cols(&drop_lines[0])[21]), ("Y", "00", "1"));
        assert_eq!((cols(&drop_lines[1])[13], cols(&drop_lines[1])[14], cols(&drop_lines[1])[16], cols(&drop_lines[1])[21]), ("N", "07", "CANCELLED", "0"));

        // 4. JSONRPC success + RPC error + invalid envelope + mid-stream RPC error
        let rpc = JsonRpcClient::new(
            reqwest::Client::builder().no_proxy().build().expect("client"),
            format!("{origin}/rpc"),
            Some("tenant-rpc".into()),
        )
        .with_call_context(ctx.clone());

        rpc.send_message(&send_req).await.expect("rpc send");
        let rpc_stream: Vec<_> = rpc.send_streaming_message(&send_req).await.expect("rpc stream").collect().await;
        assert_eq!(rpc_stream.len(), 2);
        assert!(rpc.get_task(&serde_json::from_value(json!({"id": "rpc-error"})).expect("req")).await.is_err());
        assert!(rpc.get_task(&serde_json::from_value(json!({"id": "wrong-id"})).expect("req")).await.is_err());
        let bad_stream: Vec<_> = rpc.subscribe_to_task(&serde_json::from_value(json!({"id": "mid-stream-error"})).expect("req")).await.expect("open").collect().await;
        assert_eq!(bad_stream.len(), 2);
        assert!(bad_stream[0].is_ok());
        assert!(bad_stream[1].is_err());

        let rpc_lines = capture.take();
        assert_eq!(rpc_lines.len(), 5);
        assert_eq!((cols(&rpc_lines[0])[5], cols(&rpc_lines[0])[6], cols(&rpc_lines[0])[7], cols(&rpc_lines[0])[9], cols(&rpc_lines[0])[13], cols(&rpc_lines[0])[14]),
            ("JSONRPC", "SYNC", "SendMessage", "/rpc", "Y", "00"));
        assert_eq!((cols(&rpc_lines[1])[5], cols(&rpc_lines[1])[6], cols(&rpc_lines[1])[7], cols(&rpc_lines[1])[13], cols(&rpc_lines[1])[14], cols(&rpc_lines[1])[21]),
            ("JSONRPC", "STREAM", "SendStreamingMessage", "Y", "00", "2"));
        assert!(!rpc_lines[2].contains("secret diagnostic"));
        assert_eq!((cols(&rpc_lines[2])[13], cols(&rpc_lines[2])[14], cols(&rpc_lines[2])[16]), ("N", "01", "-32001"));
        assert_eq!((cols(&rpc_lines[3])[13], cols(&rpc_lines[3])[14], cols(&rpc_lines[3])[16]), ("N", "02", "INVALID_ENVELOPE"));
        assert!(!rpc_lines[4].contains("secret mid-stream"));
        assert_eq!((cols(&rpc_lines[4])[13], cols(&rpc_lines[4])[14], cols(&rpc_lines[4])[16], cols(&rpc_lines[4])[21]), ("N", "01", "-32001", "1"));

        // 5. Transport/connection error (unreachable port)
        let dead = JsonRpcClient::new(
            reqwest::Client::builder().no_proxy().build().expect("client"),
            "http://127.0.0.1:1/rpc".into(),
            None,
        );
        assert!(dead.get_task(&serde_json::from_value(json!({"id": "x"})).expect("req")).await.is_err());
        let dead_lines = capture.take();
        assert_eq!(dead_lines.len(), 1);
        let dc = cols(&dead_lines[0]);
        assert_eq!((dc[13], dc[14], dc[15], dc[20]), ("N", "04", "0", "-1"));
    })
    .with_subscriber(subscriber)
    .await;

    server.abort();
}

struct MockTraceContext {
    trace_id: String,
    rpc_id: String,
}

struct NoopRecord;
impl Record for NoopRecord {
    fn finish(self: Box<Self>, _outcome: Outcome, _response_size: Option<u64>) {}
}

struct MockSofaTracer {
    trace_id: String,
    seq: AtomicU64,
}

impl NetworkTracer for MockSofaTracer {
    fn start(&self, _info: &RequestInfo<'_>, _incoming: &Carrier, _parent: ParentContext<'_>) -> StartedTrace {
        let child = self.seq.fetch_add(1, Ordering::Relaxed) + 1;
        let rpc_id = format!("0.{child}");
        let context = MockTraceContext {
            trace_id: self.trace_id.clone(),
            rpc_id: rpc_id.clone(),
        };
        StartedTrace {
            context: Arc::new(context),
            remove_headers: vec!["sofatraceid".into(), "sofarpcid".into()],
            headers: vec![
                ("sofatraceid".into(), self.trace_id.clone()),
                ("sofarpcid".into(), rpc_id),
            ],
            record: Box::new(NoopRecord),
        }
    }

    fn log_correlation(&self, context: &Context) -> Option<LogCorrelation> {
        let ctx = context.downcast_ref::<MockTraceContext>()?;
        LogCorrelation::new("sofa_trace_id", &ctx.trace_id, "sofa_rpc_id", &ctx.rpc_id)
    }
}

#[tokio::test]
async fn digest_records_outbound_trace_id_and_span_id_matching_wire_headers() {
    let seen_headers: Arc<Mutex<Vec<(String, String)>>> = Arc::new(Mutex::new(Vec::new()));
    let seen_rest = seen_headers.clone();
    let seen_rpc = seen_headers.clone();

    let router = Router::new()
        .route(
            "/a2a/message:send",
            post(move |headers: HeaderMap| {
                let seen_rest = seen_rest.clone();
                async move {
                    let tid = headers.get("sofatraceid").and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
                    let rid = headers.get("sofarpcid").and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
                    seen_rest.lock().unwrap_or_else(|e| e.into_inner()).push((tid, rid));
                    rest_send().await
                }
            }),
        )
        .route(
            "/rpc",
            post(move |headers: HeaderMap, Json(req): Json<Value>| {
                let seen_rpc = seen_rpc.clone();
                async move {
                    let tid = headers.get("sofatraceid").and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
                    let rid = headers.get("sofarpcid").and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
                    seen_rpc.lock().unwrap_or_else(|e| e.into_inner()).push((tid, rid));
                    jsonrpc_handler(State(()), Json(req)).await
                }
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("local_addr");
    let server = tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    let origin = format!("http://{addr}");

    let capture = DigestCapture::default();
    let subscriber = tracing_subscriber::registry().with(capture.clone());
    use tracing::instrument::WithSubscriber;

    let tracer: Arc<dyn NetworkTracer> = Arc::new(MockSofaTracer {
        trace_id: "0a1b2c3d4e5f6789".into(),
        seq: AtomicU64::new(0),
    });

    let rest = bind_rest_client(
        reqwest::Client::builder().no_proxy().build().expect("client"),
        format!("{origin}/a2a"),
        None,
    );
    let rpc = JsonRpcClient::new(
        reqwest::Client::builder().no_proxy().build().expect("client"),
        format!("{origin}/rpc"),
        None,
    );
    let dead = JsonRpcClient::new(
        reqwest::Client::builder().no_proxy().build().expect("client"),
        "http://127.0.0.1:1/rpc".into(),
        None,
    );
    let send_req: a2a::SendMessageRequest = serde_json::from_value(json!({
        "message": {"messageId": "m-trace", "role": "ROLE_USER", "parts": [{"text": "hello"}]}
    }))
    .expect("send req");

    bcs_tracing_api::with_provider(Some(tracer), async {
        rest.send_message(&send_req).await.expect("rest send");
        let events: Vec<_> = rpc.send_streaming_message(&send_req).await.expect("rpc stream").collect().await;
        assert_eq!(events.len(), 2);
        assert!(dead.get_task(&serde_json::from_value(json!({"id": "x"})).expect("req")).await.is_err());
    })
    .with_subscriber(subscriber)
    .await;

    let wire = seen_headers.lock().unwrap_or_else(|e| e.into_inner()).clone();
    assert_eq!(
        wire,
        vec![
            ("0a1b2c3d4e5f6789".to_owned(), "0.1".to_owned()),
            ("0a1b2c3d4e5f6789".to_owned(), "0.2".to_owned()),
        ]
    );

    let lines = capture.take();
    assert_eq!(lines.len(), 3);
    let c0 = cols(&lines[0]);
    let c1 = cols(&lines[1]);
    let c2 = cols(&lines[2]);
    assert_eq!((c0[1], c0[2], c0[5], c0[13], c0[14]), ("0a1b2c3d4e5f6789", "0.1", "HTTP+JSON", "Y", "00"));
    assert_eq!((c1[1], c1[2], c1[5], c1[13], c1[14], c1[21]), ("0a1b2c3d4e5f6789", "0.2", "JSONRPC", "Y", "00", "2"));
    assert_eq!((c2[1], c2[2], c2[5], c2[13], c2[14]), ("0a1b2c3d4e5f6789", "0.3", "JSONRPC", "N", "04"));

    server.abort();
}
