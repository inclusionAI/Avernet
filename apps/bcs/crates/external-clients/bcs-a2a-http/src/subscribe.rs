//! Canonical POST subscription for A2A 1.0.1, without legacy URL/method fallback.
use crate::digest::DigestGuard;
use crate::http_client::HttpClient;
use a2a::{A2AError, StreamResponse};
use futures::{stream::{self, BoxStream}, StreamExt};
use std::sync::atomic::Ordering;

const MAX_EVENT_BYTES: usize = 1_048_576;

#[derive(Default)]
struct Decoder {
    buffer: Vec<u8>,
    data: Vec<String>,
    event_bytes: usize,
    started: bool,
    rpc_id: Option<String>,
}

impl Decoder {
    fn next(&mut self, eof: bool) -> Result<Option<StreamResponse>, A2AError> {
        loop {
            let Some(end) = self.buffer.iter().position(|byte| *byte == b'\n' || *byte == b'\r') else {
                if self.buffer.len().saturating_add(self.event_bytes) > MAX_EVENT_BYTES {
                    return Err(A2AError::invalid_request("SSE event exceeds the size limit"));
                }
                return Ok(None);
            };
            let separator = if self.buffer[end] == b'\r' {
                if end + 1 == self.buffer.len() && !eof { return Ok(None); }
                if self.buffer.get(end + 1) == Some(&b'\n') { 2 } else { 1 }
            } else { 1 };
            self.event_bytes = self.event_bytes.saturating_add(end + separator);
            if self.event_bytes > MAX_EVENT_BYTES { return Err(A2AError::invalid_request("SSE event exceeds the size limit")); }
            let mut line = String::from_utf8(self.buffer.drain(..end).collect())
                .map_err(|_|A2AError::parse_error("SSE contains invalid UTF-8"))?;
            self.buffer.drain(..separator);
            if !self.started { line = line.trim_start_matches('\u{feff}').to_owned(); self.started = true; }
            if line.is_empty() {
                self.event_bytes = 0;
                if self.data.is_empty() { continue; }
                let data = std::mem::take(&mut self.data).join("\n");
                let value = serde_json::from_str(&data).map_err(|_|A2AError::parse_error("SSE contains invalid JSON"))?;
                let value = if let Some(id) = &self.rpc_id { crate::jsonrpc::decode(value, id)? } else { value };
                return serde_json::from_value(value).map(Some)
                    .map_err(|_|A2AError::parse_error("SSE contains invalid StreamResponse"));
            }
            if line.starts_with(':') { continue; }
            let (field, value) = line.split_once(':').unwrap_or((&line, ""));
            if field == "data" { self.data.push(value.strip_prefix(' ').unwrap_or(value).into()); }
        }
    }
}

pub(super) async fn post_stream<T: serde::Serialize + ?Sized>(
    http: &HttpClient, url: &str, request: &T, guard: DigestGuard,
) -> Result<BoxStream<'static, Result<StreamResponse, A2AError>>, A2AError> {
    post_stream_with_rpc(http, url, request, None, guard).await
}

pub(super) async fn post_stream_with_rpc<T: serde::Serialize + ?Sized>(
    http: &HttpClient, url: &str, request: &T, rpc_id: Option<String>, mut guard: DigestGuard,
) -> Result<BoxStream<'static, Result<StreamResponse, A2AError>>, A2AError> {
    let request = http.transport.request(|http| {
        http.post(url).header("A2A-Version", "1.0").header("Accept", "text/event-stream").json(request)
    });
    let response = tokio::time::timeout(
        std::time::Duration::from_secs(15),
        http.send_observed(request, "A2A subscription connection failed", &mut guard),
    ).await.map_err(|_| A2AError::internal("A2A subscription header timeout")).and_then(|r| r);
    let mut response = guard.observe_err(response)?;
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
    let resp_bytes = guard.resp_bytes_counter();
    // The end marker lets the diagnostic handle chunked bodies with no Content-Length.
    let mut chunks = response.bytes_stream()
        .map(Some)
        .chain(stream::iter([None]))
        .inspect(move |item| {
            if let Some(Ok(chunk)) = item { resp_bytes.fetch_add(chunk.len(), Ordering::Relaxed); }
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
        return guard.observe_err(Err(tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut body = Vec::new();
            while let Some(chunk) = chunks.next().await.transpose()
                .map_err(|_|A2AError::internal("A2A error response incomplete"))? {
                if body.len().saturating_add(chunk.len()) > MAX_EVENT_BYTES {
                    return Err(A2AError::invalid_request("A2A error response exceeds size limit"));
                }
                body.extend_from_slice(&chunk);
            }
            if let Some(id)=&rpc_id {
                if let Ok(value)=serde_json::from_slice(&body) {
                    if let Err(error)=crate::jsonrpc::decode(value,id) {return Ok(error);}
                }
            }
            Ok(crate::client_json::http_error(status.as_u16(), &body))
        }).await.map_err(|_|A2AError::internal("A2A error response timed out")).and_then(|r| r).unwrap_or_else(|e| e)));
    }
    if rpc_id.is_some() && is_json {
        return guard.observe_err(Err(tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut body=Vec::new();
            while let Some(chunk)=chunks.next().await.transpose().map_err(|_|A2AError::internal("RPC error response incomplete"))? {
                if body.len().saturating_add(chunk.len())>MAX_EVENT_BYTES {return Err(A2AError::invalid_agent_response());}
                body.extend_from_slice(&chunk);
            }
            let value=serde_json::from_slice(&body).map_err(|_|A2AError::invalid_agent_response())?;
            match crate::jsonrpc::decode(value,rpc_id.as_deref().unwrap_or("")) {
                Err(error)=>Ok(error),Ok(_)=>Err(A2AError::invalid_agent_response()),
            }
        }).await.map_err(|_|A2AError::internal("RPC error response timed out")).and_then(|r| r).unwrap_or_else(|e| e)));
    }
    if !is_sse {
        if let Some(size) = preview_size {
            // This failure normally skips the body. Give the finite preview a short read budget.
            let _ = tokio::time::timeout(std::time::Duration::from_millis(250), async {
                let mut received = 0;
                while received < size {
                    let Some(Ok(chunk)) = chunks.next().await else { break };
                    received += chunk.len();
                }
            }).await;
        }
        return guard.observe_err(Err(A2AError::invalid_agent_response()));
    }
    Ok(stream::try_unfold((chunks, Decoder { rpc_id, ..Default::default() }, false, guard), |(mut chunks, mut decoder, mut eof, mut guard)| async move {
        let step: Result<Option<StreamResponse>, A2AError> = async {
            loop {
                if let Some(event) = decoder.next(eof)? { guard.inc_stream_events(); return Ok(Some(event)); }
                if eof {
                    return if decoder.buffer.is_empty() && decoder.data.is_empty() { guard.finish_ok(); Ok(None) }
                        else { Err(A2AError::parse_error("A2A subscription ended mid-event")) };
                }
                match chunks.next().await.transpose().map_err(|_|A2AError::internal("A2A subscription interrupted"))? {
                    Some(chunk) => decoder.buffer.extend_from_slice(&chunk),
                    None => eof = true,
                }
            }
        }.await;
        match guard.observe_err(step)? {
            Some(event) => Ok(Some((event, (chunks, decoder, eof, guard)))),
            None => Ok(None),
        }
    }).boxed())
}
