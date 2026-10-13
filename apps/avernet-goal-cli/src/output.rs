use serde::Serialize;
use serde_json::Value;

use crate::cli::OutputFormat;
use crate::error::AppError;
use crate::models::{CommandResult, TargetExecutionResult};

pub fn print_command_result(result: &CommandResult, format: OutputFormat) -> Result<(), AppError> {
    match format {
        OutputFormat::Json => print_json(result),
        OutputFormat::Text => {
            for item in &result.results {
                print_target_result(item);
            }
            Ok(())
        }
    }
}

pub fn print_value(value: &Value, format: OutputFormat) -> Result<(), AppError> {
    match format {
        OutputFormat::Json => print_json(value),
        OutputFormat::Text => {
            println!("{}", serde_json::to_string_pretty(value)?);
            Ok(())
        }
    }
}

pub fn print_json<T: Serialize>(value: &T) -> Result<(), AppError> {
    println!("{}", serde_json::to_string(value)?);
    Ok(())
}

fn print_target_result(result: &TargetExecutionResult) {
    let target = result.target.as_deref().unwrap_or("<backend-default>");
    if result.success {
        if let Some(task_id) = &result.task_id {
            println!("任务已提交: target={target} task_id={task_id}");
            if let Some(node_id) = &result.node_id {
                println!("  node_id={node_id}");
            }
        } else {
            println!(
                "会话已结束: target={target} session_id={}",
                result.session_id.as_deref().unwrap_or("")
            );
        }
        return;
    }
    if result.needs_action {
        println!(
            "需要继续澄清或确认: target={target} session_id={}",
            result.session_id.as_deref().unwrap_or("")
        );
        if let Some(intake) = &result.intake {
            for question in &intake.questions {
                println!("  - {question}");
            }
        }
        return;
    }
    eprintln!(
        "执行失败: target={target} code={} message={}",
        result
            .error_code
            .as_deref()
            .unwrap_or("GOAL_EXECUTION_FAILED"),
        result.error_message.as_deref().unwrap_or("unknown error")
    );
}
