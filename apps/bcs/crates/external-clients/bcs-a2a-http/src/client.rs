//! REST binding using the pinned SDK models and bounded BCS wire decoders.
//!
//! The composition layer must supply an HTTP client whose DNS, redirects and
//! credentials obey the BCS outbound policy, and a previously validated Card
//! interface. No automatic Card discovery or transport fallback occurs here.

use bcs_network_tracing::ClientWithDefaults;

pub struct RestClient {
    http: ClientWithDefaults<reqwest::Client>,
    base_url: String,
    tenant: Option<String>,
}

impl RestClient {
    /// Async continuation shares the bounded ProtoJSON error decoder too.
    pub async fn send_message(&self, request:&a2a::SendMessageRequest)->Result<a2a::SendMessageResponse,a2a::A2AError> {
        let mut request=request.clone();
        if let Some(tenant)=&self.tenant {request.tenant=Some(tenant.clone());}
        let url=format!("{}/message:send",self.base_url.trim_end_matches('/'));
        crate::client_json::json_response(self.http.request(|http| http.post(url).json(&request))).await
    }

    /// Share the bounded REST/SSE decoder with subscriptions. The pinned SDK's
    /// initial-stream error decoder discards ProtoJSON Status/ErrorInfo details.
    pub async fn send_streaming_message(&self, request: &a2a::SendMessageRequest)
        -> Result<futures::stream::BoxStream<'static, Result<a2a::StreamResponse, a2a::A2AError>>, a2a::A2AError> {
        let mut request = request.clone();
        if let Some(tenant) = &self.tenant { request.tenant = Some(tenant.clone()); }
        let url = format!("{}/message:stream", self.base_url.trim_end_matches('/'));
        crate::subscribe::post_stream(&self.http, &url, &request).await
    }

    /// Keep opaque IDs inside one path segment. The pinned SDK concatenates
    /// raw IDs, which can escape a Bot route when they contain slashes.
    pub async fn get_task(&self, request: &a2a::GetTaskRequest) -> Result<a2a::Task, a2a::A2AError> {
        let url = crate::client_json::task_url(&self.base_url, &request.id, "")?;
        let mut query = Vec::new();
        if let Some(length) = request.history_length { query.push(("historyLength", length.to_string())); }
        if let Some(tenant) = self.tenant.as_ref().or(request.tenant.as_ref()) { query.push(("tenant", tenant.clone())); }
        crate::client_json::task_response(self.http.request(|http| http.get(url).query(&query))).await
    }

    pub async fn cancel_task(&self, request: &a2a::CancelTaskRequest) -> Result<a2a::Task, a2a::A2AError> {
        let url = crate::client_json::task_url(&self.base_url, &request.id, ":cancel")?;
        let mut request = request.clone();
        if let Some(tenant) = &self.tenant { request.tenant = Some(tenant.clone()); }
        crate::client_json::task_response(self.http.request(|http| http.post(url).json(&request))).await
    }

    /// Rust SDK 0.2.9 uses GET plus legacy fallback. The pinned 1.0.1
    /// binding requires POST. All request/event models remain SDK types.
    pub async fn subscribe_to_task(&self, request: &a2a::SubscribeToTaskRequest)
        -> Result<futures::stream::BoxStream<'static, Result<a2a::StreamResponse, a2a::A2AError>>, a2a::A2AError> {
        let mut request = request.clone();
        if let Some(tenant) = &self.tenant { request.tenant = Some(tenant.clone()); }
        let url = crate::client_json::task_url(&self.base_url, &request.id, ":subscribe")?;
        crate::subscribe::post_stream(&self.http, &url, &request).await
    }
}

/// Bind a validated interface without starting a network request.
/// Pass custom defaults via `ClientWithDefaults`, not the raw reqwest builder.
/// The selected interface tenant, when present, overrides request tenant values.
pub fn bind_rest_client(
    http: impl Into<ClientWithDefaults<reqwest::Client>>,
    selected_interface_url: String,
    selected_tenant: Option<String>,
) -> RestClient {
    RestClient { http: http.into(), base_url: selected_interface_url, tenant: selected_tenant }
}
