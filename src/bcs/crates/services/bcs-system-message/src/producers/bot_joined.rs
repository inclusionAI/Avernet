//! Producer for `SystemMessageEventKind::BotJoined`.
//!
//! When a bot joins a group this producer generates:
//! 1. A full context-injection message delivered to the newly joined bot,
//!    providing group info, member list, and recent message history.
//! 2. A short notification delivered to the other group members.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, GroupMessage, Participant, Skill, SystemMessageEvent,
    SystemMessageEventKind, SystemGroupMessage,
};
use bcs_service_api::{
    BotRegistryCoreService, CallerContext, GroupHistoryCommand, GroupMessageHistoryService,
    SystemMessageProducerService,
};

const HISTORY_LIMIT: usize = 10;
const HISTORY_MAX_LENGTH: usize = 200;

/// Produces system messages when a bot joins a group.
pub struct BotJoinedMessageProducer {
    history: Arc<dyn GroupMessageHistoryService>,
}

impl BotJoinedMessageProducer {
    pub fn new(history: Arc<dyn GroupMessageHistoryService>) -> Self {
        Self { history }
    }
}

#[async_trait]
impl SystemMessageProducerService for BotJoinedMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::BotJoined
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        group: &Group,
        registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        let SystemMessageEvent::BotJoined { actor, .. } = event else {
            return vec![];
        };

        let new_bot_uuid = actor.bot_uuid.clone();
        let mut messages = Vec::new();

        // 1. Full context injection to the newly joined bot.
        let new_bot_content =
            build_context_injection_message(group, participants, &new_bot_uuid, registry, &*self.history).await;
        messages.push(SystemGroupMessage {
            recipients: vec![new_bot_uuid.clone()],
            message: new_bot_content,
            delivery_type: DeliveryType::Inject,
        });

        // 2. Notification to other bots.
        let registered = registry.get(&new_bot_uuid).await;
        let summary = format_notification(&new_bot_uuid, registered.as_ref());
        let others: Vec<String> = participants
            .iter()
            .filter(|p| p.bot_uuid != new_bot_uuid)
            .map(|p| p.bot_uuid.clone())
            .collect();
        if !others.is_empty() {
            messages.push(SystemGroupMessage {
                recipients: others,
                message: summary,
                delivery_type: DeliveryType::Inject,
            });
        }
        messages
    }
}

fn format_notification(bot_uuid: &str, registered: Option<&bcs_domain::RegisteredBot>) -> String {
    let name = registered
        .and_then(|b| b.capabilities.name.clone())
        .unwrap_or_else(|| bot_uuid.to_string());
    let skills: &[Skill] = registered
        .map(|b| b.capabilities.skills.as_slice())
        .unwrap_or(&[]);
    let skills_str = format_skills(skills);
    if skills_str.is_empty() {
        format!("{}({}) 已加入协作群", name, bot_uuid)
    } else {
        format!(
            "{}({}) 已加入协作群 - 能力集: {}",
            name, bot_uuid, skills_str
        )
    }
}

fn format_skills(skills: &[Skill]) -> String {
    if skills.is_empty() {
        return String::new();
    }
    skills
        .iter()
        .map(|s| {
            if let Some(ref desc) = s.description {
                format!(r#"{{name: "{}", description: "{}"}}"#, s.name, desc)
            } else {
                format!(r#"{{name: "{}"}}"#, s.name)
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}

fn format_date(ts_ms: u64) -> String {
    chrono::DateTime::from_timestamp_millis(ts_ms as i64)
        .map(|dt: chrono::DateTime<chrono::Utc>| dt.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_default()
}

fn truncate_utf8(s: &str, max_chars: usize) -> String {
    match s.char_indices().nth(max_chars) {
        Some((idx, _)) => format!("{}...", &s[..idx]),
        None => s.to_string(),
    }
}

/// Compose a context-injection message for the newly joined bot.
async fn build_context_injection_message(
    group: &Group,
    participants: &[Participant],
    new_bot_uuid: &str,
    registry: &dyn BotRegistryCoreService,
    history: &dyn GroupMessageHistoryService,
) -> String {
    let mut parts = vec!["你加入了 BCS 协作群.".to_string()];
    parts.push(format!("群 ID: {}", group.id));
    parts.push(format!(
        "主题: {}",
        group.label.as_deref().unwrap_or("N/A")
    ));

    parts.push("参与者:".to_string());
    for p in participants {
        let registered = registry.get(&p.bot_uuid).await;
        let name = registered
            .as_ref()
            .and_then(|b| b.capabilities.name.as_deref())
            .or(p.bot_name.as_deref())
            .unwrap_or(&p.bot_uuid);
        let skills: &[Skill] = registered
            .as_ref()
            .map(|b| b.capabilities.skills.as_slice())
            .unwrap_or(&[]);
        let line = if !skills.is_empty() {
            format!(
                "  - {}({}) - 能力集: {}",
                name,
                p.bot_uuid,
                format_skills(skills)
            )
        } else {
            format!("  - {}({})", name, p.bot_uuid)
        };
        parts.push(line);
    }

    let history_messages = fetch_history(group, history).await;
    parts.push(format!(
        "群历史消息 (最近 {} 条):\n---\n",
        history_messages.len()
    ));
    for msg in history_messages.iter().rev().take(HISTORY_LIMIT).rev() {
        let sender_name = msg
            .bot_name
            .as_deref()
            .unwrap_or(&msg.sender);
        let date = format_date(msg.timestamp);
        let content = truncate_utf8(&msg.content, HISTORY_MAX_LENGTH);
        parts.push(format!("[{}] {}\n{}\n\n---\n", date, sender_name, content));
    }

    let _ = new_bot_uuid;

    parts.join("\n")
}

async fn fetch_history(
    group: &Group,
    history: &dyn GroupMessageHistoryService
) -> Vec<GroupMessage> {
    let cmd = GroupHistoryCommand {
        caller: CallerContext::Bot(bcs_service_api::BotActor {
            bot_uuid: group.driver_bot.clone(),
        }),
        group_id: group.id.clone(),
        view_bot_id: Some(group.driver_bot.clone()),
        limit: HISTORY_LIMIT as u64,
        before: None,
    };
    match history.get_history(cmd).await {
        Ok(result) => result.messages,
        Err(error) => {
            tracing::warn!(
                group_id = %group.id,
                driver_bot = %group.driver_bot,
                error = %error,
                "fallback to group.messages for system message history"
            );
            group.messages.clone()
        }
    }
}
