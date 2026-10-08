//! Human actor use-case contracts shared by delivery adapters and services.

use async_trait::async_trait;
use serde::Serialize;

/// Identity facts extracted by a delivery adapter for the current user.
#[derive(Debug, Clone, Default)]
pub struct CurrentHumanActorCommand {
    pub staff_no: Option<String>,
    pub nick_name: Option<String>,
}

/// Result for the `/me/repair-info` use case.
#[derive(Debug, Clone, Serialize)]
pub struct RepairHumanActorInfoResult {
    pub ok: bool,
    pub user_id: Option<String>,
    pub staff_no: Option<String>,
    pub nick_name: Option<String>,
    #[serde(rename = "actor_uuid")]
    pub human_id: Option<String>,
    pub previous_name: Option<String>,
    pub current_name: Option<String>,
    pub name_repaired: bool,
    pub skipped_reason: Option<&'static str>,
    pub error: Option<String>,
}

/// Result for the `/me/ensure-human` use case.
#[derive(Debug, Clone, Serialize)]
pub struct EnsureCurrentHumanActorResult {
    #[serde(rename = "actor_uuid")]
    pub human_id: String,
    pub human_created: bool,
    pub matched_bots: Vec<String>,
    pub edges_created: u32,
    pub edges_upgraded: u32,
    pub failed_bots: Vec<String>,
    pub errors: Vec<String>,
}

/// Error for the `/me/ensure-human` use case.
#[derive(Debug, Clone)]
pub enum EnsureCurrentHumanActorError {
    LoginRequired,
    InvalidStaffNo,
    EnsureHumanActorFailed(String),
    ListLegacyBotsForOwnerFailed(String),
    AllMatchedBotsFailed {
        failed_bots: Vec<String>,
        errors: Vec<String>,
    },
}

#[async_trait]
pub trait HumanActorService: Send + Sync {
    async fn repair_human_actor_info(
        &self,
        command: CurrentHumanActorCommand,
    ) -> RepairHumanActorInfoResult;

    async fn ensure_current_human_actor(
        &self,
        command: CurrentHumanActorCommand,
    ) -> Result<EnsureCurrentHumanActorResult, EnsureCurrentHumanActorError>;
}
