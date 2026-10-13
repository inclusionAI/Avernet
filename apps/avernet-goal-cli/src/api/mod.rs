pub mod client;

use async_trait::async_trait;
use serde_json::Value;

use crate::error::AppError;
use crate::models::{TaskIntakeCommand, TaskIntakeResult, TaskOpResult};

#[async_trait(?Send)]
pub trait GoalApi {
    async fn intake(
        &self,
        command: &TaskIntakeCommand,
        session_id: Option<&str>,
    ) -> Result<TaskIntakeResult, AppError>;

    async fn start(&self, session_id: &str) -> Result<TaskOpResult, AppError>;

    async fn get_task(&self, task_id: &str) -> Result<Value, AppError>;
}
