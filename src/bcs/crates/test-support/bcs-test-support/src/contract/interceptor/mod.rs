//! Interceptor contract harnesses.

use bcs_service_api::interceptor::{
    InterceptorDecision, MessageInterceptor, OutboundMessage,
};
use bcs_service_api::{AgentCredentials, GroupMessage, GroupMessageType, MessageRole};

pub async fn message_interceptor_contract_tests<T: MessageInterceptor + ?Sized>(svc: &T) {
    let mut first = sample_outbound_message();
    let first_decision = svc.on_outbound(&mut first).await;
    assert_valid_decision(first_decision);

    let mut second = sample_outbound_message();
    let second_decision = svc.on_outbound(&mut second).await;
    assert_valid_decision(second_decision);
}

fn sample_outbound_message() -> OutboundMessage {
    OutboundMessage {
        group_id: "contract-group".to_string(),
        receiver_bot_id: "contract-receiver".to_string(),
        caller: AgentCredentials {
            agent_code: Some("sender-agent".to_string()),
            agent_token: Some("sender-token".to_string()),
        },
        receiver: AgentCredentials {
            agent_code: Some("receiver-agent".to_string()),
            agent_token: None,
        },
        message: GroupMessage {
            id: "contract-message".to_string(),
            timestamp: 1,
            sender: "contract-sender".to_string(),
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

fn assert_valid_decision(decision: InterceptorDecision) {
    if let InterceptorDecision::Block(reason) = decision {
        assert!(
            !reason.interceptor_id.is_empty(),
            "block decisions must identify the interceptor"
        );
        assert!(!reason.code.is_empty(), "block decisions must include a code");
        assert!(
            !reason.message.is_empty(),
            "block decisions must include a message"
        );
    }
}
