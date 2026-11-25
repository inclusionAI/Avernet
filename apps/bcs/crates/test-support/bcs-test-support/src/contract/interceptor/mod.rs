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
            attachments: None,
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

/// Invocation-capable providers opt into this separate operation contract.
/// Outbound-only providers keep the existing shared harness above; None means
/// unsupported, not authorization. The fixture controls current trusted facts.
pub async fn agent_invocation_interceptor_contract_tests(
    svc: &dyn MessageInterceptor,
    input: &bcs_service_api::application::agent_invoke::AgentInvocationInput,
    expected: bcs_service_api::application::agent_invoke::AgentInvokeResult<
        bcs_service_api::application::agent_invoke::AgentInvocationPermit>,
) {
    assert_eq!(svc.on_agent_invocation(input).await, Some(expected));
    // New invocation authority must not become group/content authorization.
    let mut ordinary = sample_outbound_message();
    assert!(matches!(svc.on_outbound(&mut ordinary).await, InterceptorDecision::Pass));
}
