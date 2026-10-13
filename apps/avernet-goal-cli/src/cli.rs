use std::path::PathBuf;

use clap::{Args, Parser, Subcommand, ValueEnum};

use crate::models::ExecutionMode;

#[derive(Debug, Clone, Copy, Default, ValueEnum, PartialEq, Eq)]
pub enum OutputFormat {
    #[default]
    Text,
    Json,
}

#[derive(Debug, Clone, Copy, Default, ValueEnum, PartialEq, Eq)]
pub enum ModeArg {
    #[default]
    Relay,
    Centralized,
}

impl From<ModeArg> for ExecutionMode {
    fn from(value: ModeArg) -> Self {
        match value {
            ModeArg::Relay => Self::Relay,
            ModeArg::Centralized => Self::Centralized,
        }
    }
}

#[derive(Debug, Parser)]
#[command(
    name = "avernet-goal-cli",
    version,
    about = "提交、澄清并执行 Avernet Goal",
    long_about = "面向用户和 Agent 的 Goal 入口。每个 Goal × target 会创建独立 Intake Session，并在显式确认后创建独立 Task。"
)]
pub struct Cli {
    /// 一句话任务目标。与子命令互斥。
    #[arg(value_name = "GOAL")]
    pub goal: Option<String>,

    /// A2A 权限代理；默认 human。bot:<ref> 不改变任务归属或执行者。
    #[arg(long = "from", default_value = "human", value_name = "human|bot:<ref>")]
    pub from: String,

    /// 目标 Bot；可重复。每个目标独立澄清并创建独立任务。
    #[arg(long = "to", value_name = "bot:<ref>")]
    pub targets: Vec<String>,

    /// 任务执行模式。
    #[arg(long, value_enum, default_value_t = ModeArg::Relay)]
    pub mode: ModeArg,

    /// 输出格式。JSON 模式下 stdout 只输出一个 JSON 对象。
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    pub output: OutputFormat,

    /// 单次读取状态，不等待输入，也不自动确认。
    #[arg(long)]
    pub non_interactive: bool,

    #[command(subcommand)]
    pub command: Option<Command>,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// 恢复一个 Intake Session，并进入与首次请求相同的澄清/确认控制器。
    Resume(SessionArgs),
    /// 查询任务执行图。
    Get(GetArgs),
    /// 批量文件 Schema、示例、校验与提交。
    Batch(BatchArgs),
}

#[derive(Debug, Args)]
pub struct SessionArgs {
    pub session_id: String,
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    pub output: OutputFormat,
    #[arg(long)]
    pub non_interactive: bool,
}

#[derive(Debug, Args)]
pub struct GetArgs {
    pub task_id: String,
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    pub output: OutputFormat,
}

#[derive(Debug, Args)]
pub struct BatchArgs {
    #[command(subcommand)]
    pub command: BatchCommand,
}

#[derive(Debug, Subcommand)]
pub enum BatchCommand {
    /// 输出 Batch 文件的 Draft 2020-12 JSON Schema。
    Schema(SchemaArgs),
    /// 输出一份可直接校验和提交的 Batch 示例。
    Example(ExampleArgs),
    /// 在产生任何网络副作用前校验完整 Batch 文件。
    Validate(BatchFileArgs),
    /// 将每个 Goal × target 展开为独立 Intake Session 并提交。
    Submit(BatchSubmitArgs),
}

#[derive(Debug, Args)]
pub struct SchemaArgs {
    #[arg(long, value_enum, default_value_t = DocumentFormat::Json)]
    pub output: DocumentFormat,
}

#[derive(Debug, Args)]
pub struct ExampleArgs {
    #[arg(long, value_enum, default_value_t = DocumentFormat::Yaml)]
    pub format: DocumentFormat,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum DocumentFormat {
    Json,
    Yaml,
}

#[derive(Debug, Args)]
pub struct BatchFileArgs {
    /// Batch YAML/JSON 文件；使用 - 从 stdin 读取。
    #[arg(long, value_name = "path|-")]
    pub file: PathBuf,
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    pub output: OutputFormat,
}

#[derive(Debug, Args)]
pub struct BatchSubmitArgs {
    /// Batch YAML/JSON 文件；使用 - 从 stdin 读取。
    #[arg(long, value_name = "path|-")]
    pub file: PathBuf,
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    pub output: OutputFormat,
    /// 不等待回复或确认；每个 Session 停留在服务端返回的当前状态。
    #[arg(long)]
    pub non_interactive: bool,
}
