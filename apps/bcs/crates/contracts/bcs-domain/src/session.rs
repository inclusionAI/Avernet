//! Session domain types.
//!
//! Ported from `bcs-services::lib.rs` (old commit 0c775f5b) §930–§1035.

use serde::{Deserialize, Serialize};

use crate::group::Participant;

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
/// 原生 session_id 格式：`{group_id}:{8_hex}`。
/// Channel session_id 格式：`{group_id}:channel_{channel_type}_{8_hex}`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    /// BCS canonical session_id，最大 128 个字符。
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

    /// `0` is legacy visibility; `1` guarantees producer Domain and scoped
    /// Audience metadata for all newly written collaboration artifacts.
    #[serde(default)]
    pub message_visibility_version: u8,

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

    /// 收藏事件时间（epoch ms）。仅在「已收藏列表」上下文中由 store 填充；
    /// 其余查询路径保持 `None`，序列化时省略。
    /// 排序语义：`COALESCE(collected_at, created_at)`，由近到远。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub collected_at: Option<u64>,

    /// 调用方透传的元数据。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub meta: Option<serde_json::Value>,
}

fn default_activation_count() -> i32 {
    1
}
