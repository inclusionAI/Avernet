//! Session core business rules（无 IO）。

use crate::types::{SessionKind, SessionStatus};
use bcs_domain::MAX_SESSION_ID_CHARS;

/// 校验 session_id 格式：`{group_id}:{8_hex}`。
/// 合法 legacy：`{group_id}:00000000`。
pub fn validate_session_id(session_id: &str, group_id: &str) -> bool {
    if session_id.chars().count() > MAX_SESSION_ID_CHARS {
        return false;
    }
    if !session_id.starts_with(group_id) {
        return false;
    }
    let rest = &session_id[group_id.len()..];
    let Some(suffix) = rest.strip_prefix(':') else {
        return false;
    };
    suffix.len() == 8 && suffix.chars().all(|c| c.is_ascii_hexdigit())
}

/// 生成新 session_id：`{group_id}:{8_hex}`。
/// 随机 u32 冲突概率 1/2^32；store 层用 `uk_session_id` 唯一索引兜底。
pub fn new_session_id(group_id: &str) -> Result<String, &'static str> {
    let session_id = format!("{}:{:08x}", group_id, fastrand::u32(..));
    if validate_session_id(&session_id, group_id) {
        Ok(session_id)
    } else {
        Err("generated session_id exceeds the 64-character limit")
    }
}

/// 唤醒前置：服务化 session 必须 Completed 且 callback_status 已是终态。
pub fn can_reactivate(
    status: SessionStatus,
    session_kind: SessionKind,
    callback_status: Option<&str>,
) -> Result<(), &'static str> {
    if !matches!(status, SessionStatus::Completed) {
        return Err("session is not completed");
    }
    if !matches!(session_kind, SessionKind::ServiceInvocation) {
        return Err("session is not service_invocation");
    }
    if callback_status == Some("pending") {
        return Err("callback is still pending");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_id_format_validation() {
        assert!(validate_session_id("g:aaaaaaaa", "g"));
        assert!(validate_session_id("g:00000000", "g"));
        assert!(!validate_session_id("g_aaaaaaaa", "g"));
        assert!(!validate_session_id("g:aaaa", "g"));
        assert!(!validate_session_id("h:aaaaaaaa", "g"));
    }

    #[test]
    fn generated_session_id_passes_validation() {
        let id = new_session_id("group-123").expect("bounded group id");
        assert!(validate_session_id(&id, "group-123"));
    }

    #[test]
    fn generated_session_id_rejects_overlong_group_id() {
        assert!(new_session_id(&"g".repeat(56)).is_err());
    }

    #[test]
    fn session_id_rejects_more_than_64_characters() {
        let group_id = "g".repeat(56);
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(session_id.chars().count(), 65);
        assert!(!validate_session_id(&session_id, &group_id));
    }

    #[test]
    fn session_id_accepts_exactly_64_characters() {
        let group_id = "g".repeat(55);
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(session_id.chars().count(), 64);
        assert!(validate_session_id(&session_id, &group_id));
    }

    #[test]
    fn reactivate_blocked_when_not_completed() {
        let r = can_reactivate(SessionStatus::Running, SessionKind::ServiceInvocation, None);
        assert!(r.is_err());
    }

    #[test]
    fn reactivate_blocked_for_chat_kind() {
        let r = can_reactivate(SessionStatus::Completed, SessionKind::Chat, None);
        assert!(r.is_err());
    }

    #[test]
    fn reactivate_blocked_when_callback_pending() {
        let r = can_reactivate(
            SessionStatus::Completed,
            SessionKind::ServiceInvocation,
            Some("pending"),
        );
        assert!(r.is_err());
    }

    #[test]
    fn reactivate_ok_when_terminal() {
        let r = can_reactivate(
            SessionStatus::Completed,
            SessionKind::ServiceInvocation,
            Some("succeeded"),
        );
        assert!(r.is_ok());
    }
}
