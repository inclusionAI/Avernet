//! Producer for `SystemMessageEventKind::SessionContext`.
//!
//! When a session is created this producer generates the initial
//! `[GROUP CONTEXT]` or `[SERVICE GROUP CONTEXT]` message delivered
//! to all bot participants, with `chat.send` for the driver/manager
//! and `chat.inject` for other participants.

use std::collections::HashMap;

use async_trait::async_trait;
use bcs_domain::{
    CoordinationMode, CoordinationSurface, DeliveryType, Group, GroupStrategy, LedgerSummary,
    Participant, ParticipantRole, SystemMessageEvent, SystemMessageEventKind, SystemGroupMessage,
};
use bcs_service_api::{
    BotRegistryCoreService, SystemMessageProducerService, backfill_bot_names,
};

pub struct SessionContextMessageProducer;

#[async_trait]
impl SystemMessageProducerService for SessionContextMessageProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::SessionContext
    }

    async fn produce(
        &self,
        event: &SystemMessageEvent,
        group: &Group,
        registry: &dyn BotRegistryCoreService,
        participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        let SystemMessageEvent::SessionContext {
            session_id,
            reason,
            session_input,
            task_ledger,
            ..
        } = event
        else {
            return vec![];
        };

        let mut render_group = group.clone();
        render_group.participants = participants.to_vec();
        backfill_bot_names(registry, &mut render_group).await;

        let bot_summaries = build_bot_summaries(&render_group.participants, registry).await;
        let task_input_text = session_input
            .as_ref()
            .map(|v| v.as_str().map(str::to_string).unwrap_or_else(|| {
                serde_json::to_string_pretty(v).unwrap_or_else(|_| v.to_string())
            }));

        let bot_participants: Vec<&Participant> = render_group
            .participants
            .iter()
            .filter(|p| p.is_bot())
            .collect();
        let has_provider_downlink_bot =
            contains_provider_downlink_bot(&bot_participants, registry).await;

        let mut messages = Vec::new();
        for participant in bot_participants {
            let is_driver = is_lead_participant(&render_group, participant);
            let delivery_type = if is_driver {
                DeliveryType::Send
            } else {
                DeliveryType::Inject
            };

            let context_message = if render_group.group_strategy == GroupStrategy::ManagerWorker {
                let coordination_surface = registry
                    .resolve_coordination_surface(&participant.bot_uuid)
                    .await
                    .unwrap_or_else(|_| CoordinationSurface::legacy_upstream());
                manager_worker_initial_message(
                    &render_group,
                    session_id,
                    participant,
                    render_group.context.as_deref(),
                    delivery_type,
                    &bot_summaries,
                    task_input_text.as_deref(),
                    task_ledger.as_ref(),
                    &coordination_surface,
                )
            } else {
                initial_group_context_message(
                    &render_group,
                    session_id,
                    participant,
                    reason,
                    render_group.context.as_deref(),
                    delivery_type,
                    has_provider_downlink_bot,
                    task_input_text.as_deref(),
                )
            };

            messages.push(SystemGroupMessage {
                recipients: vec![participant.bot_uuid.clone()],
                message: context_message,
                delivery_type,
            });
        }
        messages
    }
}

async fn build_bot_summaries(participants: &[Participant], registry: &dyn BotRegistryCoreService) -> HashMap<String, String> {
    let mut summaries = HashMap::new();
    for participant in participants.iter().filter(|p| p.is_bot()) {
        if let Some(bot) = registry.get(&participant.bot_uuid).await {
            if let Some(summary) = bot.capabilities.summary.filter(|s| !s.is_empty()) {
                summaries.insert(participant.bot_uuid.clone(), summary);
            }
        }
    }
    summaries
}

async fn contains_provider_downlink_bot(
    participants: &[&Participant],
    registry: &dyn BotRegistryCoreService,
) -> bool {
    for participant in participants {
        if registry
            .resolve_delivery_target(&participant.bot_uuid)
            .await
            .map(|target| target.is_http_provider())
            .unwrap_or(false)
        {
            return true;
        }
    }
    false
}

fn is_lead_participant(group: &Group, participant: &Participant) -> bool {
    let lead_role = group.group_strategy.lead_role();
    if group.participants.iter().any(|p| p.role == lead_role) {
        participant.role == lead_role
    } else {
        participant.bot_uuid == group.driver_bot
    }
}

fn initial_group_context_message(
    group: &Group,
    session_id: &str,
    recipient: &Participant,
    topic: &str,
    user_context: Option<&str>,
    delivery_type: DeliveryType,
    use_at_mention_routing: bool,
    task_input: Option<&str>,
) -> String {
    let base_context = user_context
        .filter(|ctx| !ctx.trim().is_empty())
        .map(|ctx| format!("背景: {}\n", ctx.trim()))
        .unwrap_or_default();

    let task_line = task_input
        .filter(|task| !task.trim().is_empty())
        .map(|task| format!("\n[任务]\n{}\n[/任务]\n", task.trim()))
        .unwrap_or_default();

    let role_instruction = match delivery_type {
        DeliveryType::Send => {
            "你是本次协作的 Driver。请介绍协作目标，判断下一步需要谁参与，并开始协调。"
        }
        DeliveryType::Inject if use_at_mention_routing => {
            "你当前通过 chat.inject 收到初始化上下文，应静默观察，不要主动回复；等待 @mention 或任务点名后再响应。"
        }
        DeliveryType::Inject => {
            "你当前通过 chat.inject 收到初始化上下文，应静默观察，不要主动回复；等待 @mention、bcs_route 或任务点名后再响应。"
        }
    };
    let routing_instruction = if use_at_mention_routing {
        "路由工具 (@mention):\n\
           消息中任何 @ 标识都会触发路由，让被 @ 的 Bot 收到消息并被要求响应。\n\
           只有希望某个 Bot 响应时才使用 @，不要用 @ 表示引用、收到或转述某个 Bot 的消息。\n\
           优先使用名称；名称为空、重复或不确定时，使用 Bot ID。"
    } else {
        "路由工具 (bcs_route):\n\
           使用 bcs_route 工具指定下一个响应者（替代 @mention）。\n\
           - to: 目标 Bot 列表，支持按名称或 bot_id 选择\n\
             - 按名称: {\"type\": \"name\", \"value\": \"DBA\"}\n\
             - 按ID: {\"type\": \"bot\", \"value\": \"bot_54123f4f\"}\n\
           - reason: 路由原因"
    };
    let roster = if use_at_mention_routing {
        format_roster_with_mentions(group)
    } else {
        format_roster(group)
    };

    format!(
        "[GROUP CONTEXT]\n\
         群组ID: {}\n\
         会话ID: {}\n\
         主题: {}\n\
         {}\
         参与者:\n{}\n\
         {}\
         \n\
         {}\n\
         [/GROUP CONTEXT]\n\
         \n\
         你是: {}\n\
         你的角色: {}\n\
         \n\
         {}",
        group.id,
        session_id,
        topic,
        base_context,
        roster,
        task_line,
        routing_instruction,
        display_participant(recipient),
        role_slug(recipient.role),
        role_instruction,
    )
}

fn format_roster_with_mentions(group: &Group) -> String {
    let mut name_counts: HashMap<String, usize> = HashMap::new();
    for participant in group.participants.iter().filter(|participant| participant.is_bot()) {
        if let Some(name) = mentionable_name(participant) {
            *name_counts.entry(name).or_insert(0) += 1;
        }
    }

    group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            let display_name = participant
                .bot_name
                .as_deref()
                .map(str::trim)
                .filter(|name| !name.is_empty() && *name != participant.bot_uuid)
                .unwrap_or("-");
            let mention_name = mentionable_name(participant)
                .filter(|name| name_counts.get(name).copied().unwrap_or(0) == 1);
            let mention_hint = mention_name
                .map(|name| format!("@{} / @{}", name, participant.bot_uuid))
                .unwrap_or_else(|| format!("@{}", participant.bot_uuid));
            format!(
                "- 名称: {} | ID: {} | 角色: {} | 可@: {}",
                display_name,
                participant.bot_uuid,
                role_slug(participant.role),
                mention_hint
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn mentionable_name(participant: &Participant) -> Option<String> {
    let name = participant.bot_name.as_deref()?.trim();
    if name.is_empty() || name == participant.bot_uuid {
        return None;
    }
    if name.chars().all(is_mention_token_char) {
        Some(name.to_string())
    } else {
        None
    }
}

fn is_mention_token_char(ch: char) -> bool {
    ch == '_' || ch == ':' || ch.is_alphanumeric()
}

fn manager_worker_initial_message(
    group: &Group,
    session_id: &str,
    recipient: &Participant,
    context: Option<&str>,
    delivery_type: DeliveryType,
    bot_summaries: &HashMap<String, String>,
    task_input: Option<&str>,
    task_ledger: Option<&LedgerSummary>,
    coordination_surface: &CoordinationSurface,
) -> String {
    let is_manager = recipient.role == ParticipantRole::Manager;
    let context_line = if is_manager {
        context
            .filter(|ctx| !ctx.trim().is_empty())
            .map(|ctx| format!("\n{}\n", ctx.trim()))
            .unwrap_or_default()
    } else {
        String::new()
    };
    let task_line = if is_manager {
        task_input
            .filter(|task| !task.trim().is_empty())
            .map(|task| format!("\n[任务]\n{}\n[/任务]\n", task.trim()))
            .unwrap_or_default()
    } else {
        String::new()
    };
    let role_label = if is_manager { "manager" } else { "worker" };
    let status_line = if is_manager {
        task_ledger
            .map(format_ledger_status_line)
            .filter(|line| !line.is_empty())
            .map(|line| format!("\n{}", line))
            .unwrap_or_default()
    } else {
        String::new()
    };
    let instruction = manager_worker_coordination_instruction(
        is_manager,
        delivery_type,
        coordination_surface,
        &status_line,
    );

    format!(
        "[SERVICE GROUP CONTEXT]\n\
         群组ID: {}\n\
         会话ID: {}\n\
         模式: manager_worker\n\
         你的角色: {}\n\
         参与者:\n{}\n\
         {}\
         {}\
         {}\n\
         [/SERVICE GROUP CONTEXT]\n\
         \n\
         你是: {}\n\
         你的角色: {}",
        group.id,
        session_id,
        role_label,
        format_roster_with_role(group, bot_summaries),
        context_line,
        task_line,
        instruction,
        display_participant(recipient),
        role_label,
    )
}

fn manager_worker_coordination_instruction(
    is_manager: bool,
    delivery_type: DeliveryType,
    surface: &CoordinationSurface,
    status_line: &str,
) -> String {
    match surface.mode {
        CoordinationMode::McporterMcp => mcporter_mcp_instruction(is_manager, surface, status_line),
        CoordinationMode::NativeMcp => native_mcp_instruction(is_manager, surface, status_line),
        CoordinationMode::NativeTool => native_tool_instruction(is_manager, status_line),
        CoordinationMode::Disabled | CoordinationMode::LegacyUpstream => {
            legacy_manager_worker_instruction(delivery_type, status_line)
        }
    }
}

fn mcporter_mcp_instruction(
    is_manager: bool,
    surface: &CoordinationSurface,
    status_line: &str,
) -> String {
    let command = surface
        .mcporter_command
        .as_deref()
        .unwrap_or("mcporter");
    let server = surface.mcp_server.as_deref().unwrap_or("bcs");
    if is_manager {
        return format!(
            "\n[协同提醒] 本群为任务群，你是主 Bot。你当前平台通过 mcporter 调用 BCS MCP 工具。需要派发子任务时，使用 `{command} call {server}.bcs_assign_task target_bot=\"<目标Bot名称或ID>\" message=\"<任务内容>\"`；任务可以结束时，使用 `{command} call {server}.bcs_task_complete summary=\"<最终总结>\"`。不要直接调用原生发送工具来派发子任务，不要在普通回复中伪造工具结果。{}",
            status_line
        );
    }
    format!(
        "\n[协同提醒] 本群为任务群，你是子 Bot。你当前平台通过 mcporter 调用 BCS MCP 工具。收到主 Bot 派发的任务后，使用 `{command} call {server}.bcs_send_task_message message=\"<结果、进展、问题或阻塞>\"`。不要直接面向用户输出最终答案；最终汇总由 manager 完成，不要在普通回复中伪造工具结果。"
    )
}

fn native_mcp_instruction(
    is_manager: bool,
    surface: &CoordinationSurface,
    status_line: &str,
) -> String {
    let server = surface.mcp_server.as_deref().unwrap_or("bcs");
    if is_manager {
        return format!(
            "\n[协同提醒] 本群为任务群，你是主 Bot。你当前平台原生提供 BCS MCP 工具。需要派发子任务时，直接调用 MCP server `{server}` 上的 `bcs_assign_task`；任务可以结束时，直接调用 MCP server `{server}` 上的 `bcs_task_complete`。不要使用 mcporter、exec、bash，不要在普通回复中伪造工具结果。{}",
            status_line
        );
    }
    format!(
        "\n[协同提醒] 本群为任务群，你是子 Bot。你当前平台原生提供 BCS MCP 工具。收到 manager 派发的任务后，直接调用 MCP server `{server}` 上的 `bcs_send_task_message` 回传结果、进展、问题或阻塞。不要使用 mcporter、exec、bash，不要直接面向用户输出最终答案。"
    )
}

fn native_tool_instruction(is_manager: bool, status_line: &str) -> String {
    if is_manager {
        return format!(
            "\n[协同提醒] 本群为任务群，你是主 Bot。你当前平台原生提供 BCS 协同工具，这些工具是当前运行环境中的原生 tools，不是 MCP server 工具。需要派发子任务时，直接调用原生工具 `bcs_assign_task`；任务可以结束时，直接调用原生工具 `bcs_task_complete`。不要使用 mcporter、exec、bash，不要写 MCP server 名称，不要在普通回复中伪造工具结果。{}",
            status_line
        );
    }
    "\n[协同提醒] 本群为任务群，你是子 Bot。你当前平台原生提供 BCS 协同工具，这些工具是当前运行环境中的原生 tools，不是 MCP server 工具。收到 manager 派发的任务后，直接调用原生工具 `bcs_send_task_message` 回传结果、进展、问题或阻塞。不要使用 mcporter、exec、bash，不要写 MCP server 名称，不要直接面向用户输出最终答案。".to_string()
}

fn legacy_manager_worker_instruction(
    delivery_type: DeliveryType,
    status_line: &str,
) -> String {
    match delivery_type {
        DeliveryType::Send => format!(
            "\n[协同提醒] 本群为任务群，你是主 Bot。派发子任务用 bcs_assign_task(target_bot, message)，可并行派发多个；收齐所有子 Bot 回复、综合完毕后用 bcs_task_complete(summary) 收尾。不要用引擎自带的发送工具向群里发消息。{}",
            status_line
        ),
        DeliveryType::Inject => {
            "\n[协同提醒] 本群为任务群，你是子 Bot。收到主 Bot 派发的任务后直接处理并回复；需要阶段性同步进展 / 说明阻塞时，用 bcs_send_task_message(message) 发给主 Bot。不要用引擎自带的发送工具向群里发消息。".to_string()
        }
    }
}

fn format_ledger_status_line(summary: &LedgerSummary) -> String {
    if summary.pending.is_empty()
        && summary.replied.is_empty()
        && summary.failed.is_empty()
        && summary.timed_out.is_empty()
    {
        return String::new();
    }
    format!(
        "[任务状态] 待回复: {} | 已回复: {} | 失败: {} | 超时: {}",
        join_or_dash(&summary.pending),
        join_or_dash(&summary.replied),
        join_or_dash(&summary.failed),
        join_or_dash(&summary.timed_out),
    )
}

fn join_or_dash(items: &[String]) -> String {
    if items.is_empty() {
        "-".to_string()
    } else {
        items.join(", ")
    }
}

fn format_roster_with_role(group: &Group, bot_summaries: &HashMap<String, String>) -> String {
    group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            let role = if participant.role == ParticipantRole::Manager {
                "manager"
            } else {
                "worker"
            };
            let name = participant
                .bot_name
                .as_deref()
                .unwrap_or(&participant.bot_uuid);
            let summary = bot_summaries
                .get(&participant.bot_uuid)
                .map(|summary| format!(" — {}", summary))
                .unwrap_or_default();
            format!(
                "- 名称: {} | ID: {} | 角色: {}{}",
                name, participant.bot_uuid, role, summary
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn format_roster(group: &Group) -> String {
    group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .map(|participant| {
            format!(
                "- {} ({})",
                display_participant(participant),
                role_slug(participant.role)
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn display_participant(participant: &Participant) -> String {
    match participant.bot_name.as_deref() {
        Some(name) if !name.is_empty() && name != participant.bot_uuid => {
            format!("{}({})", name, participant.bot_uuid)
        }
        _ => participant.bot_uuid.clone(),
    }
}

fn role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}
