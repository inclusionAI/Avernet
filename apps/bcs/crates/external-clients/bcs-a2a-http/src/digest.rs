//! Framework-level client digest logging for A2A HTTP+JSON and JSON-RPC calls.
//!
//! Emits one fixed-column CSV record per outbound call to the dedicated
//! `bcs_a2a_http_client_digest` tracing target, modeled after SOFARPC's
//! `rpc-client-digest.log` for direct success-rate and latency monitoring.
//!
//! Column schema (22 comma-separated fields):
//!  1. timestamp     - local time `YYYY-MM-DD HH:mm:ss.SSS`
//!  2. trace_id      - trace ID from `bcs-tracing-api` `LogCorrelation` (e.g. `sofa_trace_id`; empty if none)
//!  3. span_id       - span/RPC ID from `bcs-tracing-api` `LogCorrelation` (e.g. outbound `sofa_rpc_id`; empty if none)
//!  4. request_id    - BCS request ID from `bcs-observability` (empty if none)
//!  5. run_id        - BCS run ID from `A2aCallContext` (empty if standalone)
//!  6. protocol      - `HTTP+JSON` or `JSONRPC`
//!  7. invoke_type   - `SYNC` or `STREAM`
//!  8. method        - `SendMessage` | `SendStreamingMessage` | `GetTask` | `CancelTask` | `SubscribeToTask`
//!  9. target_host   - sanitized `host[:port]` (never credentials/query/fragment)
//! 10. target_path   - sanitized canonical path (REST task IDs normalized to `{id}`)
//! 11. provider_id   - BCS provider ID (empty if standalone)
//! 12. bot_id        - BCS target bot ID (empty if standalone)
//! 13. tenant        - selected interface tenant (empty if none)
//! 14. success       - `Y` (`result_code == "00"`) or `N`
//! 15. result_code   - `00`=ok, `01`=protocol/biz error, `02`=server/parse/client error,
//!     `03`=timeout, `04`=connect/transport error, `05`=rate-limited(429),
//!     `06`=auth-rejected(401/403), `07`=cancelled
//! 16. http_status   - HTTP status code (`0` when no response was received)
//! 17. error_code    - bounded structured error code/reason (empty on `00`)
//! 18. req_bytes     - serialized request body length in bytes (`0` for GET)
//! 19. resp_bytes    - received response body bytes (cumulative SSE bytes for STREAM)
//! 20. duration_ms   - total call latency in integer milliseconds
//! 21. ttfb_ms       - response-header latency in integer milliseconds (`-1` if unreceived)
//! 22. stream_events - decoded SSE `StreamResponse` event count (`0` for SYNC)

use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use std::time::Instant;

pub const A2A_HTTP_CLIENT_DIGEST_TARGET: &str = "bcs_a2a_http_client_digest";
pub const A2A_HTTP_CLIENT_DIGEST_COLUMNS: usize = 22;

pub const RESULT_OK: &str = "00";
pub const RESULT_PROTOCOL_ERROR: &str = "01";
pub const RESULT_SERVER_OR_PARSE_ERROR: &str = "02";
pub const RESULT_TIMEOUT: &str = "03";
pub const RESULT_TRANSPORT_ERROR: &str = "04";
pub const RESULT_RATE_LIMITED: &str = "05";
pub const RESULT_AUTH_REJECTED: &str = "06";
pub const RESULT_CANCELLED: &str = "07";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct A2aCallContext {
    pub bot_id: String,
    pub provider_id: String,
    pub run_id: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Protocol {
    HttpJson,
    JsonRpc,
}

impl Protocol {
    fn as_str(self) -> &'static str {
        match self { Self::HttpJson => "HTTP+JSON", Self::JsonRpc => "JSONRPC" }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum InvokeType {
    Sync,
    Stream,
}

impl InvokeType {
    fn as_str(self) -> &'static str {
        match self { Self::Sync => "SYNC", Self::Stream => "STREAM" }
    }
}

/// RAII digest recorder ensuring every HTTP+JSON / JSONRPC call emits
/// exactly one digest record across success, error, timeout, stream end, or drop.
pub(crate) struct DigestGuard {
    trace_id: String,
    span_id: String,
    request_id: String,
    context: A2aCallContext,
    protocol: Protocol,
    invoke_type: InvokeType,
    method: &'static str,
    target_host: String,
    target_path: String,
    tenant: String,
    req_bytes: usize,
    resp_bytes: Arc<AtomicUsize>,
    http_status: u16,
    ttfb_ms: i64,
    headers_received: bool,
    stream_events: u64,
    started: Instant,
    finished: bool,
}

pub(crate) struct DigestStart<'a> {
    pub protocol: Protocol,
    pub invoke_type: InvokeType,
    pub method: &'static str,
    pub url: &'a str,
    pub canonical_path: Option<String>,
    pub tenant: Option<&'a str>,
    pub context: &'a A2aCallContext,
}

impl DigestGuard {
    pub(crate) fn start(params: DigestStart<'_>) -> Self {
        let (target_host, parsed_path) = split_host_and_path(params.url);
        let target_path = params.canonical_path.unwrap_or(parsed_path);
        let (trace_id, span_id) = extract_correlation(bcs_tracing_api::current_log_correlation().as_ref());
        Self {
            trace_id,
            span_id,
            request_id: bcs_observability::current_request_id(),
            context: params.context.clone(),
            protocol: params.protocol,
            invoke_type: params.invoke_type,
            method: params.method,
            target_host,
            target_path,
            tenant: params.tenant.unwrap_or("").to_owned(),
            req_bytes: 0,
            resp_bytes: Arc::new(AtomicUsize::new(0)),
            http_status: 0,
            ttfb_ms: -1,
            headers_received: false,
            stream_events: 0,
            started: Instant::now(),
            finished: false,
        }
    }

    pub(crate) fn set_req_bytes(&mut self, bytes: usize) { self.req_bytes = bytes; }

    pub(crate) fn set_log_correlation(&mut self, correlation: &bcs_tracing_api::LogCorrelation) {
        let [(_, trace_id), (_, span_id)] = correlation.fields();
        self.trace_id = trace_id.to_owned();
        self.span_id = span_id.to_owned();
    }

    pub(crate) fn record_headers(&mut self, status: u16) {
        self.http_status = status;
        self.headers_received = true;
        self.ttfb_ms = i64::try_from(self.started.elapsed().as_millis()).unwrap_or(i64::MAX);
    }

    pub(crate) fn resp_bytes_counter(&self) -> Arc<AtomicUsize> { Arc::clone(&self.resp_bytes) }

    pub(crate) fn add_resp_bytes(&self, bytes: usize) {
        self.resp_bytes.fetch_add(bytes, Ordering::Relaxed);
    }

    pub(crate) fn inc_stream_events(&mut self) {
        self.stream_events = self.stream_events.saturating_add(1);
    }

    pub(crate) fn finish_ok(&mut self) { self.emit(RESULT_OK, ""); }

    pub(crate) fn finish_err(&mut self, result_code: &'static str, error_code: &str) {
        self.emit(result_code, error_code);
    }

    pub(crate) fn observe<T>(&mut self, result: Result<T, a2a::A2AError>) -> Result<T, a2a::A2AError> {
        match &result { Ok(_) => self.finish_ok(), Err(error) => self.finish_a2a_error(error) }
        result
    }

    pub(crate) fn observe_err<T>(&mut self, result: Result<T, a2a::A2AError>) -> Result<T, a2a::A2AError> {
        if let Err(error) = &result { self.finish_a2a_error(error); }
        result
    }

    fn finish_a2a_error(&mut self, error: &a2a::A2AError) {
        if self.finished { return; }
        let (rc, ec) = classify_a2a_error(self.protocol, self.headers_received, self.http_status, error);
        self.emit(rc, &ec);
    }

    fn emit(&mut self, result_code: &'static str, error_code: &str) {
        if self.finished { return; }
        self.finished = true;
        let timestamp = chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f");
        let duration_ms = self.started.elapsed().as_millis();
        let success = if result_code == RESULT_OK { "Y" } else { "N" };
        let resp_bytes = self.resp_bytes.load(Ordering::Relaxed);
        let line = format!(
            "{timestamp},{},{},{},{},{},{},{},{},{},{},{},{},{success},{result_code},{},{},{},{resp_bytes},{duration_ms},{},{}",
            sanitize_csv_field(&self.trace_id),
            sanitize_csv_field(&self.span_id),
            sanitize_csv_field(&self.request_id),
            sanitize_csv_field(&self.context.run_id),
            self.protocol.as_str(),
            self.invoke_type.as_str(),
            self.method,
            sanitize_csv_field(&self.target_host),
            sanitize_csv_field(&self.target_path),
            sanitize_csv_field(&self.context.provider_id),
            sanitize_csv_field(&self.context.bot_id),
            sanitize_csv_field(&self.tenant),
            self.http_status,
            sanitize_csv_field(error_code),
            self.req_bytes,
            self.ttfb_ms,
            self.stream_events,
        );
        tracing::info!(target: A2A_HTTP_CLIENT_DIGEST_TARGET, "{line}");
    }
}

impl Drop for DigestGuard {
    fn drop(&mut self) {
        if !self.finished {
            if self.invoke_type == InvokeType::Stream && self.headers_received && self.stream_events > 0 {
                self.emit(RESULT_OK, "");
            } else {
                self.emit(RESULT_CANCELLED, "CANCELLED");
            }
        }
    }
}

fn extract_correlation(correlation: Option<&bcs_tracing_api::LogCorrelation>) -> (String, String) {
    match correlation {
        Some(c) => { let [(_, tid), (_, sid)] = c.fields(); (tid.to_owned(), sid.to_owned()) }
        None => (String::new(), String::new()),
    }
}

pub(crate) fn rest_canonical_path(base_url: &str, suffix: &str) -> String {
    let (_, base_path) = split_host_and_path(base_url);
    format!("{}{suffix}", base_path.trim_end_matches('/'))
}

pub(crate) fn split_host_and_path(raw: &str) -> (String, String) {
    let Ok(url) = reqwest::Url::parse(raw) else { return (String::new(), String::new()); };
    let host = match (url.host_str(), url.port()) {
        (Some(host), Some(port)) => format!("{host}:{port}"),
        (Some(host), None) => host.to_owned(),
        (None, _) => String::new(),
    };
    (host, url.path().to_owned())
}

pub(crate) fn classify_http_status(status: u16) -> &'static str {
    match status {
        401 | 403 => RESULT_AUTH_REJECTED,
        429 => RESULT_RATE_LIMITED,
        408 | 504 => RESULT_TIMEOUT,
        500..=599 => RESULT_SERVER_OR_PARSE_ERROR,
        _ => RESULT_PROTOCOL_ERROR,
    }
}

fn classify_a2a_error(
    protocol: Protocol,
    headers_received: bool,
    status: u16,
    error: &a2a::A2AError,
) -> (&'static str, String) {
    if crate::http_client::identity_failure(error).is_some() {
        return (RESULT_AUTH_REJECTED, "IDENTITY_ERROR".into());
    }
    if error.message.contains("timed out") || error.message.contains("timeout") {
        return (RESULT_TIMEOUT, "TIMEOUT".into());
    }
    if error.message == "A2A JSON-RPC failure" {
        let rc = match status {
            401 | 403 => RESULT_AUTH_REJECTED,
            429 => RESULT_RATE_LIMITED,
            408 | 504 => RESULT_TIMEOUT,
            _ => RESULT_PROTOCOL_ERROR,
        };
        return (rc, error.code.to_string());
    }
    if headers_received && !(200..300).contains(&status) {
        let ec = if error.code != a2a::error_code::INTERNAL_ERROR
            && error.code != a2a::error_code::INVALID_AGENT_RESPONSE
        {
            a2a::error_reason(error.code).to_owned()
        } else {
            format!("HTTP_{status}")
        };
        return (classify_http_status(status), ec);
    }
    match error.code {
        a2a::error_code::INTERNAL_ERROR => (RESULT_TRANSPORT_ERROR, "TRANSPORT_ERROR".into()),
        a2a::error_code::INVALID_AGENT_RESPONSE => (
            RESULT_SERVER_OR_PARSE_ERROR,
            if protocol == Protocol::JsonRpc { "INVALID_ENVELOPE" } else { "INVALID_RESPONSE" }.into(),
        ),
        a2a::error_code::PARSE_ERROR => (RESULT_SERVER_OR_PARSE_ERROR, "PARSE_ERROR".into()),
        a2a::error_code::INVALID_PARAMS => (RESULT_SERVER_OR_PARSE_ERROR, "INVALID_PARAMS".into()),
        code => (RESULT_SERVER_OR_PARSE_ERROR, a2a::error_reason(code).to_owned()),
    }
}

pub(crate) fn classify_reqwest_error(error: &reqwest::Error) -> (&'static str, &'static str) {
    if error.is_timeout() {
        (RESULT_TIMEOUT, "TIMEOUT")
    } else if error.is_connect() {
        (RESULT_TRANSPORT_ERROR, "CONNECT_ERROR")
    } else {
        (RESULT_TRANSPORT_ERROR, "TRANSPORT_ERROR")
    }
}

pub(crate) fn sanitize_csv_field(value: &str) -> String {
    value
        .chars()
        .map(|ch| if matches!(ch, ',' | '\n' | '\r' | '\t') || ch.is_control() { ' ' } else { ch })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use tracing_subscriber::layer::SubscriberExt;

    #[derive(Clone, Default)]
    struct Capture(Arc<Mutex<Vec<String>>>);

    impl<S: tracing::Subscriber> tracing_subscriber::Layer<S> for Capture {
        fn on_event(&self, event: &tracing::Event<'_>, _ctx: tracing_subscriber::layer::Context<'_, S>) {
            if event.metadata().target() != A2A_HTTP_CLIENT_DIGEST_TARGET { return; }
            #[derive(Default)]
            struct Msg(String);
            impl tracing::field::Visit for Msg {
                fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
                    if field.name() == "message" { self.0 = format!("{value:?}"); }
                }
                fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
                    if field.name() == "message" { self.0 = value.to_owned(); }
                }
            }
            let mut msg = Msg::default();
            event.record(&mut msg);
            if let Ok(mut lines) = self.0.lock() { lines.push(msg.0); }
        }
    }

    #[test]
    fn split_host_and_path_strips_credentials_query_and_fragment() {
        let (host, path) = split_host_and_path("https://user:secret@agent.example.com:8443/a2a/v1?token=abc#frag");
        assert_eq!((host.as_str(), path.as_str()), ("agent.example.com:8443", "/a2a/v1"));
        assert_eq!(split_host_and_path("not a url"), (String::new(), String::new()));
        assert_eq!(rest_canonical_path("https://agent.example.com/a2a/", "/tasks/{id}:cancel"), "/a2a/tasks/{id}:cancel");
    }

    #[test]
    fn guard_emits_exactly_once_with_22_sanitized_columns() {
        let capture = Capture::default();
        let dispatch = tracing::Dispatch::new(tracing_subscriber::registry().with(capture.clone()));
        let parent_ids = bcs_tracing_api::LogCorrelation::new("sofa_trace_id", "trace-parent", "sofa_rpc_id", "0").unwrap();
        let child_ids = bcs_tracing_api::LogCorrelation::new("sofa_trace_id", "trace-parent", "sofa_rpc_id", "0.1").unwrap();
        tracing::dispatcher::with_default(&dispatch, || {
            let ctx = A2aCallContext { bot_id: "bot,1".into(), provider_id: "prv\n1".into(), run_id: "run-1".into() };
            let mut guard = bcs_tracing_api::with_log_correlation(Some(parent_ids), || {
                DigestGuard::start(DigestStart {
                    protocol: Protocol::HttpJson, invoke_type: InvokeType::Sync, method: "SendMessage",
                    url: "https://u:p@agent.example.com:443/a2a?q=1", canonical_path: Some("/a2a/message:send".into()),
                    tenant: Some("tenant,a"), context: &ctx,
                })
            });
            guard.set_req_bytes(128);
            guard.set_log_correlation(&child_ids);
            guard.record_headers(200);
            guard.add_resp_bytes(256);
            guard.finish_ok();
            guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
        });
        let lines = capture.0.lock().unwrap_or_else(|e| e.into_inner()).clone();
        assert_eq!(lines.len(), 1);
        let cols: Vec<&str> = lines[0].split(',').collect();
        assert_eq!(cols.len(), A2A_HTTP_CLIENT_DIGEST_COLUMNS, "line: {}", lines[0]);
        assert_eq!((cols[1], cols[2], cols[4], cols[5], cols[6], cols[7]), ("trace-parent", "0.1", "run-1", "HTTP+JSON", "SYNC", "SendMessage"));
        assert_eq!((cols[8], cols[9], cols[10], cols[11], cols[12]), ("agent.example.com", "/a2a/message:send", "prv 1", "bot 1", "tenant a"));
        assert_eq!((cols[13], cols[14], cols[15], cols[16], cols[17], cols[18], cols[21]), ("Y", "00", "200", "", "128", "256", "0"));
    }

    #[test]
    fn classify_a2a_error_covers_identity_timeout_rpc_http_and_parse_variants() {
        assert_eq!(classify_a2a_error(Protocol::HttpJson, false, 0, &a2a::A2AError::internal("A2A application credential unavailable")), (RESULT_AUTH_REJECTED, "IDENTITY_ERROR".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, false, 0, &a2a::A2AError::internal("A2A Task request timed out")), (RESULT_TIMEOUT, "TIMEOUT".into()));
        assert_eq!(classify_a2a_error(Protocol::JsonRpc, true, 401, &a2a::A2AError::new(-32001, "A2A JSON-RPC failure")), (RESULT_AUTH_REJECTED, "-32001".into()));
        assert_eq!(classify_a2a_error(Protocol::JsonRpc, true, 429, &a2a::A2AError::new(-32001, "A2A JSON-RPC failure")), (RESULT_RATE_LIMITED, "-32001".into()));
        assert_eq!(classify_a2a_error(Protocol::JsonRpc, true, 504, &a2a::A2AError::new(-32001, "A2A JSON-RPC failure")), (RESULT_TIMEOUT, "-32001".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, true, 504, &a2a::A2AError::internal("A2A HTTP 504")), (RESULT_TIMEOUT, "HTTP_504".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, true, 200, &a2a::A2AError::invalid_agent_response()), (RESULT_SERVER_OR_PARSE_ERROR, "INVALID_RESPONSE".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, true, 200, &a2a::A2AError::parse_error("bad")), (RESULT_SERVER_OR_PARSE_ERROR, "PARSE_ERROR".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, false, 0, &a2a::A2AError::invalid_params("bad")), (RESULT_SERVER_OR_PARSE_ERROR, "INVALID_PARAMS".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, true, 200, &a2a::A2AError::invalid_request("big")), (RESULT_SERVER_OR_PARSE_ERROR, "INVALID_REQUEST".into()));
        assert_eq!(classify_a2a_error(Protocol::HttpJson, true, 200, &a2a::A2AError::internal("interrupted")), (RESULT_TRANSPORT_ERROR, "TRANSPORT_ERROR".into()));
    }

    #[test]
    fn stream_drop_distinguishes_received_events_from_empty_cancellation() {
        let capture = Capture::default();
        let dispatch = tracing::Dispatch::new(tracing_subscriber::registry().with(capture.clone()));
        tracing::dispatcher::with_default(&dispatch, || {
            let ctx = A2aCallContext::default();
            {
                let mut g1 = DigestGuard::start(DigestStart {
                    protocol: Protocol::JsonRpc, invoke_type: InvokeType::Stream, method: "SendStreamingMessage",
                    url: "http://127.0.0.1:8080/rpc", canonical_path: None, tenant: None, context: &ctx,
                });
                g1.record_headers(200);
                g1.inc_stream_events();
            }
            {
                let mut g2 = DigestGuard::start(DigestStart {
                    protocol: Protocol::JsonRpc, invoke_type: InvokeType::Stream, method: "SubscribeToTask",
                    url: "http://127.0.0.1:8080/rpc", canonical_path: None, tenant: None, context: &ctx,
                });
                g2.record_headers(200);
            }
        });
        let lines = capture.0.lock().unwrap_or_else(|e| e.into_inner()).clone();
        assert_eq!(lines.len(), 2);
        let c1: Vec<&str> = lines[0].split(',').collect();
        let c2: Vec<&str> = lines[1].split(',').collect();
        assert_eq!((c1[13], c1[14], c1[21]), ("Y", "00", "1"));
        assert_eq!((c2[13], c2[14], c2[16], c2[21]), ("N", "07", "CANCELLED", "0"));
    }
}
