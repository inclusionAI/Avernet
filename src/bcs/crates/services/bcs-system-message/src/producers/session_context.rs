//! Producer for `SystemMessageEventKind::SessionContext`.
//!
//! When a session is created this producer generates the initial
//! `<GroupContext>` message delivered to all bot participants, with
//! `chat.send` for the driver/manager and `chat.inject` for other
//! participants. The driver's delivery can be overridden to `chat.inject`
//! via the event's `driver_delivery` field (except in ManagerWorker groups,
//! which always deliver to the manager via `chat.send`).
//!
//! Free-chat (`Chat`) and manager-worker (`ManagerWorker`) groups share the
//! same `<GroupContext>` shell and section order; the mode label and a few
//! sections differ (free-chat has `## 工具说明` + `## 说明`; manager-worker
//! has `## manager-worker 协同说明`).

use async_trait::async_trait;
use bcs_domain::{
    CoordinationMode, CoordinationSurface, DeliveryType, Group, GroupStrategy, LedgerSummary,
    Participant, ParticipantRole, PersistMode, SystemMessageEvent, SystemMessageEventKind,
    SystemGroupMessage,
};
use bcs_service_api::{BotRegistryCoreService, SystemMessageProducerService, backfill_bot_names};

/// Lead line for the `<GroupContext>` block of a freshly created session.
const CURRENT_IN_OPENING: &str = "当前你在 bcn 群聊中，群聊相关信息如下";

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
    ) -> (Vec<SystemGroupMessage>, Option<String>) {
        let SystemMessageEvent::SessionContext {
            session_id,
            reason,
            session_input,
            task_ledger,
            driver_delivery,
            ..
        } = event
        else {
            return (vec![], None);
        };

        let mut render_group = group.clone();
        render_group.participants = participants.to_vec();
        backfill_bot_names(registry, &mut render_group).await;

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
            let is_manager_worker = render_group.group_strategy == GroupStrategy::ManagerWorker;
            let delivery_type = if is_driver {
                // ManagerWorker groups intentionally ignore the
                // `driver_delivery` (group_context_delivery) override: the
                // manager is expected to actively pick up and dispatch the
                // task, so its context is always delivered via `chat.send`.
                if is_manager_worker {
                    DeliveryType::Send
                } else {
                    driver_delivery.unwrap_or(DeliveryType::Send)
                }
            } else {
                DeliveryType::Inject
            };

            let context_message = if is_manager_worker {
                let coordination_surface = registry
                    .resolve_coordination_surface(&participant.bot_uuid)
                    .await
                    .unwrap_or_else(|_| CoordinationSurface::legacy_upstream());
                manager_worker_initial_message(
                    &render_group,
                    session_id,
                    reason,
                    participant,
                    delivery_type,
                    task_input_text.as_deref(),
                    task_ledger.as_ref(),
                    &coordination_surface,
                )
            } else {
                initial_group_context_message(
                    &render_group,
                    session_id,
                    reason,
                    participant,
                    delivery_type,
                    has_provider_downlink_bot,
                    task_input_text.as_deref(),
                )
            };

            messages.push(SystemGroupMessage {
                recipients: vec![participant.bot_uuid.clone()],
                message: context_message,
                delivery_type,
                // In manager-worker groups the manager's messages are public
                // history, so keep its initial context on the same visibility
                // boundary for human viewers. Worker contexts remain private
                // because they contain recipient-specific instructions.
                persist: if is_manager_worker && participant.role == ParticipantRole::Manager {
                    PersistMode::Public
                } else {
                    PersistMode::PerRecipient
                },
            });
        }
        // SessionContext does not emit a user-facing WS message. Bot contexts
        // are delivered per recipient; persistence follows the visibility
        // policy above, without a separate frontend broadcast.
        let user_message: Option<String> = None;
        (messages, user_message)
    }
}

pub(super) async fn contains_provider_downlink_bot(
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

/// Renders the unified free-chat `<GroupContext>` block for one recipient.
fn initial_group_context_message(
    group: &Group,
    session_id: &str,
    topic: &str,
    recipient: &Participant,
    delivery_type: DeliveryType,
    use_at_mention_routing: bool,
    task_input: Option<&str>,
) -> String {
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
    let tool_kind = if use_at_mention_routing {
        "@mention"
    } else {
        "bcs_route"
    };

    let sections = vec![
        group_info_section(
            group,
            Some(session_id),
            Some(topic).filter(|t| !t.is_empty()),
            recipient,
            "自由聊天",
        ),
        format!("## 参与者:\n{}", participant_table(group)),
        format!(
            "## 工具说明 ({})\n{}",
            tool_kind,
            routing_instruction_block(use_at_mention_routing)
        ),
        skill_section(),
        task_section(task_input),
        format!("## 说明\n{}", role_instruction),
    ];
    render_group_context(CURRENT_IN_OPENING, &sections)
}

/// Renders the unified manager-worker `<GroupContext>` block for one recipient.
fn manager_worker_initial_message(
    group: &Group,
    session_id: &str,
    topic: &str,
    recipient: &Participant,
    delivery_type: DeliveryType,
    task_input: Option<&str>,
    task_ledger: Option<&LedgerSummary>,
    coordination_surface: &CoordinationSurface,
) -> String {
    let is_manager = recipient.role == ParticipantRole::Manager;
    let status_line = if is_manager {
        mw_status_block(task_ledger)
    } else {
        String::new()
    };
    let instruction = manager_worker_coordination_instruction(
        is_manager,
        delivery_type,
        coordination_surface,
        &status_line,
    );
    // `## 任务说明` is shown only to the manager; workers receive only the
    // coordination instruction.
    let task = if is_manager { task_input } else { None };

    let sections = vec![
        group_info_section(
            group,
            Some(session_id),
            Some(topic).filter(|t| !t.is_empty()),
            recipient,
            "manager_worker",
        ),
        format!("## 参与者:\n{}", participant_table(group)),
        skill_section(),
        task_section(task),
        format!("## manager-worker 协同说明\n{}", instruction),
    ];
    render_group_context(CURRENT_IN_OPENING, &sections)
}

/// Wraps non-empty section bodies in the `<GroupContext>` shell, joining
/// sections with a blank line and placing a single newline before the close tag.
/// `opening` is the lead line after `<GroupContext>` (it differs between a new
/// session and a bot joining an existing group).
pub(super) fn render_group_context(opening: &str, sections: &[String]) -> String {
    let body = sections
        .iter()
        .filter(|section| !section.is_empty())
        .map(String::as_str)
        .collect::<Vec<_>>()
        .join("\n\n");
    format!("<GroupContext>\n{}\n\n{}\n</GroupContext>", opening, body)
}

/// Renders the `## 群聊信息` section shared by both group modes. `session_id`
/// and `topic` are omitted from the block when `None` (e.g. a bot joining a
/// group has no session context attached to the event).
pub(super) fn group_info_section(
    group: &Group,
    session_id: Option<&str>,
    topic: Option<&str>,
    recipient: &Participant,
    mode: &str,
) -> String {
    let mut lines = vec![
        "## 群聊信息".to_string(),
        format!("* 群组ID: {}", group.id),
    ];
    if let Some(sid) = session_id {
        lines.push(format!("* 会话ID: {}", sid));
    }
    lines.push(format!("* 群聊名称: {}", group_display_name(group)));
    if let Some(topic) = topic {
        lines.push(format!("* 目标: {}", topic));
    }
    lines.push(format!("* 模式: {}", mode));
    lines.push(format!("* 你的身份: {}", display_participant(recipient)));
    lines.push(format!("* 你的角色: {}", role_slug(recipient.role)));
    lines.join("\n")
}

/// Group display name: `group.label` with any `"Group: "` prefix stripped;
/// falls back to the group id when no label is set.
pub(super) fn group_display_name(group: &Group) -> String {
    group
        .label
        .as_deref()
        .map(|label| label.strip_prefix("Group: ").unwrap_or(label).trim().to_string())
        .filter(|label| !label.is_empty())
        .unwrap_or_else(|| group.id.clone())
}

/// Renders the `## 参与者:` section as a 3-column markdown table
/// (`name | bot_id | role`).
pub(super) fn participant_table(group: &Group) -> String {
    let mut rows = vec![
        "| name | bot_id | role |".to_string(),
        "|------|--------|------|".to_string(),
    ];
    for participant in group.participants.iter().filter(|p| p.is_bot()) {
        let name = participant
            .bot_name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty() && *name != participant.bot_uuid)
            .unwrap_or("-");
        rows.push(format!(
            "|{}|{}|{}|",
            name,
            participant.bot_uuid,
            role_slug(participant.role)
        ));
    }
    rows.join("\n")
}

/// Renders the `## 相关 SKILL` section shared by both group modes.
pub(super) fn skill_section() -> String {
    "## 相关 SKILL\nbcn 群聊相关操作可以参考 `bcs-coordination` 技能。".to_string()
}

/// Renders the `## 任务说明` section, or an empty string when `task_input`
/// is missing/blank.
pub(super) fn task_section(task_input: Option<&str>) -> String {
    task_input
        .filter(|task| !task.trim().is_empty())
        .map(|task| format!("## 任务说明\n{}", task.trim()))
        .unwrap_or_default()
}

/// Renders the routing instruction text (either `@mention` or `bcs_route`
/// variant) used inside the `## 工具说明` section of free-chat groups.
pub(super) fn routing_instruction_block(use_at_mention_routing: bool) -> &'static str {
    if use_at_mention_routing {
        "消息中任何 @ 标识都会触发路由，让被 @ 的 Bot 收到消息并被要求响应。\n\
         只有希望某个 Bot 响应时才使用 @，不要用 @ 表示引用、收到或转述某个 Bot 的消息。\n\
         优先使用名称；名称为空、重复或不确定时，使用 Bot ID。"
    } else {
        r#"使用 `bcs_route` 工具替代 @mention 指定下一个响应者可以提高路由准确率。
* to: 目标 Bot 列表，支持按名称或 bot_id 选择
  - 按名称: {"type": "name", "value": "DBA"}
  - 按ID: {"type": "bot", "value": "bot_54123f4f"}
* reason: 路由原因"#
    }
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
            "本群为任务群，你是主 Bot。你当前平台通过 mcporter 调用 BCS MCP 工具。需要派发子任务时，使用 `{command} call {server}.bcs_assign_task target_bot=\"<目标Bot名称或ID>\" message=\"<任务内容>\"`；任务可以结束时，使用 `{command} call {server}.bcs_task_complete summary=\"<最终总结>\"`。不要直接调用原生发送工具来派发子任务，不要在普通回复中伪造工具结果。{}",
            status_line
        );
    }
    format!(
        "本群为任务群，你是子 Bot。你当前平台通过 mcporter 调用 BCS MCP 工具。收到主 Bot 派发的任务后，使用 `{command} call {server}.bcs_send_task_message message=\"<结果、进展、问题或阻塞>\"`。不要直接面向用户输出最终答案；最终汇总由 manager 完成，不要在普通回复中伪造工具结果。"
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
            "本群为任务群，你是主 Bot。你当前平台原生提供 BCS MCP 工具。需要派发子任务时，直接调用 MCP server `{server}` 上的 `bcs_assign_task`；任务可以结束时，直接调用 MCP server `{server}` 上的 `bcs_task_complete`。不要使用 mcporter、exec、bash，不要在普通回复中伪造工具结果。{}",
            status_line
        );
    }
    format!(
        "本群为任务群，你是子 Bot。你当前平台原生提供 BCS MCP 工具。收到 manager 派发的任务后，直接调用 MCP server `{server}` 上的 `bcs_send_task_message` 回传结果、进展、问题或阻塞。不要使用 mcporter、exec、bash，不要直接面向用户输出最终答案。"
    )
}

fn native_tool_instruction(is_manager: bool, status_line: &str) -> String {
    if is_manager {
        return format!(
            "本群为任务群，你是主 Bot。你当前平台原生提供 BCS 协同工具，这些工具是当前运行环境中的原生 tools，不是 MCP server 工具。需要派发子任务时，直接调用原生工具 `bcs_assign_task`；任务可以结束时，直接调用原生工具 `bcs_task_complete`。不要使用 mcporter、exec、bash，不要写 MCP server 名称，不要在普通回复中伪造工具结果。{}",
            status_line
        );
    }
    "本群为任务群，你是子 Bot。你当前平台原生提供 BCS 协同工具，这些工具是当前运行环境中的原生 tools，不是 MCP server 工具。收到 manager 派发的任务后，直接调用原生工具 `bcs_send_task_message` 回传结果、进展、问题或阻塞。不要使用 mcporter、exec、bash，不要写 MCP server 名称，不要直接面向用户输出最终答案。".to_string()
}

fn legacy_manager_worker_instruction(delivery_type: DeliveryType, status_line: &str) -> String {
    match delivery_type {
        DeliveryType::Send => format!(
            "本群为任务群，你是主 Bot。派发子任务用 bcs_assign_task(target_bot, message)，可并行派发多个；收齐所有子 Bot 回复、综合完毕后用 bcs_task_complete(summary) 收尾。不要用引擎自带的发送工具向群里发消息。{}",
            status_line
        ),
        DeliveryType::Inject => {
            "本群为任务群，你是子 Bot。收到主 Bot 派发的任务后直接处理并回复；需要阶段性同步进展 / 说明阻塞时，用 bcs_send_task_message(message) 发给主 Bot。不要用引擎自带的发送工具向群里发消息。".to_string()
        }
    }
}

/// Renders the `[任务状态]` line for manager-worker manager context via
/// `format_ledger_status_line`: non-empty → `\n{line}`, else `""`.
fn mw_status_block(task_ledger: Option<&LedgerSummary>) -> String {
    task_ledger
        .map(format_ledger_status_line)
        .filter(|line| !line.is_empty())
        .map(|line| format!("\n{}", line))
        .unwrap_or_default()
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

pub(super) fn display_participant(participant: &Participant) -> String {
    match participant.bot_name.as_deref() {
        Some(name) if !name.is_empty() && name != participant.bot_uuid => {
            format!("{}({})", name, participant.bot_uuid)
        }
        _ => participant.bot_uuid.clone(),
    }
}

pub(super) fn role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}
