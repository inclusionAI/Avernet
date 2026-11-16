//! Canonical POST subscription for A2A 1.0.1, without legacy URL/method fallback.
use bcs_network_tracing::RequestBuilderExt;
use a2a::{A2AError, StreamResponse};
use futures::{stream::{self, BoxStream}, StreamExt};

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
    http: &bcs_network_tracing::ClientWithDefaults<reqwest::Client>, url: &str, request: &T,
) -> Result<BoxStream<'static, Result<StreamResponse, A2AError>>, A2AError> {
    post_stream_with_rpc(http, url, request, None).await
}

pub(super) async fn post_stream_with_rpc<T: serde::Serialize + ?Sized>(
    http: &bcs_network_tracing::ClientWithDefaults<reqwest::Client>, url: &str, request: &T, rpc_id: Option<String>,
) -> Result<BoxStream<'static, Result<StreamResponse, A2AError>>, A2AError> {
    let response = tokio::time::timeout(std::time::Duration::from_secs(15),
        http.request(|http| http.post(url).header("A2A-Version", "1.0").header("Accept", "text/event-stream")
            .json(request)).send_traced()).await
        .map_err(|_|A2AError::internal("A2A subscription header timeout"))?
        .map_err(|_|A2AError::internal("A2A subscription connection failed"))?;
    let status = response.status();
    if !status.is_success() {
        return Err(tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut response = response;
            let mut body = Vec::new();
            while let Some(chunk) = response.chunk().await
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
        }).await.map_err(|_|A2AError::internal("A2A error response timed out"))??);
    }
    let mime = response.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v|v.to_str().ok())
        .and_then(|v|v.split(';').next()).map(str::trim);
    if rpc_id.is_some() && mime.is_some_and(|v|v.eq_ignore_ascii_case("application/json")) {
        return Err(tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut response=response;let mut body=Vec::new();
            while let Some(chunk)=response.chunk().await.map_err(|_|A2AError::internal("RPC error response incomplete"))? {
                if body.len().saturating_add(chunk.len())>MAX_EVENT_BYTES {return Err(A2AError::invalid_agent_response());}
                body.extend_from_slice(&chunk);
            }
            let value=serde_json::from_slice(&body).map_err(|_|A2AError::invalid_agent_response())?;
            match crate::jsonrpc::decode(value,rpc_id.as_deref().unwrap_or("")) {
                Err(error)=>Ok(error),Ok(_)=>Err(A2AError::invalid_agent_response()),
            }
        }).await.map_err(|_|A2AError::internal("RPC error response timed out"))??);
    }
    if !mime.is_some_and(|value| value.eq_ignore_ascii_case("text/event-stream")) { return Err(A2AError::invalid_agent_response()); }
    Ok(stream::try_unfold((response, Decoder { rpc_id, ..Default::default() }, false), |(mut response, mut decoder, mut eof)| async move {
        loop {
            if let Some(event) = decoder.next(eof)? { return Ok(Some((event, (response, decoder, eof)))); }
            if eof {
                return if decoder.buffer.is_empty() && decoder.data.is_empty() { Ok(None) }
                    else { Err(A2AError::parse_error("A2A subscription ended mid-event")) };
            }
            match response.chunk().await.map_err(|_|A2AError::internal("A2A subscription interrupted"))? {
                Some(chunk) => decoder.buffer.extend_from_slice(&chunk),
                None => eof = true,
            }
        }
    }).boxed())
}
