use bcs_domain::HumanInputNotificationMode;
use bcs_service_api::{HumanInputReadyEvent, ServiceError, ServiceResult};

/// Render once, before enqueue. Queue activation and event replay use the
/// saved notification_text; this layer never loads a Definition or parses IDs.
pub(super) fn render(event: &HumanInputReadyEvent) -> ServiceResult<String> {
    let mut sections = vec![format!("【待你处理】{}", event.display_name), event.instruction.clone()];
    if let Some(context) = &event.loop_context {
        let invalid = || ServiceError::InvalidOperation {
            message: "HumanInput notification has invalid Loop context".into(),
            request_id: Some(event.event_id.clone()),
        };
        if context.loop_id.trim().is_empty() || context.iteration == 0
            || context.iteration > context.max_iterations
        {
            return Err(invalid());
        }
        let previous = match (&context.previous_result, context.iteration) {
            (None, 1) => "本轮没有上一轮结果。".to_string(),
            (Some(previous), iteration) if iteration > 1
                && previous.iteration == iteration - 1
                && !previous.result_node_id.trim().is_empty()
                && !previous.execution_node_id.trim().is_empty()
                && !previous.outcome.trim().is_empty() => {
                match event.notification_mode {
                    HumanInputNotificationMode::DirectAssignee => format!(
                        "上一轮结果（第 {} 轮）：\n节点：{}\n执行节点：{}\n结果：{}\n完成时间（Unix ms）：{}\n输出：\n{}",
                        previous.iteration, previous.result_node_id, previous.execution_node_id,
                        previous.outcome, previous.completed_at, previous.output,
                    ),
                    // The shared-group contract does not grant access to private
                    // artifacts. Do not expose outcome or result identities either.
                    HumanInputNotificationMode::FixedGroup =>
                        "上一轮结果未在共享群展示，请在 Workbench 人工处理界面查看。".into(),
                }
            }
            _ => return Err(invalid()),
        };
        sections.push(format!("【Loop 上下文】\nLoop：{}\n轮次：{}/{}\n\n{previous}",
            context.loop_id, context.iteration, context.max_iterations));
    }
    if event.notification_mode == HumanInputNotificationMode::DirectAssignee {
        let previous_id = event.loop_context.as_ref().and_then(|context| context.previous_result.as_ref())
            .map(|previous| previous.execution_node_id.as_str());
        let artifacts = event.upstream_artifacts.iter()
            .filter(|artifact| Some(artifact.node_id.as_str()) != previous_id)
            .map(|artifact| format!("[{}]\n{}", artifact.node_id, artifact.text))
            .collect::<Vec<_>>();
        if !artifacts.is_empty() { sections.push(format!("上游结果：\n{}", artifacts.join("\n\n"))); }
    }
    if !event.judge_outcomes.is_empty() {
        sections.push(format!("可识别结果：{}", event.judge_outcomes.join(" / ")));
    }
    if let Some(deadline_ms) = event.timeout_deadline_ms {
        sections.push(format!("等待截止时间（Unix ms）：{deadline_ms}"));
    }
    sections.push(match event.notification_mode {
        HumanInputNotificationMode::FixedGroup => "请直接 @ 机器人回复。".into(),
        HumanInputNotificationMode::DirectAssignee => "请直接回复本会话。".into(),
    });
    Ok(sections.join("\n\n"))
}
