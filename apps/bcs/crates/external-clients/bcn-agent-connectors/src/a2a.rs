//! A frozen Card selects exactly one existing HTTP wire client, with no retry or discovery.
use async_trait::async_trait;
use bcn_runtime_api::port::{AgentConnector, AgentEventStream};
use bcn_runtime_model::*;
use bcs_a2a_http::{card::SelectedCard, client::{RestClient, bind_rest_client}, jsonrpc::JsonRpcClient};
use bcs_network_tracing::ClientWithDefaults;
use crate::{errors::*, projection};

enum Client { Rest(RestClient), Rpc(JsonRpcClient) }
macro_rules! call {
    ($self:expr, $method:ident, $request:expr) => {
        match &$self.client { Client::Rest(c)=>c.$method($request).await, Client::Rpc(c)=>c.$method($request).await }
    }
}
pub struct A2aConnector { binding:FrozenTargetBinding, client:Client }
impl A2aConnector {
    /// Card selection/URL policy and credential resolution are caller responsibilities.
    /// This connector currently negotiates text only, with no idempotent replay guarantee.
    pub fn new(binding:FrozenTargetBinding, card:SelectedCard, http:ClientWithDefaults<reqwest::Client>) -> ConnectorResult<Self> {
        let caps=&binding.capabilities;
        if binding.target.scope.validate().is_err() || binding.binding_version.is_empty() || binding.binding_id.is_empty()
            || ["defaultInputModes","defaultOutputModes"].iter().any(|key|
                !card.raw.get(*key).and_then(serde_json::Value::as_array).is_some_and(|modes|modes.iter().any(|m|m=="text/plain")))
            || caps.idempotency != IdempotencySupport::Unsupported
            || caps.cancellation == CancellationSupport::ExclusiveContext
            || ((caps.streaming || caps.subscribe) && card.raw.pointer("/capabilities/streaming").and_then(serde_json::Value::as_bool)!=Some(true))
            || card.raw.pointer("/capabilities/extensions").and_then(serde_json::Value::as_array)
                .is_some_and(|exts|exts.iter().any(|e|e["required"]==true)) {
            return Err(not_sent("unsupported frozen Card capabilities"));
        }
        let protocol=serde_json::to_value(&card.interface.protocol_binding).map_err(|_|not_sent("invalid binding"))?;
        let client=match protocol.as_str() {
            Some("HTTP+JSON")=>Client::Rest(bind_rest_client(http,card.interface.url,card.interface.tenant)),
            Some("JSONRPC")=>Client::Rpc(JsonRpcClient::new(http,card.interface.url,card.interface.tenant)),
            _=>return Err(ConnectorError::Unsupported("A2A binding".into())),
        };
        Ok(Self{binding,client})
    }
    fn wire(&self, request:&AgentSendRequest) -> ConnectorResult<a2a::SendMessageRequest> {
        validate_request(&self.binding,request)?;
        if request.handle.as_ref().is_some_and(|h|h.remote_task_id.is_some()) && !self.binding.capabilities.input_continuation {
            return Err(ConnectorError::Unsupported("input continuation".into()));
        }
        let mut parts=Vec::new();
        for part in &request.message.parts {
            match part { MessagePart::Text{text}=>parts.push(a2a::Part::text(text)), _=>return Err(ConnectorError::Unsupported("non-text input".into())) }
        }
        if !fits(&request.message.parts) { return Err(not_sent("input exceeds output budget")); }
        // Scoped message identity is stable across retries; it is not a replay authorization.
        let message_id=serde_json::to_string(&request.message.message_id).map_err(|_|not_sent("invalid message identity"))?;
        Ok(a2a::SendMessageRequest {
            message:a2a::Message {message_id, role:a2a::Role::User,parts,
                context_id:request.handle.as_ref().and_then(|h|h.remote_context_id.clone()),
                task_id:request.handle.as_ref().and_then(|h|h.remote_task_id.clone()),
                metadata:None,extensions:None,reference_task_ids:None},
            configuration:Some(a2a::SendMessageConfiguration {accepted_output_modes:Some(vec!["text/plain".into()]),
                task_push_notification_config:None,history_length:Some(0),return_immediately:Some(true)}),
            metadata:None,tenant:None,
        })
    }
    fn task_id(&self, handle:&ExecutionHandle) -> ConnectorResult<String> {
        validate_handle(&self.binding,handle)?;
        handle.remote_task_id.clone().ok_or_else(||not_sent("remote task identity required"))
    }
}
#[async_trait]
impl AgentConnector for A2aConnector {
    fn capabilities(&self) -> ConnectorCapabilities { self.binding.capabilities.clone() }
    async fn send_message(&self, request:AgentSendRequest) -> ConnectorResult<AgentSendResponse> {
        let wire=self.wire(&request)?;
        let response=call!(self,send_message,&wire).map_err(|_|uncertain())?;
        match response {
            a2a::SendMessageResponse::Message(message)=>projection::message(&self.binding,message,request.handle.as_ref()),
            a2a::SendMessageResponse::Task(task)=>Ok(AgentSendResponse::Task{snapshot:projection::snapshot(&self.binding,task,request.handle.as_ref())?}),
        }
    }
    async fn send_streaming_message(&self, request:AgentSendRequest) -> ConnectorResult<AgentEventStream> {
        let wire=self.wire(&request)?;
        if !self.binding.capabilities.streaming { return Err(ConnectorError::Unsupported("streaming".into())); }
        let stream=call!(self,send_streaming_message,&wire).map_err(|_|uncertain())?;
        Ok(projection::stream(self.binding.clone(),request.handle,stream))
    }
    async fn get_task(&self, handle:ExecutionHandle) -> ConnectorResult<ExecutionSnapshot> {
        let id=self.task_id(&handle)?;
        if !self.binding.capabilities.query { return Err(ConnectorError::Unsupported("query".into())); }
        let wire=a2a::GetTaskRequest{id,history_length:Some(0),tenant:None};
        let task=call!(self,get_task,&wire).map_err(|_|uncertain())?;
        projection::snapshot(&self.binding,task,Some(&handle))
    }
    async fn subscribe_to_task(&self, handle:ExecutionHandle) -> ConnectorResult<AgentEventStream> {
        let id=self.task_id(&handle)?;
        if !self.binding.capabilities.subscribe { return Err(ConnectorError::Unsupported("subscribe".into())); }
        let wire=a2a::SubscribeToTaskRequest{id,tenant:None};
        let stream=call!(self,subscribe_to_task,&wire).map_err(|_|uncertain())?;
        Ok(projection::stream(self.binding.clone(),Some(handle),stream))
    }
    async fn cancel_task(&self, request:AgentCancelRequest) -> ConnectorResult<CancelOutcome> {
        let id=self.task_id(&request.handle)?;
        if request.operation_id.scope != self.binding.target.scope { return Err(not_sent("cancel scope mismatch")); }
        if self.binding.capabilities.cancellation != CancellationSupport::SingleTask { return Err(ConnectorError::Unsupported("cancel".into())); }
        let wire=a2a::CancelTaskRequest{id:id.clone(),tenant:None,metadata:None};
        let task=match call!(self,cancel_task,&wire) { Ok(task)=>task,Err(_)=>return Ok(CancelOutcome::Unknown) };
        let snapshot=match projection::snapshot(&self.binding,task,Some(&request.handle)) {Ok(s)=>s,Err(_)=>return Ok(CancelOutcome::Unknown)};
        Ok(match snapshot.status {
            TaskStatus::Cancelled=>CancelOutcome::Confirmed{affected_remote_task_ids:vec![id]},
            TaskStatus::Completed|TaskStatus::Failed=>CancelOutcome::AlreadyTerminal,
            _=>CancelOutcome::Unknown,
        })
    }
}
