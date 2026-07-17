//! Canonical identifiers for BCS-managed groups.

use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

use crate::GroupKind;

pub const GROUP_ID_PREFIX: &str = "bcs_grp_";
pub const GENERATED_SESSION_ID_SUFFIX_CHARS: usize = 9;
pub const MAX_SESSION_ID_CHARS: usize = 64;
pub const MAX_GENERATED_GROUP_ID_CHARS: usize =
    MAX_SESSION_ID_CHARS - GENERATED_SESSION_ID_SUFFIX_CHARS;

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum GroupIdBuildError {
    #[error("channel_type must use lowercase ASCII letters, digits, '-' or '_'")]
    InvalidChannelType,
    #[error("group id source must not be empty")]
    EmptySourceId,
    #[error(
        "generated group id cannot produce a session id within {MAX_SESSION_ID_CHARS} characters"
    )]
    SessionIdTooLong,
}

/// Generate a native BCS group id with a fresh opaque token.
pub fn generated_group_id(group_kind: GroupKind) -> String {
    let token = Uuid::new_v4().simple().to_string();
    compose_group_id(None, group_kind, &token)
}

/// Build a deterministic BCS-owned group id for an external Channel.
pub fn channel_group_id(
    channel_type: &str,
    group_kind: GroupKind,
    source_id: &str,
) -> Result<String, GroupIdBuildError> {
    let channel_type = channel_type.trim();
    if !valid_channel_type(channel_type) {
        return Err(GroupIdBuildError::InvalidChannelType);
    }
    let source_id = source_id.trim();
    if source_id.is_empty() {
        return Err(GroupIdBuildError::EmptySourceId);
    }

    let token = opaque_token(source_id);
    let group_id = compose_group_id(Some(channel_type), group_kind, &token);
    if group_id.chars().count() > MAX_GENERATED_GROUP_ID_CHARS {
        return Err(GroupIdBuildError::SessionIdTooLong);
    }
    Ok(group_id)
}

fn compose_group_id(channel_type: Option<&str>, group_kind: GroupKind, token: &str) -> String {
    match (channel_type, group_kind) {
        (None, GroupKind::Normal) => format!("{GROUP_ID_PREFIX}{token}"),
        (None, GroupKind::Dm) => format!("{GROUP_ID_PREFIX}dm_{token}"),
        (Some(channel), GroupKind::Normal) => format!("{GROUP_ID_PREFIX}{channel}_{token}"),
        (Some(channel), GroupKind::Dm) => format!("{GROUP_ID_PREFIX}{channel}_dm_{token}"),
    }
}

fn valid_channel_type(channel_type: &str) -> bool {
    // COSEC: Keep ':' and other delimiters out of generated IDs so a plugin
    // identifier cannot change the `{group_id}:{8_hex}` session boundary.
    !channel_type.is_empty()
        && channel_type
            .bytes()
            .all(|byte| {
                byte.is_ascii_lowercase()
                    || byte.is_ascii_digit()
                    || byte == b'-'
                    || byte == b'_'
            })
}

fn opaque_token(source_id: &str) -> String {
    if let Ok(uuid) = Uuid::parse_str(source_id) {
        return uuid.simple().to_string();
    }

    let digest = Sha256::digest(source_id.as_bytes());
    let mut token = String::with_capacity(32);
    for byte in &digest[..16] {
        use std::fmt::Write;
        let _ = write!(token, "{byte:02x}");
    }
    token
}

#[cfg(test)]
mod tests {
    use super::*;

    const UUID: &str = "bc7d5297-4947-474d-a2f1-cdea1c5642b6";

    #[test]
    fn native_group_ids_include_kind_when_needed() {
        assert_eq!(
            compose_group_id(None, GroupKind::Normal, "bc7d52974947474da2f1cdea1c5642b6"),
            "bcs_grp_bc7d52974947474da2f1cdea1c5642b6"
        );
        assert_eq!(
            compose_group_id(None, GroupKind::Dm, "bc7d52974947474da2f1cdea1c5642b6"),
            "bcs_grp_dm_bc7d52974947474da2f1cdea1c5642b6"
        );
    }

    #[test]
    fn channel_group_ids_include_channel_and_kind() -> Result<(), GroupIdBuildError> {
        assert_eq!(
            channel_group_id("dingtalk", GroupKind::Normal, UUID)?,
            "bcs_grp_dingtalk_bc7d52974947474da2f1cdea1c5642b6"
        );
        assert_eq!(
            channel_group_id("dingtalk", GroupKind::Dm, UUID)?,
            "bcs_grp_dingtalk_dm_bc7d52974947474da2f1cdea1c5642b6"
        );
        Ok(())
    }

    #[test]
    fn dingtalk_dm_group_leaves_room_for_session_suffix() -> Result<(), GroupIdBuildError> {
        let group_id = channel_group_id("dingtalk", GroupKind::Dm, UUID)?;
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(session_id.chars().count(), 61);
        assert!(session_id.chars().count() <= MAX_SESSION_ID_CHARS);
        Ok(())
    }

    #[test]
    fn max_channel_namespace_fits_session_id_limit() -> Result<(), GroupIdBuildError> {
        let group_id = channel_group_id("wechat_work", GroupKind::Dm, UUID)?;
        let session_id = format!("{group_id}:abcdef12");

        assert_eq!(group_id.chars().count(), MAX_GENERATED_GROUP_ID_CHARS);
        assert_eq!(session_id.chars().count(), MAX_SESSION_ID_CHARS);
        Ok(())
    }

    #[test]
    fn non_uuid_source_is_canonicalized_to_bounded_token() -> Result<(), GroupIdBuildError> {
        let first = channel_group_id("dingtalk", GroupKind::Dm, &"x".repeat(500))?;
        let second = channel_group_id("dingtalk", GroupKind::Dm, &"x".repeat(500))?;

        assert_eq!(first, second);
        assert_eq!(first.chars().count(), 52);
        Ok(())
    }

    #[test]
    fn rejects_invalid_or_overlong_channel_namespace() {
        assert_eq!(
            channel_group_id("ding:talk", GroupKind::Normal, UUID),
            Err(GroupIdBuildError::InvalidChannelType)
        );
        assert_eq!(
            channel_group_id("channel_name", GroupKind::Dm, UUID),
            Err(GroupIdBuildError::SessionIdTooLong)
        );
    }
}
