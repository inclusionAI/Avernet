//! Session core business rules（无 IO）。

use crate::types::{SessionKind, SessionStatus};
use bcs_domain::{
    MAX_GENERATED_GROUP_ID_CHARS, MAX_SESSION_ID_CHARS, is_valid_channel_type,
};

const CHANNEL_SESSION_PREFIX: &str = "channel_";

/// Validate native `{group_id}:{8_hex}` and Channel
/// `{group_id}:channel_{channel_type}_{8_hex}` session ids.
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
    valid_random_suffix(suffix) || channel_type_from_suffix(suffix).is_some()
}

/// 生成新 session_id：`{group_id}:{8_hex}`。
/// 随机 u32 冲突概率 1/2^32；store 层用 `uk_session_id` 唯一索引兜底。
pub fn new_session_id(group_id: &str) -> Result<String, &'static str> {
    if group_id.chars().count() > MAX_GENERATED_GROUP_ID_CHARS {
        return Err("group_id exceeds the generated group identifier limit");
    }
    let session_id = format!("{}:{:08x}", group_id, fastrand::u32(..));
    if validate_session_id(&session_id, group_id) {
        Ok(session_id)
    } else {
        Err("generated session_id exceeds the 128-character limit")
    }
}

/// Generate a source-identifying Channel session id while preserving the
/// single `:` separator used to recover the group id.
pub fn new_channel_session_id(
    group_id: &str,
    channel_type: &str,
) -> Result<String, &'static str> {
    let channel_type = channel_type.trim();
    if !is_valid_channel_type(channel_type) {
        return Err("channel_type must use 1-32 lowercase ASCII letters, digits, '-' or '_'");
    }
    let session_id = format!(
        "{group_id}:{CHANNEL_SESSION_PREFIX}{channel_type}_{:08x}",
        fastrand::u32(..)
    );
    if validate_session_id(&session_id, group_id) {
        Ok(session_id)
    } else {
        Err("generated channel session_id exceeds the 128-character limit")
    }
}

/// Return the concrete Channel type encoded in a new-format session id.
/// Native and legacy ids return `None`.
pub fn session_id_channel_type<'a>(session_id: &'a str, group_id: &str) -> Option<&'a str> {
    if session_id.chars().count() > MAX_SESSION_ID_CHARS {
        return None;
    }
    let suffix = session_id.strip_prefix(group_id)?.strip_prefix(':')?;
    channel_type_from_suffix(suffix)
}

fn channel_type_from_suffix(suffix: &str) -> Option<&str> {
    let encoded = suffix.strip_prefix(CHANNEL_SESSION_PREFIX)?;
    let (channel_type, random) = encoded.rsplit_once('_')?;
    (is_valid_channel_type(channel_type) && valid_random_suffix(random)).then_some(channel_type)
}

fn valid_random_suffix(value: &str) -> bool {
    value.len() == 8 && value.chars().all(|c| c.is_ascii_hexdigit())
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
        assert!(validate_session_id("g:channel_dingtalk_abcdef12", "g"));
        assert!(validate_session_id("g:channel_ding_talk_abcdef12", "g"));
        assert!(!validate_session_id("g:channel__abcdef12", "g"));
        assert!(!validate_session_id("g:channel_DingTalk_abcdef12", "g"));
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
    fn generated_channel_session_id_preserves_group_separator() {
        let group_id = "bcs_grp_dingtalk_dm_1234567890abcdef";
        let id = new_channel_session_id(group_id, "ding_talk").expect("valid channel id");
        let (parsed_group, _) = id.split_once(':').expect("single group separator");
        assert_eq!(parsed_group, group_id);
        assert_eq!(session_id_channel_type(&id, group_id), Some("ding_talk"));
        assert_eq!(id.matches(':').count(), 1);
    }

    #[test]
    fn generated_channel_session_id_rejects_unsafe_channel_type() {
        assert!(new_channel_session_id("group-123", "ding:talk").is_err());
        assert!(new_channel_session_id("group-123", &"a".repeat(33)).is_err());
    }

    #[test]
    fn session_id_rejects_more_than_128_characters() {
        let group_id = "g".repeat(120);
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(session_id.chars().count(), 129);
        assert!(!validate_session_id(&session_id, &group_id));
    }

    #[test]
    fn session_id_accepts_exactly_128_characters() {
        let group_id = "g".repeat(119);
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(session_id.chars().count(), 128);
        assert!(validate_session_id(&session_id, &group_id));
    }

    #[test]
    fn session_id_limit_counts_unicode_characters_not_bytes() {
        let group_id = "群".repeat(MAX_GENERATED_GROUP_ID_CHARS);
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(session_id.chars().count(), 64);
        assert!(session_id.len() > MAX_SESSION_ID_CHARS);
        assert!(validate_session_id(&session_id, &group_id));
        assert!(new_session_id(&group_id).is_ok());
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
