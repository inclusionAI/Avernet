//! A2A chat use-case contracts.

use async_trait::async_trait;
use serde_json::Value;

use crate::core::ServiceResult;

use super::principal::CallerContext;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct A2aRunStatus {
    pub run_id: String,
    pub status: String,
    pub response: Option<Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ChatResponseMode {
    Full,
    AfterLastToolCall,
}

impl Default for ChatResponseMode {
    fn default() -> Self {
        Self::Full
    }
}

#[derive(Debug, Clone)]
pub struct A2aChatCommand {
    pub caller: CallerContext,
    pub target_bot_id: String,
    pub message: String,
    pub from_actor_id: Option<String>,
    pub authenticated_staff_id: Option<String>,
    pub run_id: Option<String>,
    pub async_mode: bool,
    pub session_key: Option<String>,
    pub timeout_ms: Option<u64>,
    pub client: Option<String>,
    pub tags: Vec<String>,
    pub response_mode: ChatResponseMode,
    pub caller_wait_mode: Option<String>,
    pub organization_code: Option<String>,
}

#[derive(Debug, Clone)]
pub struct A2aChatOutcome {
    pub run_id: String,
    pub status: String,
    pub response: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct BlockingA2aChatCommand {
    pub caller: CallerContext,
    pub target_bot_id: String,
    pub message: String,
    pub from_actor_id: Option<String>,
    /// Metadata used only when registering the response event channel.
    /// Legacy HTTP chat defaults the delivered frame sender to "user" when
    /// omitted, but leaves this channel metadata absent.
    pub run_channel_from: Option<String>,
    pub authenticated_staff_id: Option<String>,
    pub run_id: String,
    pub session_key: String,
    pub timeout_ms: u64,
    pub client: Option<String>,
    pub tags: Vec<String>,
    pub response_mode: ChatResponseMode,
    pub organization_code: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct BlockingA2aChatOutcome {
    pub delivered: bool,
    pub bot_uuid: String,
    pub session_id: String,
    pub content: String,
}

#[derive(Debug, Clone)]
pub struct AsyncA2aChatCommand {
    pub caller: CallerContext,
    pub target_bot_id: String,
    pub message: String,
    pub from_actor_id: Option<String>,
    /// Metadata used only when registering the response event channel.
    /// Legacy HTTP chat defaults the delivered frame sender to "user" when
    /// omitted, but leaves this channel metadata absent.
    pub run_channel_from: Option<String>,
    pub authenticated_staff_id: Option<String>,
    pub run_id: String,
    pub session_key: String,
    pub timeout_ms: u64,
    pub client: Option<String>,
    pub tags: Vec<String>,
    pub response_mode: ChatResponseMode,
    pub caller_wait_mode: Option<String>,
    pub organization_code: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AsyncA2aChatAccepted {
    pub run_id: String,
    pub bot_uuid: String,
    pub session_id: String,
    pub status: String,
    pub expires_at_ms: u64,
}

#[derive(Debug, Clone)]
pub struct ChatRunQueryCommand {
    pub caller: CallerContext,
    pub run_id: String,
    pub wait_ms: u64,
    pub since_version: u64,
}

#[derive(Debug, Clone)]
pub struct ChatRunCancelCommand {
    pub caller: CallerContext,
    pub run_id: String,
}

#[async_trait]
pub trait A2aChatService: Send + Sync {
    async fn chat(&self, cmd: A2aChatCommand) -> ServiceResult<A2aChatOutcome>;
    async fn get_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus>;
    async fn wait_run(
        &self,
        caller: CallerContext,
        run_id: &str,
        since_version: u64,
        wait_ms: u64,
    ) -> ServiceResult<A2aRunStatus>;
    async fn record_run_event(&self, run_id: &str, event_json: &str) -> ServiceResult<bool>;
    async fn fail_run_if_open(&self, run_id: &str, error: &str) -> ServiceResult<bool>;
    async fn cancel_run(&self, caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus>;
    async fn cleanup_expired(
        &self,
        now_ms: u64,
        retention_ms: u64,
    ) -> ServiceResult<(Vec<String>, Vec<String>)>;
}

#[async_trait]
pub trait A2aChatRunService: Send + Sync {
    async fn run_blocking_chat(
        &self,
        cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome>;

    async fn start_async_chat(
        &self,
        cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted>;

    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus>;

    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus>;
}
