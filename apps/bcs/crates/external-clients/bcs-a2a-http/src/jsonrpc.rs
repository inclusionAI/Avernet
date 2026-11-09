//! A2A 1.0 JSON-RPC binding. No legacy method aliases or binding retries.
use a2a::A2AError;
use bcs_network_tracing::{ClientWithDefaults, RequestBuilderExt};
use futures::stream::BoxStream;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use std::sync::atomic::{AtomicU64, Ordering};

static REQUEST_ID: AtomicU64 = AtomicU64::new(1);
pub struct JsonRpcClient {
    http: ClientWithDefaults<reqwest::Client>,
    url: String,
    tenant: Option<String>,
}

pub(super) fn decode(value: Value, id: &str) -> Result<Value, A2AError> {
    if value.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
        || value.get("id").and_then(Value::as_str) != Some(id)
        || value.get("result").is_some() == value.get("error").is_some()
    {
        return Err(A2AError::invalid_agent_response());
    }
    if let Some(error) = value.get("error") {
        let code = error
            .get("code")
            .and_then(Value::as_i64)
            .and_then(|v| i32::try_from(v).ok())
            .ok_or_else(A2AError::invalid_agent_response)?;
        if !error.get("message").is_some_and(Value::is_string) {
            return Err(A2AError::invalid_agent_response());
        }
        let details = match error.get("data") {
            None => vec![],
            Some(data) => serde_json::from_value(data.clone())
                .map_err(|_| A2AError::invalid_agent_response())?,
        };
        // Do not expose untrusted remote messages in diagnostics.
        return Err(A2AError::new(code, "A2A JSON-RPC failure").with_details(details));
    }
    Ok(value["result"].clone())
}

impl JsonRpcClient {
    /// Supply custom defaults via `ClientWithDefaults`, before trace propagation.
    pub fn new(http: impl Into<ClientWithDefaults<reqwest::Client>>, url: String, tenant: Option<String>) -> Self {
        Self { http: http.into(), url, tenant }
    }
    fn request<T: Serialize>(&self, method: &str, params: &T) -> Result<(String, Value), A2AError> {
        let id = REQUEST_ID.fetch_add(1, Ordering::Relaxed).to_string();
        let mut params = serde_json::to_value(params)
            .map_err(|_| A2AError::invalid_params("invalid parameters"))?;
        if let Some(tenant) = &self.tenant {
            params["tenant"] = json!(tenant);
        }
        Ok((
            id.clone(),
            json!({"jsonrpc":"2.0", "id":id, "method":method, "params":params}),
        ))
    }
    async fn call<T: Serialize, R: DeserializeOwned>(
        &self,
        method: &str,
        params: &T,
    ) -> Result<R, A2AError> {
        let (id, request) = self.request(method, params)?;
        tokio::time::timeout(std::time::Duration::from_secs(15), async {
            let mut response = self.http.request(|http| http.post(&self.url).header("A2A-Version", "1.0")
                .json(&request)).send_traced().await
                .map_err(|_| A2AError::internal("A2A JSON-RPC request interrupted"))?;
            let status = response.status();
            let mut body = Vec::new();
            while let Some(chunk) = response.chunk().await
                .map_err(|_| A2AError::internal("A2A JSON-RPC response incomplete"))? {
                if body.len().saturating_add(chunk.len()) > 8 * 1024 * 1024 {
                    return Err(A2AError::invalid_agent_response());
                }
                body.extend_from_slice(&chunk);
            }
            let envelope: Value = serde_json::from_slice(&body)
                .map_err(|_| A2AError::invalid_agent_response())?;
            // Preserve protocol errors even when the server also uses an HTTP
            // failure status. Never accept a successful result on that status.
            let result = decode(envelope, &id)?;
            if !status.is_success() { return Err(crate::client_json::http_error(status.as_u16(), &body)); }
            serde_json::from_value(result).map_err(|_| A2AError::invalid_agent_response())
        }).await.map_err(|_| A2AError::internal("A2A JSON-RPC request timed out"))?
    }

    async fn stream<T: Serialize>(
        &self,
        method: &str,
        params: &T,
    ) -> Result<BoxStream<'static, Result<a2a::StreamResponse, A2AError>>, A2AError> {
        let (id, request) = self.request(method, params)?;
        crate::subscribe::post_stream_with_rpc(&self.http, &self.url, &request, Some(id)).await
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
