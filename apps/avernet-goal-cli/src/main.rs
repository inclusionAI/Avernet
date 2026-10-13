mod api;
mod batch;
mod cli;
mod controller;
mod error;
mod models;
mod output;

use std::process::ExitCode as ProcessExitCode;

use api::GoalApi;
use api::client::ApiClient;
use batch::{BatchValidationResult, example, json_schema, read_batch_file, validate_and_expand};
use clap::{CommandFactory, Parser};
use cli::{BatchCommand, Cli, Command, DocumentFormat, OutputFormat};
use controller::{GoalRequest, resume_session, submit_goal};
use error::{AppError, ExitCode};
use models::{CommandResult, TargetExecutionResult};
use output::{print_command_result, print_json, print_value};
use serde::Serialize;
use serde_json::{Value, json};

#[tokio::main]
async fn main() -> ProcessExitCode {
    let cli = Cli::parse();
    let output = selected_output(&cli);
    match run(&cli).await {
        Ok(code) => ProcessExitCode::from(code as u8),
        Err(error) => {
            if matches!(output, OutputFormat::Json) {
                let payload = json!({
                    "success": false,
                    "error": {
                        "code": error_code(&error),
                        "message": error.to_string(),
                    }
                });
                let _ = print_json(&payload);
            } else {
                eprintln!("Error: {error}");
            }
            ProcessExitCode::from(error.exit_code() as u8)
        }
    }
}

async fn run(cli: &Cli) -> Result<ExitCode, AppError> {
    match &cli.command {
        Some(Command::Batch(args)) => run_batch(&args.command).await,
        Some(Command::Resume(args)) => {
            reject_root_goal_options(cli)?;
            let api = ApiClient::new().await?;
            let result = resume_session(&api, &args.session_id, args.non_interactive).await;
            let close_result = api.close().await;
            let item = result?;
            close_result?;
            let command_result = CommandResult {
                command: "resume".to_string(),
                success: item.success,
                results: vec![item],
            };
            print_command_result(&command_result, args.output)?;
            Ok(result_exit_code(&command_result.results))
        }
        Some(Command::Get(args)) => {
            reject_root_goal_options(cli)?;
            let api = ApiClient::new().await?;
            let result = api.get_task(&args.task_id).await;
            let close_result = api.close().await;
            let value = result?;
            close_result?;
            print_value(&value, args.output)?;
            Ok(ExitCode::Success)
        }
        None => run_goal(cli).await,
    }
}

async fn run_goal(cli: &Cli) -> Result<ExitCode, AppError> {
    let goal = cli.goal.as_ref().ok_or_else(|| {
        let mut command = Cli::command();
        let _ = command.print_help();
        AppError::InvalidInput("\nGOAL or a subcommand is required".to_string())
    })?;
    let targets = if cli.targets.is_empty() {
        vec![None]
    } else {
        cli.targets.iter().cloned().map(Some).collect()
    };
    let requests = targets
        .into_iter()
        .map(|target| GoalRequest {
            goal: goal.trim().to_string(),
            from_ref: cli.from.clone(),
            target,
            mode: cli.mode.into(),
        })
        .collect::<Vec<_>>();
    validate_requests(&requests)?;

    let api = ApiClient::new().await?;
    let mut results = Vec::with_capacity(requests.len());
    for request in requests {
        let goal = request.goal.clone();
        let target = request.target.clone();
        match submit_goal(&api, request, cli.non_interactive).await {
            Ok(result) => results.push(result),
            Err(error) => results.push(failed_result(goal, target, &error)),
        }
    }
    api.close().await?;
    let command_result = CommandResult {
        command: "submit".to_string(),
        success: results.iter().all(|result| result.success),
        results,
    };
    print_command_result(&command_result, cli.output)?;
    Ok(result_exit_code(&command_result.results))
}

async fn run_batch(command: &BatchCommand) -> Result<ExitCode, AppError> {
    match command {
        BatchCommand::Schema(args) => {
            print_document(&json_schema()?, args.output)?;
            Ok(ExitCode::Success)
        }
        BatchCommand::Example(args) => {
            print_serialized_document(&example(), args.format)?;
            Ok(ExitCode::Success)
        }
        BatchCommand::Validate(args) => {
            let file = read_batch_file(&args.file)?;
            let executions = validate_and_expand(&file)?;
            let result = BatchValidationResult {
                valid: true,
                goal_count: file.goals.len(),
                execution_count: executions.len(),
                executions,
            };
            match args.output {
                OutputFormat::Json => print_json(&result)?,
                OutputFormat::Text => println!(
                    "Batch 校验通过: goals={}, executions={}",
                    result.goal_count, result.execution_count
                ),
            }
            Ok(ExitCode::Success)
        }
        BatchCommand::Submit(args) => {
            let file = read_batch_file(&args.file)?;
            let units = validate_and_expand(&file)?;
            let api = ApiClient::new().await?;
            let mut results = Vec::with_capacity(units.len());
            for unit in units {
                let goal_id = unit.goal_id.clone();
                let goal = unit.goal.clone();
                let target = unit.target.clone();
                let request = GoalRequest {
                    goal: unit.goal,
                    from_ref: unit.from_ref,
                    target: unit.target,
                    mode: unit.mode,
                };
                let mut result = match submit_goal(&api, request, args.non_interactive).await {
                    Ok(result) => result,
                    Err(error) => failed_result(goal, target, &error),
                };
                result.goal_id = Some(goal_id);
                results.push(result);
            }
            api.close().await?;
            let command_result = CommandResult {
                command: "batch.submit".to_string(),
                success: results.iter().all(|result| result.success),
                results,
            };
            print_command_result(&command_result, args.output)?;
            Ok(result_exit_code(&command_result.results))
        }
    }
}

fn validate_requests(requests: &[GoalRequest]) -> Result<(), AppError> {
    let mut targets = std::collections::HashSet::new();
    for request in requests {
        if request.goal.trim().is_empty() {
            return Err(AppError::InvalidInput("goal must not be empty".to_string()));
        }
        batch::validate_from(&request.from_ref, "--from")?;
        if let Some(target) = &request.target {
            batch::validate_target(target, "--to")?;
            if !targets.insert(target) {
                return Err(AppError::InvalidInput(format!(
                    "DUPLICATE_TARGET: {target}"
                )));
            }
        }
    }
    Ok(())
}

fn reject_root_goal_options(cli: &Cli) -> Result<(), AppError> {
    if cli.goal.is_some()
        || cli.from != "human"
        || !cli.targets.is_empty()
        || cli.mode != cli::ModeArg::Relay
        || cli.output != OutputFormat::Text
        || cli.non_interactive
    {
        return Err(AppError::InvalidInput(
            "root Goal options cannot be combined with this subcommand; place subcommand options after the subcommand".to_string(),
        ));
    }
    Ok(())
}

fn failed_result(goal: String, target: Option<String>, error: &AppError) -> TargetExecutionResult {
    TargetExecutionResult::failed(goal, target, error_code(error), error.to_string())
}

fn result_exit_code(results: &[TargetExecutionResult]) -> ExitCode {
    let successes = results.iter().filter(|result| result.success).count();
    let pending = results.iter().filter(|result| result.needs_action).count();
    let failed = results.len().saturating_sub(successes + pending);
    if failed > 0 && (successes > 0 || pending > 0) {
        return ExitCode::PartialSuccess;
    }
    if pending > 0 {
        return ExitCode::ActionRequired;
    }
    if failed == 0 {
        return ExitCode::Success;
    }
    if results
        .iter()
        .all(|result| result.error_code.as_deref() == Some("UNAUTHORIZED"))
    {
        return ExitCode::Unauthorized;
    }
    if results
        .iter()
        .all(|result| result.error_code.as_deref() == Some("GATEWAY_UNAVAILABLE"))
    {
        return ExitCode::Unavailable;
    }
    ExitCode::ExecutionFailed
}

fn error_code(error: &AppError) -> &'static str {
    match error {
        AppError::InvalidInput(_) | AppError::Serialization(_) => "INVALID_INPUT",
        AppError::Unauthorized(_) => "UNAUTHORIZED",
        AppError::Unavailable(_) => "GATEWAY_UNAVAILABLE",
        AppError::Remote(_) | AppError::Io(_) => "GOAL_EXECUTION_FAILED",
    }
}

fn selected_output(cli: &Cli) -> OutputFormat {
    match &cli.command {
        Some(Command::Resume(args)) => args.output,
        Some(Command::Get(args)) => args.output,
        Some(Command::Batch(args)) => match &args.command {
            BatchCommand::Validate(args) => args.output,
            BatchCommand::Submit(args) => args.output,
            BatchCommand::Schema(_) | BatchCommand::Example(_) => OutputFormat::Text,
        },
        None => cli.output,
    }
}

fn print_document(value: &Value, format: DocumentFormat) -> Result<(), AppError> {
    match format {
        DocumentFormat::Json => {
            println!("{}", serde_json::to_string_pretty(value)?);
        }
        DocumentFormat::Yaml => {
            print!("{}", serde_yaml::to_string(value)?);
        }
    }
    Ok(())
}

fn print_serialized_document<T: Serialize>(
    value: &T,
    format: DocumentFormat,
) -> Result<(), AppError> {
    match format {
        DocumentFormat::Json => println!("{}", serde_json::to_string_pretty(value)?),
        DocumentFormat::Yaml => print!("{}", serde_yaml::to_string(value)?),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use models::ExecutionMode;

    #[test]
    fn repeated_target_is_rejected_before_network_use() {
        let request = || GoalRequest {
            goal: "分析支付失败率".to_string(),
            from_ref: "human".to_string(),
            target: Some("bot:risk".to_string()),
            mode: ExecutionMode::Relay,
        };
        let error = validate_requests(&[request(), request()]).expect_err("duplicate target");
        assert!(error.to_string().contains("DUPLICATE_TARGET"));
    }
}
