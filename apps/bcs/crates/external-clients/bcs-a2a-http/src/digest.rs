//! Framework-level client digest logging for A2A HTTP+JSON and JSON-RPC calls.
//!
//! Emits one fixed-column CSV record per outbound call to the dedicated
//! `bcs_a2a_http_client_digest` tracing target, modeled after SOFARPC's
//! `rpc-client-digest.log` for direct success-rate and latency monitoring.
//!
//! Column schema (20 comma-separated fields):
//!  1. timestamp     - local time `YYYY-MM-DD HH:mm:ss.SSS`
//!  2. request_id    - BCS request ID from `bcs-observability` (empty if none)
//!  3. run_id        - BCS run ID from `A2aCallContext` (empty if standalone)
//!  4. protocol      - `HTTP+JSON` or `JSONRPC`
//!  5. invoke_type   - `SYNC` or `STREAM`
//!  6. method        - `SendMessage` | `SendStreamingMessage` | `GetTask` | `CancelTask` | `SubscribeToTask`
//!  7. target_host   - sanitized `host[:port]` (never credentials/query/fragment)
//!  8. target_path   - sanitized canonical path (REST task IDs normalized to `{id}`)
//!  9. provider_id   - BCS provider ID (empty if standalone)
//! 10. bot_id        - BCS target bot ID (empty if standalone)
//! 11. tenant        - selected interface tenant (empty if none)
//! 12. success       - `Y` (`result_code == "00"`) or `N`
//! 13. result_code   - `00`=ok, `01`=protocol/biz error, `02`=server/parse/client error,
//!     `03`=timeout, `04`=connect/transport error, `05`=rate-limited(429),
//!     `06`=auth-rejected(401/403), `07`=cancelled
//! 14. http_status   - HTTP status code (`0` when no response was received)
//! 15. error_code    - bounded structured error code/reason (empty on `00`)
//! 16. req_bytes     - serialized request body length in bytes (`0` for GET)
//! 17. resp_bytes    - received response body bytes (cumulative SSE bytes for STREAM)
//! 18. duration_ms   - total call latency in integer milliseconds
//! 19. ttfb_ms       - response-header latency in integer milliseconds (`-1` if unreceived)
//! 20. stream_events - decoded SSE `StreamResponse` event count (`0` for SYNC)

use std::time::Instant;

pub const A2A_HTTP_CLIENT_DIGEST_TARGET: &str = "bcs_a2a_http_client_digest";
pub const A2A_HTTP_CLIENT_DIGEST_COLUMNS: usize = 20;

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
        match self {
            Self::HttpJson => "HTTP+JSON",
            Self::JsonRpc => "JSONRPC",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum InvokeType {
    Sync,
    Stream,
}

impl InvokeType {
    fn as_str(self) -> &'static str {
        match self {
            Self::Sync => "SYNC",
            Self::Stream => "STREAM",
        }
    }
}

/// RAII digest recorder ensuring every HTTP+JSON / JSONRPC call emits
/// exactly one digest record across success, error, timeout, stream end, or drop.
pub(crate) struct DigestGuard {
    request_id: String,
    context: A2aCallContext,
    protocol: Protocol,
    invoke_type: InvokeType,
    method: &'static str,
    target_host: String,
    target_path: String,
    tenant: String,
    req_bytes: usize,
    resp_bytes: usize,
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
    pub req_bytes: usize,
}

impl DigestGuard {
    pub(crate) fn start(params: DigestStart<'_>) -> Self {
        let (target_host, parsed_path) = split_host_and_path(params.url);
        let target_path = params.canonical_path.unwrap_or(parsed_path);
        Self {
            request_id: bcs_observability::current_request_id(),
            context: params.context.clone(),
            protocol: params.protocol,
            invoke_type: params.invoke_type,
            method: params.method,
            target_host,
            target_path,
            tenant: params.tenant.unwrap_or("").to_owned(),
            req_bytes: params.req_bytes,
            resp_bytes: 0,
            http_status: 0,
            ttfb_ms: -1,
            headers_received: false,
            stream_events: 0,
            started: Instant::now(),
            finished: false,
        }
    }

    pub(crate) fn record_headers(&mut self, status: u16) {
        self.http_status = status;
        self.headers_received = true;
        self.ttfb_ms = elapsed_ms_i64(self.started);
    }

    pub(crate) fn add_resp_bytes(&mut self, bytes: usize) {
        self.resp_bytes = self.resp_bytes.saturating_add(bytes);
    }

    pub(crate) fn inc_stream_events(&mut self) {
        self.stream_events = self.stream_events.saturating_add(1);
    }

    pub(crate) fn finish_ok(&mut self) {
        self.emit(RESULT_OK, "");
    }

    pub(crate) fn finish_err(&mut self, result_code: &'static str, error_code: &str) {
        self.emit(result_code, error_code);
    }

    fn emit(&mut self, result_code: &'static str, error_code: &str) {
        if self.finished {
            return;
        }
        self.finished = true;
        let timestamp = chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f");
        let duration_ms = self.started.elapsed().as_millis();
        let success = if result_code == RESULT_OK { "Y" } else { "N" };
        let line = format!(
            "{timestamp},{},{},{},{},{},{},{},{},{},{},{success},{result_code},{},{},{},{},{duration_ms},{},{}",
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
            self.resp_bytes,
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

pub(crate) fn rest_canonical_path(base_url: &str, suffix: &str) -> String {
    let (_, base_path) = split_host_and_path(base_url);
    let trimmed = base_path.trim_end_matches('/');
    format!("{trimmed}{suffix}")
}

pub(crate) fn split_host_and_path(raw: &str) -> (String, String) {
    match reqwest::Url::parse(raw) {
        Ok(url) => {
            let host = match (url.host_str(), url.port()) {
                (Some(host), Some(port)) => format!("{host}:{port}"),
                (Some(host), None) => host.to_owned(),
                (None, _) => String::new(),
            };
            (host, url.path().to_owned())
        }
        Err(_) => (String::new(), String::new()),
    }
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

pub(crate) fn classify_rest_http_error(status: u16, error: &a2a::A2AError) -> (&'static str, String) {
    let result_code = classify_http_status(status);
    let error_code = if error.code != a2a::error_code::INTERNAL_ERROR {
        a2a::error_reason(error.code).to_owned()
    } else {
        format!("HTTP_{status}")
    };
    (result_code, error_code)
}

pub(crate) fn classify_jsonrpc_rpc_error(status: u16, rpc_code: i32) -> (&'static str, String) {
    let result_code = match status {
        401 | 403 => RESULT_AUTH_REJECTED,
        429 => RESULT_RATE_LIMITED,
        408 | 504 => RESULT_TIMEOUT,
        _ => RESULT_PROTOCOL_ERROR,
    };
    (result_code, rpc_code.to_string())
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

fn elapsed_ms_i64(started: Instant) -> i64 {
    i64::try_from(started.elapsed().as_millis()).unwrap_or(i64::MAX)
}

pub(crate) fn sanitize_csv_field(value: &str) -> String {
    value
        .chars()
        .map(|ch| match ch {
            ',' | '\n' | '\r' | '\t' => ' ',
            ch if ch.is_control() => ' ',
            ch => ch,
        })
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

    #[test]
    fn split_host_and_path_strips_credentials_query_and_fragment() {
        let (host, path) = split_host_and_path("https://user:secret@agent.example.com:8443/a2a/v1?token=abc#frag");
        assert_eq!(host, "agent.example.com:8443");
        assert_eq!(path, "/a2a/v1");
        assert!(!host.contains("secret"));
        assert!(!path.contains("abc"));
        assert_eq!(rest_canonical_path("https://agent.example.com/a2a/", "/tasks/{id}:cancel"), "/a2a/tasks/{id}:cancel");
    }

    #[test]
    fn guard_emits_exactly_once_with_20_sanitized_columns() {
        let capture = Capture::default();
        let subscriber = tracing_subscriber::registry().with(capture.clone());
        let dispatch = tracing::Dispatch::new(subscriber);
        tracing::dispatcher::with_default(&dispatch, || {
            let ctx = A2aCallContext {
                bot_id: "bot,1".into(),
                provider_id: "prv\n1".into(),
                run_id: "run-1".into(),
            };
            let mut guard = DigestGuard::start(DigestStart {
                protocol: Protocol::HttpJson,
                invoke_type: InvokeType::Sync,
                method: "SendMessage",
                url: "https://u:p@agent.example.com:443/a2a?q=1",
                canonical_path: Some("/a2a/message:send".into()),
                tenant: Some("tenant,a"),
                context: &ctx,
                req_bytes: 128,
            });
            guard.record_headers(200);
            guard.add_resp_bytes(256);
            guard.finish_ok();
            guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
        });
        let lines = capture.0.lock().unwrap_or_else(|e| e.into_inner()).clone();
        assert_eq!(lines.len(), 1);
        let cols: Vec<&str> = lines[0].split(',').collect();
        assert_eq!(cols.len(), A2A_HTTP_CLIENT_DIGEST_COLUMNS, "line: {}", lines[0]);
        assert_eq!(cols[2], "run-1");
        assert_eq!(cols[3], "HTTP+JSON");
        assert_eq!(cols[4], "SYNC");
        assert_eq!(cols[5], "SendMessage");
        assert_eq!(cols[6], "agent.example.com");
        assert_eq!(cols[7], "/a2a/message:send");
        assert_eq!(cols[8], "prv 1");
        assert_eq!(cols[9], "bot 1");
        assert_eq!(cols[10], "tenant a");
        assert_eq!(cols[11], "Y");
        assert_eq!(cols[12], "00");
        assert_eq!(cols[13], "200");
        assert_eq!(cols[14], "");
        assert_eq!(cols[15], "128");
        assert_eq!(cols[16], "256");
        assert_eq!(cols[19], "0");
    }

    #[test]
    fn stream_drop_distinguishes_received_events_from_empty_cancellation() {
        let capture = Capture::default();
        let subscriber = tracing_subscriber::registry().with(capture.clone());
        let dispatch = tracing::Dispatch::new(subscriber);
        tracing::dispatcher::with_default(&dispatch, || {
            let ctx = A2aCallContext::default();
            {
                let mut g1 = DigestGuard::start(DigestStart {
                    protocol: Protocol::JsonRpc,
                    invoke_type: InvokeType::Stream,
                    method: "SendStreamingMessage",
                    url: "http://127.0.0.1:8080/rpc",
                    canonical_path: None,
                    tenant: None,
                    context: &ctx,
                    req_bytes: 64,
                });
                g1.record_headers(200);
                g1.inc_stream_events();
            }
            {
                let mut g2 = DigestGuard::start(DigestStart {
                    protocol: Protocol::JsonRpc,
                    invoke_type: InvokeType::Stream,
                    method: "SubscribeToTask",
                    url: "http://127.0.0.1:8080/rpc",
                    canonical_path: None,
                    tenant: None,
                    context: &ctx,
                    req_bytes: 64,
                });
                g2.record_headers(200);
            }
        });
        let lines = capture.0.lock().unwrap_or_else(|e| e.into_inner()).clone();
        assert_eq!(lines.len(), 2);
        let c1: Vec<&str> = lines[0].split(',').collect();
        let c2: Vec<&str> = lines[1].split(',').collect();
        assert_eq!((c1[11], c1[12], c1[19]), ("Y", "00", "1"));
        assert_eq!((c2[11], c2[12], c2[14], c2[19]), ("N", "07", "CANCELLED", "0"));
    }
}
