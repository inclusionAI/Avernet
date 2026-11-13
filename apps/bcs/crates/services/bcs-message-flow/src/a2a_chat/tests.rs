use super::*;

#[test]
fn direct_chat_failure_reason_maps_legacy_bot_error_text() {
    assert_eq!(
        direct_chat_failure_reason("Timeout waiting for bot response"),
        DirectChatRunReason::Timeout
    );
    assert_eq!(
        direct_chat_failure_reason("Agent response timed out before completion."),
        DirectChatRunReason::Timeout
    );
    assert_eq!(
        direct_chat_failure_reason("executor safety-net: time out"),
        DirectChatRunReason::Timeout
    );
    assert_eq!(
        direct_chat_failure_reason("target bot is not connected"),
        DirectChatRunReason::BotNotConnected
    );
    assert_eq!(
        direct_chat_failure_reason("blocked:policy"),
        DirectChatRunReason::Blocked
    );
    assert_eq!(
        direct_chat_failure_reason("unexpected model error"),
        DirectChatRunReason::InternalError
    );
    assert_eq!(
        direct_chat_service_error_reason(&ServiceError::InternalError(
            "provider response header timeout after 5000ms".to_string()
        )),
        DirectChatRunReason::Timeout
    );
    assert_eq!(
        direct_chat_service_error_reason(&ServiceError::InternalError(
            "provider request failed: operation timed out".to_string()
        )),
        DirectChatRunReason::Timeout
    );
}
