use async_trait::async_trait;

use crate::ServiceResult;

/// 单个被 @ 的人类参与者。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MentionedHuman {
    /// 人类参与者 actor id，形如 `human_{staff_no}`。
    pub actor_id: String,
    /// @ 时使用的显示名（参与者 `bot_name`）。
    pub display_name: String,
}

/// 一次 @ 人类提醒事件。一条消息对应一个事件，包含该消息中全部被 @ 的人类。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MentionNotification {
    /// 会话 id；群级消息（无 session）时为空字符串。
    pub session_id: String,
    pub group_id: String,
    /// 发送者 actor id（`bot_x` 或 `human_y`，群回调为 `system`）。
    pub sender_actor_id: String,
    /// 发送者展示名。
    pub sender_label: String,
    /// 本条消息中被 @ 的全部人类（已排除发送者自身与 Hidden 状态人类）。
    pub mentioned: Vec<MentionedHuman>,
    /// 消息文本：文本路由为 `RoutingDecision.cleaned_message`，
    /// 显式 mention 路径为原始消息文本（契约 6.1）。
    pub message_text: String,
    pub timestamp_ms: u64,
}

/// @ 人类提醒通知端口。核心消息流只依赖本 trait；bootstrap 组合根把选中的
/// 通知后端（`bcs-human-notify-api` 的 `HumanMentionNotifier`）适配为端口。
#[async_trait]
pub trait HumanMentionNotifyPort: Send + Sync {
    /// 后端是否可用。未配置后端时为 `false`，消息流零开销跳过。
    fn is_available(&self) -> bool;

    /// 投递一次提醒。错误由调用方（消息流）吞掉，不影响主路径。
    async fn notify_mentioned_humans(
        &self,
        notification: MentionNotification,
    ) -> ServiceResult<()>;
}

/// 未配置通知后端时的 no-op 端口。
pub struct NoopHumanMentionNotifyPort;

#[async_trait]
impl HumanMentionNotifyPort for NoopHumanMentionNotifyPort {
    fn is_available(&self) -> bool {
        false
    }

    async fn notify_mentioned_humans(
        &self,
        _notification: MentionNotification,
    ) -> ServiceResult<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_notification() -> MentionNotification {
        MentionNotification {
            session_id: "group-1:abcdef12".to_string(),
            group_id: "group-1".to_string(),
            sender_actor_id: "bot-driver".to_string(),
            sender_label: "Driver".to_string(),
            mentioned: vec![MentionedHuman {
                actor_id: "human_1".to_string(),
                display_name: "Human One".to_string(),
            }],
            message_text: "hello".to_string(),
            timestamp_ms: 1_700_000_000_000,
        }
    }

    #[test]
    fn noop_port_is_unavailable_and_returns_ok() {
        let port = NoopHumanMentionNotifyPort;
        assert!(!port.is_available());
    }

    #[tokio::test]
    async fn noop_port_accepts_notification() {
        let port = NoopHumanMentionNotifyPort;
        port.notify_mentioned_humans(sample_notification())
            .await
            .expect("noop port must accept notifications");
    }
}
