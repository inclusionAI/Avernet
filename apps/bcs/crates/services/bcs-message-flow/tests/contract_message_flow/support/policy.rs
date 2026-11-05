use super::*;

pub(super) struct BlockingInterceptor;

#[async_trait::async_trait]
impl MessageInterceptor for BlockingInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-block".to_string(),
            code: "blocked".to_string(),
            message: "blocked by test".to_string(),
            user_visible: true,
        })
    }
}
