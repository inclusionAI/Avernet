use std::io::{self, Write};

use crate::api::GoalApi;
use crate::batch::{validate_from, validate_target};
use crate::error::AppError;
use crate::models::{
    ExecutionMode, IntakeAction, IntakeStatus, IntakeTarget, IntakeTargetType,
    TargetExecutionResult, TaskIntakeCommand, TaskIntakeResult,
};

#[derive(Debug, Clone)]
pub struct GoalRequest {
    pub goal: String,
    pub from_ref: String,
    pub target: Option<String>,
    pub mode: ExecutionMode,
}

pub async fn submit_goal<A: GoalApi>(
    api: &A,
    request: GoalRequest,
    non_interactive: bool,
) -> Result<TargetExecutionResult, AppError> {
    validate_goal_request(&request)?;
    let proxy = request.from_ref.strip_prefix("bot:").map(str::to_string);
    let target = request.target.as_deref().map(|value| IntakeTarget {
        target_type: IntakeTargetType::Bot,
        target_id: value.trim_start_matches("bot:").to_string(),
    });
    let result = api
        .intake(
            &TaskIntakeCommand {
                action: IntakeAction::Start,
                message: Some(request.goal.clone()),
                requested_a2a_proxy_bot_ref: proxy,
                requested_target: target,
                requested_mode: Some(request.mode),
            },
            None,
        )
        .await?;
    drive_session(api, request.goal, request.target, result, non_interactive).await
}

pub async fn resume_session<A: GoalApi>(
    api: &A,
    session_id: &str,
    non_interactive: bool,
) -> Result<TargetExecutionResult, AppError> {
    if session_id.trim().is_empty() {
        return Err(AppError::InvalidInput(
            "session_id must not be empty".to_string(),
        ));
    }
    let result = api
        .intake(
            &TaskIntakeCommand {
                action: IntakeAction::Resume,
                message: None,
                requested_a2a_proxy_bot_ref: None,
                requested_target: None,
                requested_mode: None,
            },
            Some(session_id),
        )
        .await?;
    let goal = result
        .task_spec
        .as_ref()
        .map(|spec| spec.goal.objective.clone())
        .unwrap_or_default();
    let target = result.target.as_ref().map(|value| value.target_id.clone());
    drive_session(api, goal, target, result, non_interactive).await
}

async fn drive_session<A: GoalApi>(
    api: &A,
    goal: String,
    target: Option<String>,
    mut intake: TaskIntakeResult,
    non_interactive: bool,
) -> Result<TargetExecutionResult, AppError> {
    loop {
        if intake.processing {
            return Ok(TargetExecutionResult::from_intake(goal, target, intake));
        }
        match intake.status {
            IntakeStatus::Confirmed => {
                let started = api.start(&intake.session_id).await?;
                let mut result = TargetExecutionResult::from_intake(goal, target, intake);
                result.success = started.success;
                result.needs_action = false;
                result.task_id = Some(started.task_id);
                result.node_id = started.node_id;
                result.error_message = started.message;
                if !result.success {
                    result.error_code = Some("GOAL_START_FAILED".to_string());
                }
                return Ok(result);
            }
            IntakeStatus::Cancelled => {
                let mut result = TargetExecutionResult::from_intake(goal, target, intake);
                result.success = true;
                result.needs_action = false;
                return Ok(result);
            }
            IntakeStatus::Clarifying if non_interactive => {
                return Ok(TargetExecutionResult::from_intake(goal, target, intake));
            }
            IntakeStatus::Clarifying => {
                let action = prompt_next_action(&intake)?;
                intake = api.intake(&action, Some(&intake.session_id)).await?;
            }
        }
    }
}

fn prompt_next_action(result: &TaskIntakeResult) -> Result<TaskIntakeCommand, AppError> {
    render_intake_prompt(result)?;
    if result.task_spec.is_some() && result.missing_fields.is_empty() {
        loop {
            eprint!("确认提交任务？[confirm/cancel]: ");
            io::stderr().flush()?;
            let answer = read_line()?;
            match answer.trim().to_ascii_lowercase().as_str() {
                "confirm" | "y" | "yes" | "确认" | "确认提交" => {
                    return Ok(session_command(IntakeAction::Confirm, None));
                }
                "cancel" | "c" | "取消" => {
                    return Ok(session_command(IntakeAction::Cancel, None));
                }
                _ => eprintln!("请输入 confirm 或 cancel。"),
            }
        }
    }

    eprint!("补充信息: ");
    io::stderr().flush()?;
    let message = read_line()?;
    if message.trim().is_empty() {
        return Err(AppError::InvalidInput(
            "clarification reply must not be empty".to_string(),
        ));
    }
    Ok(session_command(
        IntakeAction::Continue,
        Some(message.trim().to_string()),
    ))
}

fn render_intake_prompt(result: &TaskIntakeResult) -> Result<(), AppError> {
    eprintln!("\nIntake Session: {}", result.session_id);
    if let Some(spec) = &result.task_spec {
        eprintln!("标题: {}", spec.context.title);
        eprintln!("目标: {}", spec.goal.objective);
        if !spec.goal.acceptances.is_empty() {
            eprintln!("验收标准:");
            for item in &spec.goal.acceptances {
                eprintln!("  - {}", item.description);
            }
        }
    }
    if !result.questions.is_empty() {
        eprintln!("待澄清:");
        for (index, question) in result.questions.iter().enumerate() {
            eprintln!("  {}. {}", index + 1, question);
        }
    } else if !result.missing_fields.is_empty() {
        eprintln!("缺少字段: {}", result.missing_fields.join(", "));
    }
    Ok(())
}

fn read_line() -> Result<String, AppError> {
    let mut value = String::new();
    io::stdin().read_line(&mut value)?;
    Ok(value)
}

fn session_command(action: IntakeAction, message: Option<String>) -> TaskIntakeCommand {
    TaskIntakeCommand {
        action,
        message,
        requested_a2a_proxy_bot_ref: None,
        requested_target: None,
        requested_mode: None,
    }
}

fn validate_goal_request(request: &GoalRequest) -> Result<(), AppError> {
    if request.goal.trim().is_empty() {
        return Err(AppError::InvalidInput("goal must not be empty".to_string()));
    }
    validate_from(&request.from_ref, "--from")?;
    if let Some(target) = &request.target {
        validate_target(target, "--to")?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;

    use async_trait::async_trait;
    use serde_json::json;

    use super::*;
    use crate::models::TaskOpResult;

    struct FakeApi {
        calls: RefCell<Vec<IntakeAction>>,
        intake_result: TaskIntakeResult,
    }

    #[async_trait(?Send)]
    impl GoalApi for FakeApi {
        async fn intake(
            &self,
            command: &TaskIntakeCommand,
            _session_id: Option<&str>,
        ) -> Result<TaskIntakeResult, AppError> {
            self.calls.borrow_mut().push(command.action.clone());
            Ok(self.intake_result.clone())
        }

        async fn start(&self, _session_id: &str) -> Result<TaskOpResult, AppError> {
            panic!("non-interactive clarification must not start")
        }

        async fn get_task(&self, _task_id: &str) -> Result<serde_json::Value, AppError> {
            Ok(json!({}))
        }
    }

    fn clarifying_result() -> TaskIntakeResult {
        TaskIntakeResult {
            session_id: "tis-1".to_string(),
            owner_user_id: "u1".to_string(),
            source_type: "api".to_string(),
            status: IntakeStatus::Clarifying,
            target: None,
            execution_mode: ExecutionMode::Relay,
            processing: false,
            task_spec: None,
            missing_fields: vec!["deliverables".to_string()],
            questions: vec!["交付物是什么？".to_string()],
            create_time_ms: 1,
            update_time_ms: 1,
        }
    }

    #[tokio::test]
    async fn non_interactive_stops_without_confirming() {
        let api = FakeApi {
            calls: RefCell::new(Vec::new()),
            intake_result: clarifying_result(),
        };
        let result = submit_goal(
            &api,
            GoalRequest {
                goal: "分析失败率".to_string(),
                from_ref: "human".to_string(),
                target: None,
                mode: ExecutionMode::Relay,
            },
            true,
        )
        .await
        .expect("submit");
        assert!(result.needs_action);
        assert_eq!(*api.calls.borrow(), vec![IntakeAction::Start]);
    }

    #[tokio::test]
    async fn processing_a2a_run_returns_recoverable_session_without_prompting() {
        let mut pending = clarifying_result();
        pending.processing = true;
        let api = FakeApi {
            calls: RefCell::new(Vec::new()),
            intake_result: pending,
        };
        let result = resume_session(&api, "tis-1", false).await.expect("resume");
        assert!(result.needs_action);
        assert_eq!(result.session_id.as_deref(), Some("tis-1"));
        assert_eq!(
            result.error_code.as_deref(),
            Some("TASK_A2A_STATUS_UNKNOWN")
        );
        assert_eq!(*api.calls.borrow(), vec![IntakeAction::Resume]);
    }
}
