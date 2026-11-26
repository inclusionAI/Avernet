//! A2A 1.0 JSON-RPC binding. No legacy method aliases or binding retries.
use a2a::A2AError;
use bcs_network_tracing::ClientWithDefaults;
use crate::digest::{
    A2aCallContext, DigestGuard, DigestStart, InvokeType, Protocol, RESULT_SERVER_OR_PARSE_ERROR,
    RESULT_TIMEOUT, classify_http_status, classify_jsonrpc_rpc_error, classify_reqwest_error,
    classify_rest_http_error,
};
use crate::http_client::HttpClient;
use futures::stream::BoxStream;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::sync::atomic::{AtomicU64, Ordering};

static REQUEST_ID: AtomicU64 = AtomicU64::new(1);
pub struct JsonRpcClient {
    http: HttpClient,
    url: String,
    tenant: Option<String>,
    context: A2aCallContext,
}

pub(super) enum DecodeError {
    Protocol { error: A2AError, rpc_code: i32 },
    InvalidEnvelope(A2AError),
}

pub(super) fn decode_classified(value: Value, id: &str) -> Result<Value, DecodeError> {
    if value.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
        || value.get("id").and_then(Value::as_str) != Some(id)
        || value.get("result").is_some() == value.get("error").is_some()
    {
        return Err(DecodeError::InvalidEnvelope(A2AError::invalid_agent_response()));
    }
    if let Some(error) = value.get("error") {
        let code = error
            .get("code")
            .and_then(Value::as_i64)
            .and_then(|v| i32::try_from(v).ok())
            .ok_or_else(|| DecodeError::InvalidEnvelope(A2AError::invalid_agent_response()))?;
        if !error.get("message").is_some_and(Value::is_string) {
            return Err(DecodeError::InvalidEnvelope(A2AError::invalid_agent_response()));
        }
        let details = match error.get("data") {
            None => vec![],
            Some(data) => serde_json::from_value(data.clone())
                .map_err(|_| DecodeError::InvalidEnvelope(A2AError::invalid_agent_response()))?,
        };
        // Do not expose untrusted remote messages in diagnostics.
        return Err(DecodeError::Protocol {
            error: A2AError::new(code, "A2A JSON-RPC failure").with_details(details),
            rpc_code: code,
        });
    }
    Ok(value["result"].clone())
}

impl JsonRpcClient {
    /// Supply custom defaults via `ClientWithDefaults`, before trace propagation.
    pub fn new(http: impl Into<ClientWithDefaults<reqwest::Client>>, url: String, tenant: Option<String>) -> Self {
        Self::with_http(HttpClient::new(http.into()), url, tenant)
    }
    pub(super) fn with_http(http: HttpClient, url: String, tenant: Option<String>) -> Self {
        Self { http, url, tenant, context: A2aCallContext::default() }
    }
    pub fn with_call_context(mut self, context: A2aCallContext) -> Self {
        self.context = context;
        self
    }
    fn request<T: Serialize>(&self, method: &str, params: &T) -> Result<(String, Vec<u8>), A2AError> {
        let id = REQUEST_ID.fetch_add(1, Ordering::Relaxed).to_string();
        let mut params = serde_json::to_value(params)
            .map_err(|_| A2AError::invalid_params("invalid parameters"))?;
        if let Some(tenant) = &self.tenant {
            params["tenant"] = json!(tenant);
        }
        let payload = json!({"jsonrpc":"2.0", "id":id, "method":method, "params":params});
        let body = serde_json::to_vec(&payload)
            .map_err(|_| A2AError::invalid_params("invalid parameters"))?;
        Ok((id, body))
    }
    fn start_digest(&self, invoke_type: InvokeType, method: &'static str, req_bytes: usize) -> DigestGuard {
        DigestGuard::start(DigestStart {
            protocol: Protocol::JsonRpc,
            invoke_type,
            method,
            url: &self.url,
            canonical_path: None,
            tenant: self.tenant.as_deref(),
            context: &self.context,
            req_bytes,
        })
    }
    async fn call<T: Serialize, R: DeserializeOwned>(
        &self,
        method: &'static str,
        params: &T,
    ) -> Result<R, A2AError> {
        let (id, req_body) = match self.request(method, params) {
            Ok(pair) => pair,
            Err(err) => {
                let mut guard = self.start_digest(InvokeType::Sync, method, 0);
                guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "INVALID_PARAMS");
                return Err(err);
            }
        };
        let mut guard = self.start_digest(InvokeType::Sync, method, req_body.len());
        match tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let request = self.http.transport.request(|http| {
                http.post(&self.url)
                    .header("A2A-Version", "1.0")
                    .header(reqwest::header::CONTENT_TYPE, "application/json")
                    .body(req_body)
            });
            let mut response = self.http.send_observed(request, "A2A JSON-RPC request interrupted", &mut guard).await?;
            let status = response.status();
            guard.record_headers(status.as_u16());
            let mut body = Vec::new();
            loop {
                match response.chunk().await {
                    Ok(Some(chunk)) => {
                        guard.add_resp_bytes(chunk.len());
                        if body.len().saturating_add(chunk.len()) > 8 * 1024 * 1024 {
                            guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "SIZE_LIMIT");
                            return Err(A2AError::invalid_agent_response());
                        }
                        body.extend_from_slice(&chunk);
                    }
                    Ok(None) => break,
                    Err(err) => {
                        let (rc, ec) = classify_reqwest_error(&err);
                        guard.finish_err(rc, ec);
                        return Err(A2AError::internal("A2A JSON-RPC response incomplete"));
                    }
                }
            }
            let envelope: Value = match serde_json::from_slice(&body) {
                Ok(envelope) => envelope,
                Err(_) => {
                    if !status.is_success() {
                        guard.finish_err(classify_http_status(status.as_u16()), &format!("HTTP_{}", status.as_u16()));
                    } else {
                        guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "PARSE_ERROR");
                    }
                    return Err(A2AError::invalid_agent_response());
                }
            };
            // Preserve protocol errors even when the server also uses an HTTP
            // failure status. Never accept a successful result on that status.
            let result = match decode_classified(envelope, &id) {
                Ok(result) => result,
                Err(DecodeError::Protocol { error, rpc_code }) => {
                    let (rc, ec) = classify_jsonrpc_rpc_error(status.as_u16(), rpc_code);
                    guard.finish_err(rc, &ec);
                    return Err(error);
                }
                Err(DecodeError::InvalidEnvelope(error)) => {
                    if !status.is_success() {
                        guard.finish_err(classify_http_status(status.as_u16()), &format!("HTTP_{}", status.as_u16()));
                    } else {
                        guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "INVALID_ENVELOPE");
                    }
                    return Err(error);
                }
            };
            if !status.is_success() {
                let err = crate::client_json::http_error(status.as_u16(), &body);
                let (rc, ec) = classify_rest_http_error(status.as_u16(), &err);
                guard.finish_err(rc, &ec);
                return Err(err);
            }
            match serde_json::from_value(result) {
                Ok(parsed) => {
                    guard.finish_ok();
                    Ok(parsed)
                }
                Err(_) => {
                    guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "PARSE_ERROR");
                    Err(A2AError::invalid_agent_response())
                }
            }
        }).await {
            Ok(result) => result,
            Err(_) => {
                guard.finish_err(RESULT_TIMEOUT, "TIMEOUT");
                Err(A2AError::internal("A2A JSON-RPC request timed out"))
            }
        }
    }

    async fn stream<T: Serialize>(
        &self,
        method: &'static str,
        params: &T,
    ) -> Result<BoxStream<'static, Result<a2a::StreamResponse, A2AError>>, A2AError> {
        let (id, req_body) = match self.request(method, params) {
            Ok(pair) => pair,
            Err(err) => {
                let mut guard = self.start_digest(InvokeType::Stream, method, 0);
                guard.finish_err(RESULT_SERVER_OR_PARSE_ERROR, "INVALID_PARAMS");
                return Err(err);
            }
        };
        let guard = self.start_digest(InvokeType::Stream, method, req_body.len());
        crate::subscribe::post_stream_with_rpc(&self.http, &self.url, req_body, Some(id), guard).await
    }
    pub async fn send_message(
        &self,
        request: &a2a::SendMessageRequest,
    ) -> Result<a2a::SendMessageResponse, A2AError> {
        self.call("SendMessage", request).await
    }
    pub async fn send_streaming_message(
        &self,
        request: &a2a::SendMessageRequest,
    ) -> Result<BoxStream<'static, Result<a2a::StreamResponse, A2AError>>, A2AError> {
        self.stream("SendStreamingMessage", request).await
    }
    pub async fn get_task(&self, request: &a2a::GetTaskRequest) -> Result<a2a::Task, A2AError> {
        self.call("GetTask", request).await
    }
    pub async fn cancel_task(
        &self,
        request: &a2a::CancelTaskRequest,
    ) -> Result<a2a::Task, A2AError> {
        self.call("CancelTask", request).await
    }
    pub async fn subscribe_to_task(
        &self,
        request: &a2a::SubscribeToTaskRequest,
    ) -> Result<BoxStream<'static, Result<a2a::StreamResponse, A2AError>>, A2AError> {
        self.stream("SubscribeToTask", request).await
    }
}
