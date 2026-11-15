use super::*;

#[derive(Clone, Default)]
struct SharedLogBuffer(Arc<std::sync::Mutex<Vec<u8>>>);

struct SharedLogWriter {
    buffer: Arc<std::sync::Mutex<Vec<u8>>>,
}

impl Write for SharedLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buffer.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter {
            buffer: self.0.clone(),
        }
    }
}

async fn capture_tracing_logs<Fut>(future: Fut) -> String
where
    Fut: Future<Output = ()>,
{
    let buffer = SharedLogBuffer::default();
    let subscriber = tracing_subscriber::fmt()
        .with_ansi(false)
        .with_level(false)
        .with_target(true)
        .with_writer(buffer.clone())
        .finish();
    let dispatch = tracing::Dispatch::new(subscriber);
    let guard = tracing::dispatcher::set_default(&dispatch);
    future.await;
    drop(guard);
    String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap()
}

async fn capture_otel_spans<F, T>(future: F) -> (T, Vec<opentelemetry_sdk::trace::SpanData>)
where
    F: Future<Output = T>,
{
    opentelemetry::global::set_text_map_propagator(
        opentelemetry_sdk::propagation::TraceContextPropagator::new(),
    );
    let exporter = opentelemetry_sdk::trace::InMemorySpanExporterBuilder::new().build();
    let provider = opentelemetry_sdk::trace::SdkTracerProvider::builder()
        .with_simple_exporter(exporter.clone())
        .build();
    let tracer = opentelemetry::trace::TracerProvider::tracer(&provider, "bot-events-contract");
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new("bcn_otel=info"))
        .with(tracing_opentelemetry::layer().with_tracer(tracer));

    let output = future.with_subscriber(subscriber).await;
    provider.force_flush().unwrap();
    (output, exporter.get_finished_spans().unwrap())
}

fn assert_otel_span_string_attribute(
    span: &opentelemetry_sdk::trace::SpanData,
    key: &str,
    expected: &str,
) {
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == key
            && matches!(&attr.value, opentelemetry::Value::String(value) if value.as_str() == expected)
    }));
}

fn assert_otel_span_bool_attribute(
    span: &opentelemetry_sdk::trace::SpanData,
    key: &str,
    expected: bool,
) {
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == key && attr.value == opentelemetry::Value::Bool(expected)
    }));
}

fn assert_gen_ai_output_message(
    span: &opentelemetry_sdk::trace::SpanData,
    expected_content: &str,
    expected_finish_reason: &str,
) {
    let value = span
        .attributes
        .iter()
        .find_map(|attr| match &attr.value {
            opentelemetry::Value::String(value)
                if attr.key.as_str() == "gen_ai.output.messages" =>
            {
                Some(value.as_str())
            }
            _ => None,
        })
        .expect("gen_ai.output.messages string attribute");
    let messages: Value = serde_json::from_str(value).expect("schema-compliant output messages JSON");
    assert_eq!(messages.as_array().map(Vec::len), Some(1));
    assert_eq!(messages[0]["role"], "assistant");
    assert_eq!(messages[0]["parts"].as_array().map(Vec::len), Some(1));
    assert_eq!(messages[0]["parts"][0]["type"], "text");
    assert_eq!(messages[0]["parts"][0]["content"], expected_content);
    assert_eq!(messages[0]["finish_reason"], expected_finish_reason);
}
#[tokio::test]
async fn bot_events_logs_json_rejections_before_handler() {
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let app = app.layer(axum::middleware::from_fn(bcs_http::gateway_trace::observe_request));

    let logs = capture_tracing_logs(async {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", "prv-log")
                    .header("x-request-id", "rejected-callback-42")
                    .header("authorization", "Bearer runtime-token")
                    .body(Body::from(
                        json!({
                            "run_id": "run-json-rejection",
                            "state": "failed",
                            "message": { "text": "boom" }
                        })
                        .to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        let body = String::from_utf8(
            to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap()
                .to_vec(),
        )
        .unwrap();
        assert!(body.contains("Failed to deserialize"), "body: {body}");
        assert!(body.contains("unknown variant `failed`"), "body: {body}");
    })
    .await;

    assert!(
        logs.contains("provider callback: invalid bot event request"),
        "expected invalid request log, got:\n{logs}"
    );
    let rejection = logs.lines().find(|line| line.contains("provider callback: invalid bot event request")).unwrap();
    assert!(rejection.contains("request_id=rejected-callback-42"), "request ID missing from business error: {rejection}");
    assert!(
        logs.contains("provider_id=prv-log"),
        "expected provider id in log, got:\n{logs}"
    );
    assert!(
        logs.contains("unknown variant"),
        "expected serde rejection in log, got:\n{logs}"
    );
    assert!(
        logs.contains("boom"),
        "expected request body in log, got:\n{logs}"
    );
}
#[tokio::test]
async fn agentpass_resolve_does_not_log_token_or_claims() {
    let jwt_header = json!({ "alg": "RS256", "typ": "JWT" });
    let jwt_payload = json!({
        "act": "agent-code-log-sentinel",
        "sno": "staff-number-log-sentinel",
        "sub": "operator-log-sentinel"
    });
    let token = diagnostic_jwt(jwt_header, jwt_payload, b"signature-log-sentinel");
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        token.clone(),
        "agent-code-log-sentinel".to_string(),
    )]));
    let TestApp { app, .. } = test_app(Arc::new(resolver));
    let request_token = token.clone();

    let logs = capture_tracing_logs(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/providers/agentpass/resolve")
                    .header("X-BCN-Provider-Id", "provider-log-sentinel")
                    .header("authorization", format!("Bearer {request_token}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let _ = response_json(response).await;
    })
    .await;

    assert!(!logs.contains(&token));
    assert!(!logs.contains("staff-number-log-sentinel"));
    assert!(!logs.contains("operator-log-sentinel"));
}
#[tokio::test]
async fn bot_events_marks_auth_failure_content_untrusted_without_business_attributes() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        run_context,
        ..
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-untrusted".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(BcnMakeSpan)
            .on_response(BcnOnResponse),
    );
    let provider_id = registered.provider_id;
    let admin_token = registered.admin_token;

    let (status, spans) = capture_otel_spans(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", provider_id)
                    .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(Body::from(json!({
                        "run_id": "run-untrusted",
                        "state": "final",
                        "message": { "text": "untrusted-callback-content" }
                    }).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        drop(response);
        status
    })
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let span = spans.iter().find(|span| span.name == "bcn.bot.response").unwrap();
    assert_otel_span_string_attribute(span, "bcn.auth.result", "failed");
    assert!(!span.attributes.iter().any(|attr| attr.key.as_str() == "bcn.provider.id"));
    assert!(!span.attributes.iter().any(|attr| attr.key.as_str() == "bcn.run.id"));
    assert!(span.events.events.is_empty());
    assert_otel_span_bool_attribute(span, "bcn.content.untrusted", true);
    assert_gen_ai_output_message(span, "untrusted-callback-content", "stop");
}

#[tokio::test]
async fn bot_events_marks_success_content_trusted_with_business_attributes() {
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        run_context,
        ..
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.unwrap();
    run_context
        .put_context(BotRunContext {
            run_id: "run-trusted".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(BcnMakeSpan)
            .on_response(BcnOnResponse),
    );
    let provider_id = registered.provider_id;
    let request_provider_id = provider_id.clone();

    let (status, spans) = capture_otel_spans(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", request_provider_id.as_str())
                    .header("authorization", format!("Bearer {token}"))
                    .body(Body::from(json!({
                        "run_id": "run-trusted",
                        "state": "final",
                        "message": { "text": "trusted-callback-content" }
                    }).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        drop(response);
        status
    })
    .await;

    assert_eq!(status, StatusCode::OK);
    let span = spans.iter().find(|span| span.name == "bcn.bot.response").unwrap();
    assert_otel_span_string_attribute(span, "bcn.auth.result", "success");
    assert_otel_span_string_attribute(span, "bcn.provider.id", &provider_id);
    assert_otel_span_string_attribute(span, "bcn.run.id", "run-trusted");
    assert!(span.events.events.is_empty());
    assert_otel_span_bool_attribute(span, "bcn.content.untrusted", false);
    assert_gen_ai_output_message(span, "trusted-callback-content", "stop");
}

#[tokio::test]
async fn bot_events_records_untrusted_content_when_auth_cannot_follow_invalid_shape() {
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(BcnMakeSpan)
            .on_response(BcnOnResponse),
    );

    let (status, spans) = capture_otel_spans(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", "unverified-provider")
                    .header("authorization", "Bearer unverified-token")
                    .body(Body::from(json!({
                        "run_id": "unverified-run",
                        "message": { "text": "invalid-shape-content" }
                    }).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        drop(response);
        status
    })
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    let span = spans.iter().find(|span| span.name == "bcn.bot.response").unwrap();
    assert_otel_span_string_attribute(span, "bcn.auth.result", "unverified");
    assert!(!span.attributes.iter().any(|attr| attr.key.as_str() == "bcn.provider.id"));
    assert!(span.events.events.is_empty());
    assert_otel_span_bool_attribute(span, "bcn.content.untrusted", true);
    assert_gen_ai_output_message(span, "invalid-shape-content", "unknown");
}
