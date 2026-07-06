use async_trait::async_trait;
use bcs_service_api::interceptor::{
    BlockReason, InterceptorDecision, MessageInterceptor, OutboundMessage,
};
use bcs_service_api::{AgentCredentials, GroupMessage, GroupMessageType, MessageRole};
use bcs_services_container::InterceptorChain;

struct PassInterceptor;

#[async_trait]
impl MessageInterceptor for PassInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Pass
    }
}

struct ModifyInterceptor;

#[async_trait]
impl MessageInterceptor for ModifyInterceptor {
    async fn on_outbound(&self, msg: &mut OutboundMessage) -> InterceptorDecision {
        msg.message.content.push_str(" modified");
        InterceptorDecision::Modify
    }
}

struct BlockInterceptor;

#[async_trait]
impl MessageInterceptor for BlockInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "block".to_string(),
            code: "blocked".to_string(),
            message: "blocked by test".to_string(),
            user_visible: false,
        })
    }
}

#[tokio::test]
async fn chain_passes_when_empty() {
    let chain = InterceptorChain::new();
    let mut msg = sample_message();
    assert!(matches!(
        chain.on_outbound(&mut msg).await,
        InterceptorDecision::Pass
    ));
}

#[tokio::test]
async fn chain_reports_modify_after_any_modifier() {
    let mut chain = InterceptorChain::new();
    chain.push(PassInterceptor);
    chain.push(ModifyInterceptor);

    let mut msg = sample_message();
    assert!(matches!(
        chain.on_outbound(&mut msg).await,
        InterceptorDecision::Modify
    ));
    assert_eq!(msg.message.content, "hello modified");
}

#[tokio::test]
async fn chain_stops_on_block() {
    let mut chain = InterceptorChain::new();
    chain.push(BlockInterceptor);
    chain.push(ModifyInterceptor);

    let mut msg = sample_message();
    assert!(matches!(
        chain.on_outbound(&mut msg).await,
        InterceptorDecision::Block(_)
    ));
    assert_eq!(msg.message.content, "hello");
}

fn sample_message() -> OutboundMessage {
    OutboundMessage {
        group_id: "group".to_string(),
        receiver_bot_id: "receiver".to_string(),
        caller: AgentCredentials {
            agent_code: Some("sender-agent".to_string()),
            agent_token: Some("sender-token".to_string()),
        },
        receiver: AgentCredentials {
            agent_code: Some("receiver-agent".to_string()),
            agent_token: None,
        },
        message: GroupMessage {
            id: "message".to_string(),
            timestamp: 1,
            sender: "sender".to_string(),
            content: "hello".to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            history_meta: None,
            metadata: None,
            run_id: String::new(),
        },
    }
}
