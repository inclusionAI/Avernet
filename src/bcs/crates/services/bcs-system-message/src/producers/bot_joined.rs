//! Producer for `SystemMessageEventKind::BotJoined`.
//!
//! When a bot joins a group this producer generates:
//! 1. A full context-injection message delivered to the newly joined bot,
//!    providing group info, member list, and recent message history.
//! 2. A short notification delivered to the other group members.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    DeliveryType, Group, GroupMessage, GroupStrategy, Participant, ParticipantRole, PersistMode,
    Skill, SystemMessageEvent, SystemMessageEventKind, SystemGroupMessage,
};
use bcs_service_api::{
    BotRegistryCoreService, CallerContext, GroupMessageHistoryService, SessionHistoryCommand,
    SystemMessageProducerService, backfill_bot_names,
};

use super::session_context::{
    contains_provider_downlink_bot, group_info_section, participant_table, render_group_context,
    routing_instruction_block, skill_section,
};

/// Lead line for the `<GroupContext>` block delivered to a newly joined bot.
const JOINED_OPENING: &str = "你已加入 bcn 群聊，群聊相关信息如下";

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
    ) -> (Vec<SystemGroupMessage>, Option<String>) {
        let SystemMessageEvent::BotJoined {
            actor,
            session_id,
            ..
        } = event
        else {
            return (vec![], None);
        };

        let new_bot_uuid = actor.bot_uuid.clone();
        let mut messages = Vec::new();

        // 1. Full context injection to the newly joined bot (personalized,
        // persisted with owner = the new bot only).
        let new_bot_content = build_context_injection_message(
            group,
            participants,
            &new_bot_uuid,
            session_id,
            registry,
            &*self.history,
        )
        .await;
        messages.push(SystemGroupMessage {
            recipients: vec![new_bot_uuid.clone()],
            message: new_bot_content,
            delivery_type: DeliveryType::Inject,
            persist: PersistMode::PerRecipient,
        });

        // 2. Notification to other bots — identical text for every recipient,
        // so persist a single public record (owner = None) that human viewers
        // also read in history.
        let registered = registry.get(&new_bot_uuid).await;
        let summary = format_notification(&new_bot_uuid, registered.as_ref());
        let user_message = Some(summary.clone());
        let others: Vec<String> = participants
            .iter()
            .filter(|p| p.bot_uuid != new_bot_uuid)
            .map(|p| p.bot_uuid.clone())
            .collect();
        messages.push(SystemGroupMessage {
            recipients: others,
            message: summary,
            delivery_type: DeliveryType::Inject,
            persist: PersistMode::Public,
        });
        (messages, user_message)
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
///
/// Mirrors the unified `<GroupContext>` block used by the session-context
/// producer, with a `你已加入` opening and a trailing `## 群历史消息` section.
/// The `BotJoined` event carries no session/reason/task, so `## 群聊信息`
/// omits `会话ID`/`目标` and there is no `## 任务说明` section.
async fn build_context_injection_message(
    group: &Group,
    participants: &[Participant],
    new_bot_uuid: &str,
    session_id: &str,
    registry: &dyn BotRegistryCoreService,
    history: &dyn GroupMessageHistoryService,
) -> String {
    let mut render_group = group.clone();
    render_group.participants = participants.to_vec();
    backfill_bot_names(registry, &mut render_group).await;

    let recipient = render_group
        .participants
        .iter()
        .find(|p| p.bot_uuid == new_bot_uuid)
        .cloned()
        .unwrap_or_else(|| Participant::bot(new_bot_uuid, ParticipantRole::Consultant));

    let mode = if render_group.group_strategy == GroupStrategy::ManagerWorker {
        "manager_worker"
    } else {
        "自由聊天"
    };

    let bot_participants: Vec<&Participant> = render_group
        .participants
        .iter()
        .filter(|p| p.is_bot())
        .collect();
    let use_at_mention_routing = contains_provider_downlink_bot(&bot_participants, registry).await;
    let tool_kind = if use_at_mention_routing {
        "@mention"
    } else {
        "bcs_route"
    };
    let role_instruction = if use_at_mention_routing {
        "你当前通过 chat.inject 收到初始化上下文，应静默观察，不要主动回复；等待 @mention 或任务点名后再响应。"
    } else {
        "你当前通过 chat.inject 收到初始化上下文，应静默观察，不要主动回复；等待 @mention、bcs_route 或任务点名后再响应。"
    };

    let history_messages = fetch_history(group, session_id, participants, history).await;

    let sections = vec![
        group_info_section(&render_group, None, None, &recipient, mode),
        format!("## 参与者:\n{}", participant_table(&render_group)),
        format!(
            "## 工具说明 ({})\n{}",
            tool_kind,
            routing_instruction_block(use_at_mention_routing)
        ),
        skill_section(),
        format!("## 说明\n{}", role_instruction),
        history_section(&history_messages),
    ];
    render_group_context(JOINED_OPENING, &sections)
}

/// Renders the `## 群历史消息 (最近 N 条)` section with the most recent
/// messages (chronological, capped at `HISTORY_LIMIT`).
fn history_section(messages: &[GroupMessage]) -> String {
    let recent: Vec<&GroupMessage> = messages.iter().rev().take(HISTORY_LIMIT).rev().collect();
    let mut section = format!("## 群历史消息 (最近 {} 条)", recent.len());
    if recent.is_empty() {
        section.push_str("\n暂无历史消息");
    } else {
        for msg in &recent {
            let sender_name = msg.bot_name.as_deref().unwrap_or(&msg.sender);
            let date = format_date(msg.timestamp);
            let content = truncate_utf8(&msg.content, HISTORY_MAX_LENGTH);
            section.push_str(&format!("\n[{}] {}\n{}\n---", date, sender_name, content));
        }
    }
    section
}

async fn fetch_history(
    group: &Group,
    session_id: &str,
    session_participants: &[Participant],
    history: &dyn GroupMessageHistoryService,
) -> Vec<GroupMessage> {
    let cmd = SessionHistoryCommand {
        caller: CallerContext::Bot(bcs_service_api::BotActor {
            bot_uuid: group.driver_bot.clone(),
        }),
        group_id: group.id.clone(),
        session_id: session_id.to_string(),
        session_participants: session_participants.to_vec(),
        view_bot_id: Some(group.driver_bot.clone()),
        limit: HISTORY_LIMIT as u64,
        before: None,
    };
    match history.get_session_history(cmd).await {
        Ok(result) => result.messages,
        Err(error) => {
            tracing::warn!(
                group_id = %group.id,
                session_id = %session_id,
                driver_bot = %group.driver_bot,
                error = %error,
                "fallback to group.messages for system message history"
            );
            group.messages.clone()
        }
    }
}
