//! Standard A2A gRPC binding. Composition supplies the policy-checked channel,
//! including MOSN routing; this binding never discovers or dials a peer itself.
use a2a::{A2AError, TypedDetail};
use a2a_pb::{pbconv, proto::a2a_service_client::A2aServiceClient};
use futures::{stream::BoxStream, StreamExt};
use prost::Message;
use tonic::{metadata::MetadataMap, transport::Channel, Request, Status};

#[derive(Clone)]
pub struct GrpcClient {
    sdk: A2aServiceClient<Channel>,
    headers: MetadataMap,
    tenant: Option<String>,
    timeout: std::time::Duration,
}

impl GrpcClient {
    /// No endpoint fallback or automatic retry. Task lifecycle owns retry policy.
    pub fn new(channel: Channel, headers: MetadataMap, tenant: Option<String>) -> Self {
        Self { sdk: A2aServiceClient::new(channel)
            .max_decoding_message_size(8 * 1024 * 1024)
            .max_encoding_message_size(8 * 1024 * 1024), headers, tenant, timeout:std::time::Duration::from_secs(120) }
    }

    pub fn with_timeout(mut self, timeout: std::time::Duration) -> Self {self.timeout=timeout;self}

    fn request<T>(&self, body: T, method: &'static str) -> Request<T> {
        let mut request = Request::new(body);
        *request.metadata_mut() = self.headers.clone();
        request.metadata_mut().insert("a2a-version", tonic::metadata::MetadataValue::from_static("1.0"));
        request.metadata_mut().insert("sofa_head_method_name", tonic::metadata::MetadataValue::from_static(method));
        request.set_timeout(self.timeout);
        request
    }

    pub async fn send_message(&self, request: &a2a::SendMessageRequest)
        -> Result<a2a::SendMessageResponse, A2AError> {
        let mut body = pbconv::to_proto_send_message_request(request);
        if let Some(tenant) = &self.tenant { body.tenant = tenant.clone(); }
        let response = self.sdk.clone().send_message(self.request(body,"SendMessage")).await.map_err(grpc_error)?.into_inner();
        pbconv::from_proto_send_message_response(&response)
            .ok_or_else(|| A2AError::parse_error("empty A2A gRPC submission response"))
    }

    pub async fn send_streaming_message(&self, request: &a2a::SendMessageRequest)
        -> Result<BoxStream<'static, Result<a2a::StreamResponse, A2AError>>, A2AError> {
        let mut body = pbconv::to_proto_send_message_request(request);
        if let Some(tenant) = &self.tenant { body.tenant = tenant.clone(); }
        let response = self.sdk.clone().send_streaming_message(self.request(body,"SendStreamingMessage")).await.map_err(grpc_error)?;
        Ok(events(response.into_inner()))
    }

    pub async fn get_task(&self, request: &a2a::GetTaskRequest) -> Result<a2a::Task, A2AError> {
        let mut body = pbconv::to_proto_get_task_request(request);
        if let Some(tenant) = &self.tenant { body.tenant = tenant.clone(); }
        let response = self.sdk.clone().get_task(self.request(body,"GetTask")).await.map_err(grpc_error)?.into_inner();
        Ok(pbconv::from_proto_task(&response))
    }

    pub async fn cancel_task(&self, request: &a2a::CancelTaskRequest) -> Result<a2a::Task, A2AError> {
        let mut body = pbconv::to_proto_cancel_task_request(request);
        if let Some(tenant) = &self.tenant { body.tenant = tenant.clone(); }
        let response = self.sdk.clone().cancel_task(self.request(body,"CancelTask")).await.map_err(grpc_error)?.into_inner();
        Ok(pbconv::from_proto_task(&response))
    }

    pub async fn subscribe_to_task(&self, request: &a2a::SubscribeToTaskRequest)
        -> Result<BoxStream<'static, Result<a2a::StreamResponse, A2AError>>, A2AError> {
        let mut body = pbconv::to_proto_subscribe_to_task_request(request);
        if let Some(tenant) = &self.tenant { body.tenant = tenant.clone(); }
        let response = self.sdk.clone().subscribe_to_task(self.request(body,"SubscribeToTask")).await.map_err(grpc_error)?;
        Ok(events(response.into_inner()))
    }
}

fn events(stream: tonic::Streaming<a2a_pb::proto::StreamResponse>)
    -> BoxStream<'static, Result<a2a::StreamResponse, A2AError>> {
    stream.map(|event| {
        let event = event.map_err(grpc_error)?;
        pbconv::from_proto_stream_response(&event)
            .ok_or_else(|| A2AError::parse_error("empty A2A gRPC stream event"))
    }).boxed()
}

// Wire-compatible google.rpc.Status/ErrorInfo subset. Do not forward peer
// messages, domains or metadata, which may contain credentials or business data.
#[derive(Clone, PartialEq, Message)]
struct RpcStatus {
    #[prost(int32, tag="1")] code: i32,
    #[prost(string, tag="2")] message: String,
    #[prost(message, repeated, tag="3")] details: Vec<prost_types::Any>,
}
#[derive(Clone, PartialEq, Message)]
struct ErrorInfo {
    #[prost(string, tag="1")] reason: String,
}

fn grpc_error(status: Status) -> A2AError {
    let details: Vec<TypedDetail> = if status.details().len() <= 64 * 1024 {
        RpcStatus::decode(status.details()).ok().into_iter().flat_map(|status| status.details)
            .filter(|detail| detail.type_url == "type.googleapis.com/google.rpc.ErrorInfo")
            .filter_map(|detail| ErrorInfo::decode(detail.value.as_slice()).ok())
            .filter_map(|info| a2a::reason_to_error_code(&info.reason))
            .take(16).map(|code| TypedDetail::error_info(a2a::error_reason(code),
                a2a::errordetails::PROTOCOL_DOMAIN, None)).collect()
    } else { Vec::new() };
    let code = details.first().and_then(|detail| detail.value.get("reason"))
        .and_then(serde_json::Value::as_str).and_then(a2a::reason_to_error_code)
        .unwrap_or(a2a::error_code::INTERNAL_ERROR);
    A2AError::new(code, format!("A2A gRPC {}", status.code() as i32)).with_details(details)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_reason_survives_without_peer_diagnostics() {
        let detail = ErrorInfo { reason: "TASK_NOT_CANCELABLE".into() };
        let wire = RpcStatus { code: 9, message: "private diagnostic".into(),
            details: vec![prost_types::Any {
                type_url: "type.googleapis.com/google.rpc.ErrorInfo".into(),
                value: detail.encode_to_vec(),
            }] }.encode_to_vec();
        let error = grpc_error(Status::with_details(tonic::Code::FailedPrecondition,
            "private diagnostic", wire.into()));
        assert_eq!(error.code, a2a::error_code::TASK_NOT_CANCELABLE);
        assert_eq!(error.message, "A2A gRPC 9");
        assert!(!serde_json::to_string(&error.details).unwrap().contains("private diagnostic"));
    }

    #[test]
    fn status_alone_does_not_invent_task_not_found() {
        let error = grpc_error(Status::not_found("private task identifier"));
        assert_eq!(error.code, a2a::error_code::INTERNAL_ERROR);
        assert_eq!(error.message, "A2A gRPC 5");
    }

    #[test]
    fn oversized_or_malformed_details_are_not_forwarded() {
        for bytes in [vec![255], vec![0; 65537]] {
            let error = grpc_error(Status::with_details(tonic::Code::Unavailable,
                "private diagnostic", bytes.into()));
            assert_eq!(error.code, a2a::error_code::INTERNAL_ERROR);
            assert!(error.details.unwrap().is_empty());
        }
    }

    #[tokio::test]
    async fn requests_keep_auth_metadata_and_pin_wire_version() {
        let channel = tonic::transport::Endpoint::from_static("http://127.0.0.1:1").connect_lazy();
        let mut headers = MetadataMap::new();
        headers.insert("a2a-version", "0.3".parse().unwrap());
        headers.insert("authorization", "Bearer test-fixture".parse().unwrap());
        let client = GrpcClient::new(channel, headers, Some("bot".into()));
        let request = client.request((),"GetTask");
        assert_eq!(request.metadata().get("a2a-version").unwrap(), "1.0");
        assert_eq!(request.metadata().get("authorization").unwrap(), "Bearer test-fixture");
        assert_eq!(request.metadata().get("sofa_head_method_name").unwrap(), "GetTask");
        assert!(request.metadata().get("grpc-timeout").is_some());
    }
}
