//! Both bindings feed the same Task driver, projection and interruption lifecycle.
use crate::{client::RestClient, grpc_client::GrpcClient};
use a2a::A2AError;
use futures::stream::BoxStream;

pub(super) enum BindingClient { Rest(RestClient), Grpc(GrpcClient) }
impl BindingClient {
    pub async fn send_message(&self, request:&a2a::SendMessageRequest)->Result<a2a::SendMessageResponse,A2AError> {
        match self {Self::Rest(client)=>client.send_message(request).await,Self::Grpc(client)=>client.send_message(request).await}
    }
    pub async fn send_streaming_message(&self, request:&a2a::SendMessageRequest)
        ->Result<BoxStream<'static,Result<a2a::StreamResponse,A2AError>>,A2AError> {
        match self {Self::Rest(client)=>client.send_streaming_message(request).await,Self::Grpc(client)=>client.send_streaming_message(request).await}
    }
    pub async fn get_task(&self, request:&a2a::GetTaskRequest)->Result<a2a::Task,A2AError> {
        match self {Self::Rest(client)=>client.get_task(request).await,Self::Grpc(client)=>client.get_task(request).await}
    }
    pub async fn cancel_task(&self, request:&a2a::CancelTaskRequest)->Result<a2a::Task,A2AError> {
        match self {Self::Rest(client)=>client.cancel_task(request).await,Self::Grpc(client)=>client.cancel_task(request).await}
    }
    pub async fn subscribe_to_task(&self, request:&a2a::SubscribeToTaskRequest)
        ->Result<BoxStream<'static,Result<a2a::StreamResponse,A2AError>>,A2AError> {
        match self {Self::Rest(client)=>client.subscribe_to_task(request).await,Self::Grpc(client)=>client.subscribe_to_task(request).await}
    }
}
