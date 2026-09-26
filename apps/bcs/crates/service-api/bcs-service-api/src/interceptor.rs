//! Outbound message interceptor extension point.
//!
//! Multiple cross-cutting concerns can inspect or modify outbound delivery
//! without being hard-wired into the dispatcher.

use async_trait::async_trait;
use bcs_domain::{AgentCredentials, GroupMessage};

use crate::port::{DeliveryBlockContext, DeliveryPolicyBlockInstrumentationHook};

use std::sync::Arc;

/// Decision from a single interceptor.
pub enum InterceptorDecision {
    /// Continue to the next interceptor.
    Pass,

    /// The message was modified in place; continue to the next interceptor.
    Modify,

    /// Block delivery; the chain stops and returns the reason.
    Block(BlockReason),
}

#[derive(Debug, Clone)]
pub struct BlockReason {
    /// Identifier of the interceptor that blocked.
    pub interceptor_id: String,

    /// Machine-readable code, for example "rate_limit" or "content_unsafe".
    pub code: String,

    /// Human-readable explanation.
    pub message: String,

    /// Whether the reason is safe to expose to the caller.
    pub user_visible: bool,
}

/// Outbound message structure passed through the chain.
#[derive(Debug, Clone)]
pub struct OutboundMessage {
    pub group_id: String,
    pub message: GroupMessage,
    pub receiver_bot_id: String,
    pub caller: AgentCredentials,
    pub receiver: AgentCredentials,
}

#[async_trait]
pub trait MessageInterceptor: Send + Sync {
    /// Inspect or modify an outbound message before delivery.
    async fn on_outbound(&self, msg: &mut OutboundMessage) -> InterceptorDecision;
}

#[derive(Debug, Default)]
struct NoopDeliveryPolicyBlockInstrumentationHook;

#[async_trait]
impl DeliveryPolicyBlockInstrumentationHook for NoopDeliveryPolicyBlockInstrumentationHook {
    async fn blocked(&self, _context: DeliveryBlockContext) {}
}

/// Ordered chain for outbound message interceptors.
pub struct InterceptorChain {
    interceptors: Vec<Arc<dyn MessageInterceptor>>,
    block_hook: Arc<dyn DeliveryPolicyBlockInstrumentationHook>,
}

impl Default for InterceptorChain {
    fn default() -> Self {
        Self {
            interceptors: Vec::new(),
            block_hook: Arc::new(NoopDeliveryPolicyBlockInstrumentationHook),
        }
    }
}

impl InterceptorChain {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push<I>(&mut self, interceptor: I)
    where
        I: MessageInterceptor + 'static,
    {
        self.interceptors.push(Arc::new(interceptor));
    }

    pub fn push_arc(&mut self, interceptor: Arc<dyn MessageInterceptor>) {
        self.interceptors.push(interceptor);
    }

    pub fn with_block_hook(
        mut self,
        block_hook: Arc<dyn DeliveryPolicyBlockInstrumentationHook>,
    ) -> Self {
        self.block_hook = block_hook;
        self
    }

    pub fn set_block_hook(
        &mut self,
        block_hook: Arc<dyn DeliveryPolicyBlockInstrumentationHook>,
    ) {
        self.block_hook = block_hook;
    }

    pub fn len(&self) -> usize {
        self.interceptors.len()
    }

    pub fn is_empty(&self) -> bool {
        self.interceptors.is_empty()
    }

    pub async fn on_outbound(&self, msg: &mut OutboundMessage) -> InterceptorDecision {
        self.on_outbound_with_context(msg, DeliveryBlockContext::default())
            .await
    }

    pub async fn on_outbound_with_context(
        &self,
        msg: &mut OutboundMessage,
        block_context: DeliveryBlockContext,
    ) -> InterceptorDecision {
        let mut modified = false;
        for interceptor in &self.interceptors {
            match interceptor.on_outbound(msg).await {
                InterceptorDecision::Pass => {}
                InterceptorDecision::Modify => modified = true,
                block @ InterceptorDecision::Block(_) => {
                    self.block_hook.blocked(block_context).await;
                    return block;
                }
            }
        }
        if modified {
            InterceptorDecision::Modify
        } else {
            InterceptorDecision::Pass
        }
    }
}
