//! Registered operation-aware authorization extension. Ordinary friend/group/
//! tool outbound messages acquire no rights from AgentInvoke.
use async_trait::async_trait;
use bcs_service_api::application::agent_invoke::{
    AgentInvocationInput, AgentInvocationPermit, AgentInvocationService, AgentInvokeResult,
};
use bcs_service_api::interceptor::{InterceptorDecision, MessageInterceptor, OutboundMessage};
use std::sync::Arc;

pub struct AgentInvokeInterceptor {
    service: Arc<dyn AgentInvocationService>,
}

impl AgentInvokeInterceptor {
    pub fn new(service: Arc<dyn AgentInvocationService>) -> Self {
        Self { service }
    }
}

#[async_trait]
impl MessageInterceptor for AgentInvokeInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Pass
    }

    async fn on_agent_invocation(
        &self,
        input: &AgentInvocationInput,
    ) -> Option<AgentInvokeResult<AgentInvocationPermit>> {
        Some(self.service.authorize_invocation(input.clone()).await)
    }
}
