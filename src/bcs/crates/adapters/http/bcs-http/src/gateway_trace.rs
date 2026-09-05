use axum::http::HeaderMap;
use bcs_telemetry::{
    CapturedGenAiMessages, capture_gen_ai_input_messages, capture_gen_ai_output_messages,
};
use opentelemetry::{KeyValue, global};
use opentelemetry::trace::{Status, TraceContextExt};
use opentelemetry_http::HeaderExtractor;
use std::time::Duration;
use tower_http::trace::{MakeSpan, OnResponse};
use tracing::{Instrument, Span, info_span};
use tracing_opentelemetry::OpenTelemetrySpanExt;

const TRUNCATION_MARKER: &str = "...[TRUNCATED]...";
pub(crate) const SPAN_CONTENT_LIMIT_BYTES: usize = 4096;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BusinessTraceOperation {
    GatewayDispatch,
    BotResponse,
    HttpRequest,
}

impl BusinessTraceOperation {
    pub fn span_name(self) -> &'static str {
        match self {
            Self::GatewayDispatch => "bcn.gateway.dispatch",
            Self::BotResponse => "bcn.bot.response",
            Self::HttpRequest => "http.request",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct ChatClientObservation {
    pub detach: Option<bool>,
    pub wait_timeout_ms: Option<u64>,
}

pub(crate) fn chat_client_observation(headers: &HeaderMap) -> ChatClientObservation {
    ChatClientObservation {
        detach: headers
            .get("x-bcs-client-detach")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse().ok()),
        wait_timeout_ms: headers
            .get("x-bcs-client-wait-timeout-ms")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse().ok()),
    }
}

pub fn classify_business_request(path: &str) -> Option<BusinessTraceOperation> {
    let segments: Vec<_> = path.trim_matches('/').split('/').collect();
    match segments.as_slice() {
        ["bots", bot_id, "chat" | "chat-async"] if !bot_id.is_empty() => {
            Some(BusinessTraceOperation::GatewayDispatch)
        }
        ["bot", "events"] => Some(BusinessTraceOperation::BotResponse),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct BcnMakeSpan;

impl<B> MakeSpan<B> for BcnMakeSpan {
    fn make_span(&mut self, request: &axum::http::Request<B>) -> Span {
        let method = request.method().as_str();
        let path = request.uri().path();
        if path == "/bot/events/coordination" {
            return Span::none();
        }
        let operation = classify_business_request(path).unwrap_or(BusinessTraceOperation::HttpRequest);
        let parent = global::get_text_map_propagator(|propagator| {
            propagator.extract(&HeaderExtractor(request.headers()))
        });
        let span = match operation {
            BusinessTraceOperation::GatewayDispatch if path.ends_with("/chat-async") => info_span!(
                target: "bcn_otel",
                "bcn.gateway.dispatch",
                request_id = tracing::field::Empty, trace_id = tracing::field::Empty,
                otel.kind = "server",
                http.request.method = %method,
                http.route = "/bots/{id}/chat-async",
            ),
            BusinessTraceOperation::GatewayDispatch => info_span!(
                target: "bcn_otel",
                "bcn.gateway.dispatch",
                request_id = tracing::field::Empty, trace_id = tracing::field::Empty,
                otel.kind = "server",
                http.request.method = %method,
                url.path = %path,
            ),
            BusinessTraceOperation::HttpRequest => info_span!(
                target: "bcn_otel", "http.request", otel.kind = "server",
                request_id = tracing::field::Empty, trace_id = tracing::field::Empty,
                http.request.method = %method,
                http.route = request.extensions().get::<axum::extract::MatchedPath>()
                    .map(|route| route.as_str()).unwrap_or("unmatched"),
            ),
            BusinessTraceOperation::BotResponse => info_span!(
                target: "bcn_otel",
                "bcn.bot.response",
                request_id = tracing::field::Empty, trace_id = tracing::field::Empty,
                otel.kind = "server",
                http.request.method = %method,
                url.path = %path,
            ),
        };
        let _ = span.set_parent(parent);
        if let Some(request_id) = request.headers().get("x-request-id").and_then(|v| v.to_str().ok()) {
            span.record("request_id", request_id);
        }
        let context = span.context();
        let context_span = context.span();
        if context_span.span_context().is_valid() {
            span.record("trace_id", context_span.span_context().trace_id().to_string());
        }
        span
    }
}

/// Correlates extractor/auth failures and cancellation before a handler is entered.
pub async fn observe_request(
    mut request: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    if request.uri().path() == "/bot/events/coordination" { return next.run(request).await; }
    let request_id = request.headers().get("x-request-id").and_then(|v| v.to_str().ok())
        .filter(|value| !value.is_empty() && value.len() <= 128
            && value.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_.:".contains(&c)))
        .map(str::to_owned).unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let header = axum::http::HeaderValue::from_str(&request_id).expect("validated request ID");
    request.headers_mut().insert("x-request-id", header.clone());
    let span = BcnMakeSpan.make_span(&request);
    let trace_id = span.context().span().span_context().trace_id().to_string();
    let route = request.extensions().get::<axum::extract::MatchedPath>()
        .map(|route| route.as_str()).unwrap_or("unmatched").to_owned();
    let mut observation = RequestObservation {
        request_id: request_id.clone(), trace_id, route, span: span.clone(),
        started: std::time::Instant::now(), completed: false,
    };
    tracing::info!(target: "bcs_http_access", request_id = %observation.request_id,
        trace_id = %observation.trace_id, route = %observation.route,
        method = %request.method(), "http.request.started");
    let mut response = bcs_telemetry::with_request_context(request_id,
        next.run(request)).instrument(span.clone()).await;
    let elapsed = observation.started.elapsed();
    BcnOnResponse.on_response(&response, elapsed, &span);
    response.headers_mut().insert("x-request-id", header);
    observation.completed = true;
    tracing::info!(target: "bcs_http_access", request_id = %observation.request_id,
        trace_id = %observation.trace_id, route = %observation.route,
        status = response.status().as_u16(), duration_ms = elapsed.as_secs_f64() * 1000.0,
        outcome = if response.status().is_success() { "success" } else { "http_error" },
        "http.request.response_ready");
    response
}

struct RequestObservation {
    request_id: String,
    trace_id: String,
    route: String,
    span: Span,
    started: std::time::Instant,
    completed: bool,
}
impl Drop for RequestObservation {
    fn drop(&mut self) {
        if !self.completed {
            self.span.in_scope(|| tracing::warn!(target: "bcs_http_access",
                request_id = %self.request_id, trace_id = %self.trace_id, route = %self.route,
                duration_ms = self.started.elapsed().as_secs_f64() * 1000.0,
                outcome = "cancelled", "http.request.cancelled"));
        }
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct BcnOnResponse;

impl<B> OnResponse<B> for BcnOnResponse {
    fn on_response(self, response: &axum::http::Response<B>, latency: Duration, span: &Span) {
        let status = response.status().as_u16() as i64;
        span.set_attribute("http.response.status_code", status);
        span.set_attribute("http.server.request.duration_ms", latency.as_millis() as i64);
        if response.status().is_server_error() {
            span.set_status(Status::error(format!("HTTP {status}")));
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CapturedSpanContent {
    pub content: String,
    pub original_size_bytes: usize,
    pub captured_size_bytes: usize,
    pub truncated: bool,
}

pub(crate) fn truncate_span_content(input: &str, limit_bytes: usize) -> CapturedSpanContent {
    let original_size_bytes = input.len();
    if original_size_bytes <= limit_bytes {
        return CapturedSpanContent {
            content: input.to_string(),
            original_size_bytes,
            captured_size_bytes: original_size_bytes,
            truncated: false,
        };
    }

    if limit_bytes <= TRUNCATION_MARKER.len() {
        let end = floor_char_boundary(input, limit_bytes);
        let content = input[..end].to_string();
        return CapturedSpanContent {
            captured_size_bytes: content.len(),
            content,
            original_size_bytes,
            truncated: true,
        };
    }

    let available = limit_bytes - TRUNCATION_MARKER.len();
    let requested_head = available.saturating_mul(3) / 4;
    let head_end = floor_char_boundary(input, requested_head);
    let requested_tail = available - head_end;
    let tail_start = ceil_char_boundary(input, input.len().saturating_sub(requested_tail));
    let content = format!(
        "{}{}{}",
        &input[..head_end],
        TRUNCATION_MARKER,
        &input[tail_start..]
    );

    CapturedSpanContent {
        captured_size_bytes: content.len(),
        content,
        original_size_bytes,
        truncated: true,
    }
}

pub(crate) fn record_span_content_event(event_name: &'static str, content: &str) {
    let captured = truncate_span_content(content, SPAN_CONTENT_LIMIT_BYTES);
    Span::current().add_event(
        event_name,
        vec![
            KeyValue::new("bcn.content", captured.content),
            KeyValue::new(
                "bcn.content.original_size_bytes",
                captured.original_size_bytes as i64,
            ),
            KeyValue::new(
                "bcn.content.captured_size_bytes",
                captured.captured_size_bytes as i64,
            ),
            KeyValue::new(
                "bcn.content.limit_bytes",
                SPAN_CONTENT_LIMIT_BYTES as i64,
            ),
            KeyValue::new("bcn.content.truncated", captured.truncated),
        ],
    );
}

pub(crate) fn record_gen_ai_input_message(content: &str, untrusted: bool) {
    let captured = capture_gen_ai_input_messages(content, SPAN_CONTENT_LIMIT_BYTES);
    record_gen_ai_messages("gen_ai.input.messages", captured, untrusted);
}

pub(crate) fn record_gen_ai_output_message(
    content: &str,
    finish_reason: &str,
    untrusted: bool,
) {
    let captured =
        capture_gen_ai_output_messages(content, finish_reason, SPAN_CONTENT_LIMIT_BYTES);
    record_gen_ai_messages("gen_ai.output.messages", captured, untrusted);
}

pub(crate) fn record_span_content_attribute(
    attribute_name: &'static str,
    content: &str,
    untrusted: bool,
) {
    let captured = truncate_span_content(content, SPAN_CONTENT_LIMIT_BYTES);
    let span = Span::current();
    span.set_attribute(attribute_name, captured.content);
    span.set_attribute(
        "bcn.content.original_size_bytes",
        captured.original_size_bytes as i64,
    );
    span.set_attribute(
        "bcn.content.captured_size_bytes",
        captured.captured_size_bytes as i64,
    );
    span.set_attribute(
        "bcn.content.limit_bytes",
        SPAN_CONTENT_LIMIT_BYTES as i64,
    );
    span.set_attribute("bcn.content.truncated", captured.truncated);
    span.set_attribute("bcn.content.untrusted", untrusted);
}

fn record_gen_ai_messages(
    attribute_name: &'static str,
    captured: CapturedGenAiMessages,
    untrusted: bool,
) {
    let span = Span::current();
    span.set_attribute(attribute_name, captured.value);
    span.set_attribute(
        "bcn.content.original_size_bytes",
        captured.original_size_bytes as i64,
    );
    span.set_attribute(
        "bcn.content.captured_size_bytes",
        captured.captured_size_bytes as i64,
    );
    span.set_attribute(
        "bcn.content.limit_bytes",
        SPAN_CONTENT_LIMIT_BYTES as i64,
    );
    span.set_attribute("bcn.content.truncated", captured.truncated);
    span.set_attribute("bcn.content.untrusted", untrusted);
}

fn floor_char_boundary(value: &str, mut index: usize) -> usize {
    index = index.min(value.len());
    while index > 0 && !value.is_char_boundary(index) {
        index -= 1;
    }
    index
}

fn ceil_char_boundary(value: &str, mut index: usize) -> usize {
    index = index.min(value.len());
    while index < value.len() && !value.is_char_boundary(index) {
        index += 1;
    }
    index
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{HeaderMap, HeaderValue};
    use opentelemetry::{global, trace::TracerProvider as _};
    use opentelemetry_sdk::{
        propagation::TraceContextPropagator,
        trace::{InMemorySpanExporterBuilder, SdkTracerProvider},
    };
    use tower_http::trace::{MakeSpan, OnResponse};
    use tracing_subscriber::prelude::*;

    #[test]
    fn span_content_keeps_short_utf8_text() {
        let captured = truncate_span_content("你好 BCN", 4096);

        assert_eq!(captured.content, "你好 BCN");
        assert_eq!(captured.original_size_bytes, 10);
        assert_eq!(captured.captured_size_bytes, 10);
        assert!(!captured.truncated);
    }

    #[test]
    fn span_content_preserves_head_and_tail_within_byte_limit() {
        let input = format!("{}END", "你".repeat(2000));
        let captured = truncate_span_content(&input, 4096);

        assert!(captured.truncated);
        assert!(captured.content.contains("...[TRUNCATED]..."));
        assert!(captured.content.ends_with("END"));
        assert!(captured.captured_size_bytes <= 4096);
        assert_eq!(captured.original_size_bytes, input.len());
        assert!(std::str::from_utf8(captured.content.as_bytes()).is_ok());
    }

    #[test]
    fn chat_client_observation_parses_detach_and_wait_timeout_headers() {
        let mut headers = HeaderMap::new();
        headers.insert("x-bcs-client-detach", HeaderValue::from_static("true"));
        headers.insert(
            "x-bcs-client-wait-timeout-ms",
            HeaderValue::from_static("60000"),
        );

        let observation = chat_client_observation(&headers);

        assert_eq!(observation.detach, Some(true));
        assert_eq!(observation.wait_timeout_ms, Some(60_000));
    }

    #[test]
    fn business_trace_operation_classifies_chat_and_callback_routes() {
        assert_eq!(
            classify_business_request("/bots/bot-1/chat-async"),
            Some(BusinessTraceOperation::GatewayDispatch)
        );
        assert_eq!(
            classify_business_request("/bot/events"),
            Some(BusinessTraceOperation::BotResponse)
        );
        assert_eq!(classify_business_request("/bot/events/coordination"), None);
        assert_eq!(classify_business_request("/chat/runs/run-1"), None);
    }

    #[test]
    fn gateway_request_span_uses_inbound_traceparent_as_remote_parent() {
        assert_request_span_parent("/bots/bot-1/chat-async", "bcn.gateway.dispatch");
    }

    #[test]
    fn bot_response_span_uses_provider_traceparent_as_remote_parent() {
        assert_request_span_parent("/bot/events", "bcn.bot.response");
    }

    #[test]
    fn requests_without_valid_traceparent_create_root_spans() {
        assert_request_creates_root_span("/bots/bot-1/chat-async", None);
        assert_request_creates_root_span("/bot/events", None);
        assert_request_creates_root_span("/bots/bot-1/chat-async", Some("malformed"));
        assert_request_creates_root_span("/bot/events", Some("malformed"));
        assert_request_creates_root_span("/bots/query", None);
    }

    #[test]
    fn coordination_route_returns_disabled_span_even_with_traceparent() {
        global::set_text_map_propagator(TraceContextPropagator::new());
        let request = axum::http::Request::builder()
            .method("POST")
            .uri("/bot/events/coordination")
            .header(
                "traceparent",
                "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            )
            .body(())
            .unwrap();
        let mut make_span = BcnMakeSpan::default();

        assert!(make_span.make_span(&request).is_disabled());
    }

    fn assert_request_span_parent(path: &str, expected_name: &str) {
        global::set_text_map_propagator(TraceContextPropagator::new());
        let exporter = InMemorySpanExporterBuilder::new().build();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter.clone())
            .build();
        let tracer = provider.tracer("bcn-gateway-test");
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(tracer));

        tracing::subscriber::with_default(subscriber, || {
            let request = axum::http::Request::builder()
                .method("POST")
                .uri(path)
                .header(
                    "traceparent",
                    "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                )
                .body(())
                .unwrap();
            let mut make_span = BcnMakeSpan::default();
            drop(make_span.make_span(&request));
        });

        provider.force_flush().unwrap();
        let spans = exporter.get_finished_spans().unwrap();
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0].name, expected_name);
        assert_eq!(
            spans[0].span_context.trace_id().to_string(),
            "0af7651916cd43dd8448eb211c80319c"
        );
        assert_eq!(
            spans[0].parent_span_id.to_string(),
            "b7ad6b7169203331"
        );
    }

    fn assert_request_creates_root_span(path: &str, traceparent: Option<&str>) {
        global::set_text_map_propagator(TraceContextPropagator::new());
        let exporter = InMemorySpanExporterBuilder::new().build();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter.clone())
            .build();
        let tracer = provider.tracer("bcn-gateway-test");
        let subscriber = tracing_subscriber::registry()
            .with(tracing_subscriber::EnvFilter::new("bcn_otel=info"))
            .with(tracing_opentelemetry::layer().with_tracer(tracer));

        tracing::subscriber::with_default(subscriber, || {
            let mut builder = axum::http::Request::builder().method("POST").uri(path);
            if let Some(traceparent) = traceparent {
                builder = builder.header("traceparent", traceparent);
            }
            let request = builder.body(()).unwrap();
            let mut make_span = BcnMakeSpan::default();
            let span = make_span.make_span(&request);
            assert!(!span.is_disabled());
            drop(span);
        });

        provider.force_flush().unwrap();
        let spans = exporter.get_finished_spans().unwrap();
        assert_eq!(spans.len(), 1);
        assert!(spans[0].span_context.is_valid());
        assert_eq!(spans[0].parent_span_id, opentelemetry::trace::SpanId::INVALID);
    }

    #[test]
    fn gateway_response_records_status_latency_and_server_error() {
        let exporter = InMemorySpanExporterBuilder::new().build();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter.clone())
            .build();
        let tracer = provider.tracer("bcn-gateway-test");
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(tracer));

        tracing::subscriber::with_default(subscriber, || {
            let span = info_span!(target: "bcn_otel", "bcn.gateway.dispatch");
            let response = axum::http::Response::builder()
                .status(503)
                .body(())
                .unwrap();
            BcnOnResponse.on_response(&response, Duration::from_millis(42), &span);
            drop(span);
        });

        provider.force_flush().unwrap();
        let spans = exporter.get_finished_spans().unwrap();
        let attributes = &spans[0].attributes;
        assert!(attributes.iter().any(|attribute| {
            attribute.key.as_str() == "http.response.status_code"
                && attribute.value == opentelemetry::Value::I64(503)
        }));
        assert!(attributes.iter().any(|attribute| {
            attribute.key.as_str() == "http.server.request.duration_ms"
                && attribute.value == opentelemetry::Value::I64(42)
        }));
        assert!(matches!(spans[0].status, Status::Error { .. }));
    }

    #[test]
    fn content_attributes_record_truncated_body_and_sizes_without_events() {
        let exporter = InMemorySpanExporterBuilder::new().build();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter.clone())
            .build();
        let tracer = provider.tracer("bcn-content-test");
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(tracer));

        tracing::subscriber::with_default(subscriber, || {
            let span = info_span!(target: "bcn_otel", "bcn.gateway.dispatch");
            let _guard = span.enter();
            record_gen_ai_input_message(&"x".repeat(5000), false);
        });

        provider.force_flush().unwrap();
        let spans = exporter.get_finished_spans().unwrap();
        assert!(spans[0].events.events.is_empty());
        assert!(spans[0].attributes.iter().all(|attribute| {
            attribute.key.as_str() != "bcn.content"
        }));
        assert!(spans[0].attributes.iter().any(|attribute| {
            attribute.key.as_str() == "bcn.content.truncated"
                && attribute.value == opentelemetry::Value::Bool(true)
        }));
        assert!(spans[0].attributes.iter().any(|attribute| {
            attribute.key.as_str() == "bcn.content.original_size_bytes"
                && attribute.value == opentelemetry::Value::I64(5000)
        }));
        assert!(spans[0].attributes.iter().any(|attribute| {
            attribute.key.as_str() == "bcn.content.limit_bytes"
                && attribute.value == opentelemetry::Value::I64(4096)
        }));
        assert!(spans[0].attributes.iter().any(|attribute| {
            attribute.key.as_str() == "bcn.content.untrusted"
                && attribute.value == opentelemetry::Value::Bool(false)
        }));
        let captured = spans[0]
            .attributes
            .iter()
            .find(|attribute| attribute.key.as_str() == "gen_ai.input.messages")
            .unwrap();
        let captured_size = match &captured.value {
            opentelemetry::Value::String(value) => value.as_str().len(),
            other => panic!("unexpected captured content value: {other:?}"),
        };
        assert!(captured_size <= 4096);
        assert!(spans[0].attributes.iter().any(|attribute| {
            attribute.key.as_str() == "bcn.content.captured_size_bytes"
                && matches!(attribute.value, opentelemetry::Value::I64(size) if size < 4096)
        }));
    }
}
