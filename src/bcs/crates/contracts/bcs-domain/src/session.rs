//! Session and ServiceSpec domain types.
//!
//! Ported from `bcs-services::lib.rs` (old commit 0c775f5b) §930–§1035.

use serde::{Deserialize, Serialize};

use crate::group::Participant;

/// Callback channel configuration for a service group.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum CallbackChannelConfig {
    #[serde(rename = "antding")]
    AntDing {
        access_key_id: String,
        access_key_secret: String,
        robot_code: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        user_id: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        open_conversation_id: Option<String>,
    },
    #[serde(rename = "baas")]
    Baas {
        base_url: String,
        api_key: String,
        bot_id: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        metadata: Option<serde_json::Value>,
    },
}

impl CallbackChannelConfig {
    pub fn type_name(&self) -> &'static str {
        match self {
            Self::AntDing { .. } => "antding",
            Self::Baas { .. } => "baas",
        }
    }
}

/// Callback configuration for a service group template.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CallbackConfig {
    #[serde(default)]
    pub channels: Vec<CallbackChannelConfig>,
}

/// 服务化 Group 的对外暴露配置。
///
/// - `callback_config` 本期 Immutable（未来 Draft+publish 可改）；
/// - `timeout_seconds` / `max_concurrency` 可原地 PATCH，
///   受路由字段锁保护（有 running service session 时 409）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceSpec {
    /// 回调通道配置（Immutable）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub callback_config: Option<CallbackConfig>,

    /// 超时秒数；NULL = 不超时判定。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_seconds: Option<i32>,

    /// 并发上限；NULL = 不限。仅统计 running 的 service_invocation session。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_concurrency: Option<i32>,
}

/// Session 状态机（本期仅 Running / Completed）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SessionStatus {
    Running,
    Completed,
}

impl Default for SessionStatus {
    fn default() -> Self {
        Self::Running
    }
}

/// Session 种类：chat（普通会话）或 service_invocation（服务化调用）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionKind {
    Chat,
    ServiceInvocation,
}

impl Default for SessionKind {
    fn default() -> Self {
        Self::Chat
    }
}

/// 一次服务调用 / 会话执行。
///
/// session_id 格式：`{group_id}:{8_hex}`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    /// session_id（业务 UUID 形式），格式 `{group_id}:{8_hex}`。
    pub id: String,

    /// 所属 group 的逻辑 id。
    pub group_id: String,

    /// 会话标题（前端展示用）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_title: Option<String>,

    /// 环境标签（`prod` / `pre` / `dev`）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub env: Option<String>,

    pub status: SessionStatus,
    pub session_kind: SessionKind,

    /// Session 成员（从 group.participants seed 后独立演化）。
    /// 路由决策基于该字段而非 group.participants —— 群层改 seed 不影响 in-flight session。
    #[serde(default)]
    pub participants: Vec<Participant>,

    /// Pin 到的 group version（本期恒为 Some(1)）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_version: Option<i32>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub caller_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub callback_status: Option<String>,

    /// 初始 1，每次 reactivate +1。
    #[serde(default = "default_activation_count")]
    pub activation_count: i32,

    /// 创建者稳定身份（对外服务化是 `svc-key:{sha256_hex[:16]}`）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub caller_principal: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_by: Option<String>,

    /// Current max message sequence number, atomically incremented on writes.
    #[serde(default)]
    pub current_msg_seq: i64,

    /// Per-participant join sequence e.g. `{"bot_a": 0, "bot_b": 42}`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub participant_join_seq: Option<serde_json::Value>,

    pub created_at: u64,
    pub updated_at: u64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<u64>,

    /// 调用方透传的元数据。
    /// 回调时作为 `instance_meta` 传给通道，可用于携带 `callback_target.user_id` 等动态参数。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub meta: Option<serde_json::Value>,
}

fn default_activation_count() -> i32 {
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn callback_channel_config_round_trips_baas() {
        let channel: CallbackChannelConfig = serde_json::from_value(serde_json::json!({
            "type": "baas",
            "base_url": "https://baas.example.com",
            "api_key": "sk-test",
            "bot_id": "default:151614",
            "metadata": {
                "title": "BCS service callback",
                "bot_options": {
                    "lifecycle_stage": "online"
                },
                "sender_options": {
                    "from": "owner"
                }
            }
        }))
        .expect("baas callback channel should deserialize");

        assert_eq!(channel.type_name(), "baas");

        let encoded = serde_json::to_value(&channel).expect("baas channel should serialize");
        assert_eq!(encoded["type"], "baas");
        assert_eq!(encoded["base_url"], "https://baas.example.com");
        assert_eq!(encoded["api_key"], "sk-test");
        assert_eq!(encoded["bot_id"], "default:151614");
        assert_eq!(encoded["metadata"]["title"], "BCS service callback");
        assert_eq!(encoded["metadata"]["bot_options"]["lifecycle_stage"], "online");
        assert_eq!(encoded["metadata"]["sender_options"]["from"], "owner");
    }
}
