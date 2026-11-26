//! Canonical POST subscription for A2A 1.0.1, without legacy URL/method fallback.
use crate::digest::{
    DigestGuard, RESULT_PROTOCOL_ERROR, RESULT_SERVER_OR_PARSE_ERROR, RESULT_TIMEOUT,
    classify_jsonrpc_rpc_error, classify_reqwest_error, classify_rest_http_error,
};
use crate::http_client::HttpClient;
use crate::jsonrpc::DecodeError;
use a2a::{A2AError, StreamResponse};
use futures::{stream::{self, BoxStream}, StreamExt};

const MAX_EVENT_BYTES: usize = 1_048_576;

struct SseDecodeError {
    error: A2AError,
    result_code: &'static str,
    error_code: String,
}

#[derive(Default)]
struct Decoder {
    buffer: Vec<u8>,
    data: Vec<String>,
    event_bytes: usize,
    started: bool,
    rpc_id: Option<String>,
}

impl Decoder {
    fn next(&mut self, eof: bool) -> Result<Option<StreamResponse>, SseDecodeError> {
        loop {
            let Some(end) = self.buffer.iter().position(|byte| *byte == b'\n' || *byte == b'\r') else {
                if self.buffer.len().saturating_add(self.event_bytes) > MAX_EVENT_BYTES {
                    return Err(SseDecodeError {
                        error: A2AError::invalid_request("SSE event exceeds the size limit"),
                        result_code: RESULT_SERVER_OR_PARSE_ERROR,
                        error_code: "SIZE_LIMIT".into(),
                    });
                }
                return Ok(None);
            };
            let separator = if self.buffer[end] == b'\r' {
                if end + 1 == self.buffer.len() && !eof { return Ok(None); }
                if self.buffer.get(end + 1) == Some(&b'\n') { 2 } else { 1 }
            } else { 1 };
            self.event_bytes = self.event_bytes.saturating_add(end + separator);
            if self.event_bytes > MAX_EVENT_BYTES {
                return Err(SseDecodeError {
                    error: A2AError::invalid_request("SSE event exceeds the size limit"),
                    result_code: RESULT_SERVER_OR_PARSE_ERROR,
                    error_code: "SIZE_LIMIT".into(),
                });
            }
            let mut line = String::from_utf8(self.buffer.drain(..end).collect())
                .map_err(|_| SseDecodeError {
                    error: A2AError::parse_error("SSE contains invalid UTF-8"),
                    result_code: RESULT_SERVER_OR_PARSE_ERROR,
                    error_code: "PARSE_ERROR".into(),
                })?;
            self.buffer.drain(..separator);
            if !self.started { line = line.trim_start_matches('\u{feff}').to_owned(); self.started = true; }
            if line.is_empty() {
                self.event_bytes = 0;
                if self.data.is_empty() { continue; }
                let data = std::mem::take(&mut self.data).join("\n");
                let value = serde_json::from_str(&data).map_err(|_| SseDecodeError {
                    error: A2AError::parse_error("SSE contains invalid JSON"),
                    result_code: RESULT_SERVER_OR_PARSE_ERROR,
                    error_code: "PARSE_ERROR".into(),
                })?;
                let value = if let Some(id) = &self.rpc_id {
                    match crate::jsonrpc::decode_classified(value, id) {
                        Ok(value) => value,
                        Err(DecodeError::Protocol { error, rpc_code }) => {
                            return Err(SseDecodeError {
                                error,
                                result_code: RESULT_PROTOCOL_ERROR,
                                error_code: rpc_code.to_string(),
                            });
                        }
                        Err(DecodeError::InvalidEnvelope(error)) => {
                            return Err(SseDecodeError {
                                error,
                                result_code: RESULT_SERVER_OR_PARSE_ERROR,
                                error_code: "INVALID_ENVELOPE".into(),
                            });
                        }
                    }
                } else { value };
                return serde_json::from_value(value).map(Some).map_err(|_| SseDecodeError {
                    error: A2AError::parse_error("SSE contains invalid StreamResponse"),
                    result_code: RESULT_SERVER_OR_PARSE_ERROR,
                    error_code: "PARSE_ERROR".into(),
                });
            }
            if line.starts_with(':') { continue; }
            let (field, value) = line.split_once(':').unwrap_or((&line, ""));
            if field == "data" { self.data.push(value.strip_prefix(' ').unwrap_or(value).into()); }
        }
    }
}

pub(super) async fn post_stream(
    http: &HttpClient, url: &str, body: Vec<u8>, guard: DigestGuard,
) -> Result<BoxStream<'static, Result<StreamResponse, A2AError>>, A2AError> {
    post_stream_with_rpc(http, url, body, None, guard).await
}

pub(super) async fn post_stream_with_rpc(
    http: &HttpClient, url: &str, body: Vec<u8>, rpc_id: Option<String>, mut guard: DigestGuard,
) -> Result<BoxStream<'static, Result<StreamResponse, A2AError>>, A2AError> {
    let request = http.transport.request(|http| {
        http.post(url)
            .header("A2A-Version", "1.0")
            .header("Accept", "text/event-stream")
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
    });
    let mut response = match tokio::time::timeout(
        std::time::Duration::from_secs(15),
        http.send_observed(request, "A2A subscription connection failed", &mut guard),
    ).await {
        Ok(Ok(response)) => response,
        Ok(Err(err)) => return Err(err),
        Err(_) => {
            guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
            return Err(A2AError::internal("A2A subscription header timeout"));
        }
    };
    let status = response.status();
    guard.record_headers(status.as_u16());
    let mime = response.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v|v.to_str().ok())
        .and_then(|v|v.split(';').next()).map(str::trim);
    let is_json = mime.is_some_and(|value| value.eq_ignore_ascii_case("application/json"));
    let is_sse = mime.is_some_and(|value| value.eq_ignore_ascii_case("text/event-stream"));
    let content_type = match mime {
        _ if is_json => "application/json",
        _ if is_sse => "text/event-stream",
        Some(value) if value.eq_ignore_ascii_case("text/plain") => "text/plain",
        Some(value) if value.eq_ignore_ascii_case("text/html") => "text/html",
        Some(value) if value.eq_ignore_ascii_case("application/problem+json") => "application/problem+json",
        Some(_) => "other",
        None => "missing",
    };
    let mut diagnostic = crate::test_response::Diagnostic::from_response(&mut response, content_type);
    let preview_size = diagnostic.as_ref().map(|entry| entry.preview_budget());
    // The end marker lets the diagnostic handle chunked bodies with no Content-Length.
    let mut chunks = response.bytes_stream()
        .map(Some)
        .chain(stream::iter([None]))
        .inspect(move |item| {
            if let Some(entry) = &mut diagnostic {
                match item {
                    Some(Ok(chunk)) => entry.observe(chunk),
                    Some(Err(_)) => entry.finish("body_read_error"),
                    None => entry.finish("complete"),
                }
            }
        })
        .filter_map(futures::future::ready);
    if !status.is_success() {
        return Err(match tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut body = Vec::new();
            loop {
                match chunks.next().await.transpose() {
                    Ok(Some(chunk)) => {
                        guard.add_resp_bytes(chunk.len());
                        if body.len().saturating_add(chunk.len()) > MAX_EVENT_BYTES {
                            guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "SIZE_LIMIT");
                            return Err(A2AError::invalid_request("A2A error response exceeds size limit"));
                        }
                        body.extend_from_slice(&chunk);
                    }
                    Ok(None) => break,
                    Err(err) => {
                        let (rc, ec) = classify_reqwest_error(&err);
                        guard.finish_err(rc, ec);
                        return Err(A2AError::internal("A2A error response incomplete"));
                    }
                }
            }
            if let Some(id) = &rpc_id
                && let Ok(value) = serde_json::from_slice(&body)
                && let Err(DecodeError::Protocol { error, rpc_code }) = crate::jsonrpc::decode_classified(value, id)
            {
                let (rc, ec) = classify_jsonrpc_rpc_error(status.as_u16(), rpc_code);
                guard.finish_err(rc, &ec);
                return Ok(error);
            }
            let err = crate::client_json::http_error(status.as_u16(), &body);
            let (rc, ec) = classify_rest_http_error(status.as_u16(), &err);
            guard.finish_err(rc, &ec);
            Ok(err)
        }).await {
            Ok(result) => result?,
            Err(_) => {
                guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
                return Err(A2AError::internal("A2A error response timed out"));
            }
        });
    }
    if rpc_id.is_some() && is_json {
        return Err(match tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut body = Vec::new();
            loop {
                match chunks.next().await.transpose() {
                    Ok(Some(chunk)) => {
                        guard.add_resp_bytes(chunk.len());
                        if body.len().saturating_add(chunk.len()) > MAX_EVENT_BYTES {
                            guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "SIZE_LIMIT");
                            return Err(A2AError::invalid_agent_response());
                        }
                        body.extend_from_slice(&chunk);
                    }
                    Ok(None) => break,
                    Err(err) => {
                        let (rc, ec) = classify_reqwest_error(&err);
                        guard.finish_err(rc, ec);
                        return Err(A2AError::internal("RPC error response incomplete"));
                    }
                }
            }
            let value = match serde_json::from_slice(&body) {
                Ok(value) => value,
                Err(_) => {
                    guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "PARSE_ERROR");
                    return Err(A2AError::invalid_agent_response());
                }
            };
            match crate::jsonrpc::decode_classified(value, rpc_id.as_deref().unwrap_or("")) {
                Err(DecodeError::Protocol { error, rpc_code }) => {
                    let (rc, ec) = classify_jsonrpc_rpc_error(status.as_u16(), rpc_code);
                    guard.finish_err(rc, &ec);
                    Ok(error)
                }
                Err(DecodeError::InvalidEnvelope(error)) => {
                    guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "INVALID_ENVELOPE");
                    Ok(error)
                }
                Ok(_) => {
                    guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "INVALID_CONTENT_TYPE");
                    Err(A2AError::invalid_agent_response())
                }
            }
        }).await {
            Ok(result) => result?,
            Err(_) => {
                guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
                return Err(A2AError::internal("RPC error response timed out"));
            }
        });
    }
    if !is_sse {
        if let Some(size) = preview_size {
            // This failure normally skips the body. Give the finite preview a short read budget.
            let _ = tokio::time::timeout(std::time::Duration::from_millis(250), async {
                let mut received = 0;
                while received < size {
                    let Some(Ok(chunk)) = chunks.next().await else { break };
                    guard.add_resp_bytes(chunk.len());
                    received += chunk.len();
                }
            }).await;
        }
        guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "INVALID_CONTENT_TYPE");
        return Err(A2AError::invalid_agent_response());
    }
    Ok(stream::try_unfold((chunks, Decoder { rpc_id, ..Default::default() }, false, guard), |(mut chunks, mut decoder, mut eof, mut guard)| async move {
        loop {
            match decoder.next(eof) {
                Ok(Some(event)) => {
                    guard.inc_stream_events();
                    return Ok(Some((event, (chunks, decoder, eof, guard))));
                }
                Ok(None) => {}
                Err(err) => {
                    guard.finish_err(err.result_code, &err.error_code);
                    return Err(err.error);
                }
            }
            if eof {
                return if decoder.buffer.is_empty() && decoder.data.is_empty() {
                    guard.finish_ok();
                    Ok(None)
                } else {
                    guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "STREAM_TRUNCATED");
                    Err(A2AError::parse_error("A2A subscription ended mid-event"))
                };
            }
            match chunks.next().await.transpose() {
                Ok(Some(chunk)) => {
                    guard.add_resp_bytes(chunk.len());
                    decoder.buffer.extend_from_slice(&chunk);
                }
                Ok(None) => eof = true,
                Err(err) => {
                    let (rc, ec) = classify_reqwest_error(&err);
                    guard.finish_err(rc, ec);
                    return Err(A2AError::internal("A2A subscription interrupted"));
                }
            }
        }
    }).boxed())
}
