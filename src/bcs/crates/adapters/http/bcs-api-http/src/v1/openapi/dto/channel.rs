use bcs_domain::{
    BindingStatus, BindingTarget, ChannelBinding, ChannelType, GroupChatScope, Visibility,
};
use bcs_service_api::CreateBindingCommand;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// POST body for `POST /openapi/v1/collaboration/channels/bindings`.
///
/// `env` is omitted: the service uses its own configured env; `cmd.env` was a
/// never-read legacy field. `deny_unknown_fields` rejects any client-supplied
/// `env` with 400 `invalid_request`.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateChannelBindingRequest {
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    #[serde(default)]
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub config: Value,
}

impl CreateChannelBindingRequest {
    /// Build the shared `CreateBindingCommand`, carrying the authenticated
    /// human user id as `created_by`. `env` is left empty —
    /// `BcsChannelService` ignores `cmd.env` and uses its own runtime env.
    pub fn into_command(self, created_by: String) -> CreateBindingCommand {
        CreateBindingCommand {
            channel_type: self.channel_type,
            account_ref: self.account_ref,
            target: self.target,
            group_chat_scope: self.group_chat_scope,
            outbound_visibility: self.outbound_visibility,
            env: String::new(),
            created_by: Some(created_by),
            config: self.config,
        }
    }
}

/// Public binding shape — mirrors legacy `BindingResponse`. `config` is the
/// service-redacted copy (POST via `redact_config`, GET/list via `redact_bindings`).
#[derive(Debug, Serialize)]
pub struct ChannelBindingDto {
    pub id: String,
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub env: String,
    pub status: BindingStatus,
    pub created_by: Option<String>,
    pub config: Value,
}

impl From<ChannelBinding> for ChannelBindingDto {
    fn from(b: ChannelBinding) -> Self {
        Self {
            id: b.id,
            channel_type: b.channel_type,
            account_ref: b.account_ref,
            target: b.target,
            group_chat_scope: b.group_chat_scope,
            outbound_visibility: b.outbound_visibility,
            env: b.env,
            status: b.status,
            created_by: b.created_by,
            config: b.config,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ChannelBindingPage {
    pub items: Vec<ChannelBindingDto>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BindingTargetType {
    Bot,
    Group,
}

/// GET `/channels/bindings/by-target` query.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListBindingsByTargetQuery {
    pub target_type: BindingTargetType,
    pub target_id: String,
    #[serde(default)]
    pub channel_type: Option<ChannelType>,
}

/// Resolve the target query to a `BindingTarget`; an empty `target_id`
/// surfaces as a message the route maps to 400 `invalid_request`
/// (matches legacy `normalize_target_query`).
pub fn normalize_target_query(
    query: ListBindingsByTargetQuery,
) -> Result<(BindingTarget, Option<ChannelType>), String> {
    let target_id = query.target_id.trim();
    if target_id.is_empty() {
        return Err("target_id is required".to_string());
    }
    let channel_type = query
        .channel_type
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty());
    let target = match query.target_type {
        BindingTargetType::Bot => BindingTarget::Bot { bot_id: target_id.to_string() },
        BindingTargetType::Group => BindingTarget::Group { group_id: target_id.to_string() },
    };
    Ok((target, channel_type))
}

/// PATCH body — `active` and `config` are mutually exclusive (exactly one).
/// `deny_unknown_fields` plus the route's XOR check enforces the contract.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateChannelBindingRequest {
    #[serde(default)]
    pub active: Option<bool>,
    #[serde(default)]
    pub config: Option<Value>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_request_rejects_env_field() {
        let json = serde_json::json!({
            "channel_type": "dingtalk",
            "account_ref": "r1",
            "target": { "bot": { "bot_id": "b1" } },
            "outbound_visibility": "full_transcript",
            "config": {},
            "env": "prod"
        });
        assert!(serde_json::from_value::<CreateChannelBindingRequest>(json).is_err());
    }

    #[test]
    fn create_request_parses_minimal() {
        let json = serde_json::json!({
            "channel_type": "dingtalk",
            "account_ref": "r1",
            "target": { "bot": { "bot_id": "b1" } },
            "outbound_visibility": "full_transcript",
            "config": {}
        });
        let req: CreateChannelBindingRequest = serde_json::from_value(json).unwrap();
        assert_eq!(req.account_ref, "r1");
        assert!(req.group_chat_scope.is_none());
        let cmd = req.into_command("staff-1".to_string());
        assert_eq!(cmd.created_by.as_deref(), Some("staff-1"));
        assert_eq!(cmd.env, "");
    }

    #[test]
    fn update_request_rejects_unknown_field() {
        let json = serde_json::json!({ "active": true, "bogus": 1 });
        assert!(serde_json::from_value::<UpdateChannelBindingRequest>(json).is_err());
    }

    #[test]
    fn normalize_by_target_requires_target_id() {
        let q = ListBindingsByTargetQuery {
            target_type: BindingTargetType::Bot,
            target_id: "  ".into(),
            channel_type: None,
        };
        assert!(normalize_target_query(q).is_err());
    }

    #[test]
    fn normalize_by_target_builds_bot_target() {
        let q = ListBindingsByTargetQuery {
            target_type: BindingTargetType::Bot,
            target_id: "b1".into(),
            channel_type: Some("dingtalk".into()),
        };
        let (target, channel_type) = normalize_target_query(q).expect("ok");
        assert!(matches!(target, BindingTarget::Bot { .. }));
        assert_eq!(channel_type.as_deref(), Some("dingtalk"));
    }
}
