//! Provider 2.0 sends one SSE request. JSON ACK is uncertainty, never callback fallback.
use async_trait::async_trait;
use bcn_runtime_model::*;
use bcn_runtime_api::port::{AgentConnector,AgentEventStream};
use bcs_network_tracing::{ClientWithDefaults,RequestBuilderExt};
use bcs_protocol::http::{ProviderWebhookRequest,ProviderWebhookBotRef,ProviderWebhookSender,ProviderAbortResponse,
    BCN_PROTOCOL_VERSION_HEADER,BCN_TRANSPORT_HEADER,BCN_MESSAGE_ID_HEADER,BCN_TIMESTAMP_HEADER};
use crate::errors::*;
use std::time::Duration;

pub struct ProviderConfig {
    pub endpoint:String,
    pub bot:ProviderWebhookBotRef,
    pub sender:Option<ProviderWebhookSender>,
    pub group_id:String,
    /// Header and each read deadline. Runtime owns the overall task deadline.
    pub timeout_ms:u64,
}
pub struct ProviderConnector {binding:FrozenTargetBinding,config:ProviderConfig,http:ClientWithDefaults<reqwest::Client>}
impl ProviderConnector {
    /// Supply a policy-checked client (including redirect policy) and credentials as defaults.
    /// No callback receiver, task query, input continuation or idempotent resend is implied.
    pub fn new(binding:FrozenTargetBinding,config:ProviderConfig,http:ClientWithDefaults<reqwest::Client>)->ConnectorResult<Self> {
        let c=&binding.capabilities;
        if binding.target.scope.validate().is_err() || binding.binding_id.is_empty() || binding.binding_version.is_empty()
            || config.timeout_ms==0 || config.bot.provider_id.is_empty() || config.bot.provider_bot_ref.is_empty()
            || !c.streaming || c.query || c.subscribe || c.input_continuation || c.idempotency!=IdempotencySupport::Unsupported
            || c.cancellation==CancellationSupport::SingleTask {
            return Err(not_sent("unsupported Provider configuration"));
        }
        let url=reqwest::Url::parse(&config.endpoint).map_err(|_|not_sent("invalid Provider endpoint"))?;
        if !matches!(url.scheme(),"http"|"https") || !url.username().is_empty() || url.password().is_some() || url.fragment().is_some() {
            return Err(not_sent("invalid Provider endpoint"));
        }
        Ok(Self{binding,config,http})
    }
    fn envelope(&self,id:String,method:&str,context:String,message:Option<serde_json::Value>)->ProviderWebhookRequest {
        ProviderWebhookRequest{frame_type:"req".into(),id,method:method.into(),params:None,session_id:context,
            bcn_group_id:self.config.group_id.clone(),to_bot:self.config.bot.clone(),from:self.config.sender.clone(),
            message,attachments:vec![],before:None,after:None,limit:None,timeout_ms:self.config.timeout_ms,extensions:None}
    }
    async fn post(&self,body:&ProviderWebhookRequest,sse:bool)->ConnectorResult<reqwest::Response> {
        let request=self.http.request(|http| {
            let request=http.post(&self.config.endpoint).header(BCN_PROTOCOL_VERSION_HEADER,"2.0")
                .header(BCN_MESSAGE_ID_HEADER,&body.id).header(BCN_TIMESTAMP_HEADER,bcs_protocol::now_ms().to_string())
                .header(reqwest::header::ACCEPT,if sse {"text/event-stream"} else {"application/json"});
            let request=if sse {request.header(BCN_TRANSPORT_HEADER,"sse")} else {request};
            request.json(body)
        });
        tokio::time::timeout(Duration::from_millis(self.config.timeout_ms),request.send_traced()).await
            .map_err(|_|uncertain())?.map_err(|_|uncertain())
    }
}
#[async_trait]
impl AgentConnector for ProviderConnector {
    fn capabilities(&self)->ConnectorCapabilities{self.binding.capabilities.clone()}
    async fn send_streaming_message(&self,request:AgentSendRequest)->ConnectorResult<AgentEventStream> {
        validate_request(&self.binding,&request)?;
        if request.handle.as_ref().is_some_and(|h|h.remote_task_id.is_some()) {
            return Err(ConnectorError::Unsupported("Provider task continuation".into()));
        }
        let context=match request.handle.as_ref().and_then(|h|h.remote_context_id.clone()) {
            Some(context)=>context,
            None=>serde_json::to_string(request.message.context_id.as_ref().ok_or_else(||not_sent("Provider context required"))?)
                .map_err(|_|not_sent("invalid context identity"))?,
        };
        let mut text=String::new();
        for part in &request.message.parts {match part {MessagePart::Text{text:t}=>text.push_str(t),_=>return Err(ConnectorError::Unsupported("non-text input".into()))}}
        if !fits(&request.message.parts) {return Err(not_sent("input exceeds output budget"));}
        let id=serde_json::to_string(&request.dispatch_id).map_err(|_|not_sent("invalid dispatch identity"))?;
        let body=self.envelope(id,"chat.send",context.clone(),Some(text.into()));
        let response=self.post(&body,true).await?;
        let mime=response.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v|v.to_str().ok()).and_then(|v|v.split(';').next()).map(str::trim);
        if !response.status().is_success() || !mime.is_some_and(|m|m.eq_ignore_ascii_case("text/event-stream")) {
            // ACK can mean accepted side effects. No second POST and no callback registration.
            return Err(uncertain());
        }
        Ok(crate::provider_stream::stream(self.binding.clone(),context,response,self.config.timeout_ms))
    }
    async fn get_task(&self,handle:ExecutionHandle)->ConnectorResult<ExecutionSnapshot> {
        validate_handle(&self.binding,&handle)?;Err(ConnectorError::Unsupported("Provider query".into()))
    }
    async fn subscribe_to_task(&self,handle:ExecutionHandle)->ConnectorResult<AgentEventStream> {
        validate_handle(&self.binding,&handle)?;Err(ConnectorError::Unsupported("Provider subscribe".into()))
    }
    async fn cancel_task(&self,request:AgentCancelRequest)->ConnectorResult<CancelOutcome> {
        validate_handle(&self.binding,&request.handle)?;
        if self.binding.capabilities.cancellation!=CancellationSupport::ExclusiveContext {return Err(ConnectorError::Unsupported("Provider cancellation".into()));}
        if !request.exclusive_context || request.operation_id.scope!=self.binding.target.scope {return Err(not_sent("exclusive context required"));}
        let context=request.handle.remote_context_id.ok_or_else(||not_sent("remote context required"))?;
        let expected=request.handle.remote_task_id.ok_or_else(||not_sent("observed remote run required"))?;
        let id=serde_json::to_string(&request.operation_id).map_err(|_|not_sent("invalid operation identity"))?;
        let body=self.envelope(id,"chat.abort",context,None);
        let response=match self.post(&body,false).await {Ok(r)=>r,Err(_)=>return Ok(CancelOutcome::Unknown)};
        if !response.status().is_success() {return Ok(CancelOutcome::Unknown);}
        let bytes=match crate::provider_stream::body(response,self.config.timeout_ms).await {Ok(b)=>b,Err(_)=>return Ok(CancelOutcome::Unknown)};
        let response:ProviderAbortResponse=match serde_json::from_slice(&bytes) {Ok(r)=>r,Err(_)=>return Ok(CancelOutcome::Unknown)};
        Ok(if response.ok && response.aborted && response.aborted_run_ids.contains(&expected)
            && response.aborted_run_ids.iter().all(|id|!id.is_empty()) {
            CancelOutcome::Confirmed{affected_remote_task_ids:response.aborted_run_ids}
        } else {CancelOutcome::Unknown})
    }
}
