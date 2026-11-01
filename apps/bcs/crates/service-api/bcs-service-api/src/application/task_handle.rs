//! Global retained-task handles. These are correlation identifiers, not credentials.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TaskHandleStatus {
    Queued, Running, Completed, Failed, Cancelled, TimedOut, Unknown, NotFound, Ambiguous,
    AlreadyFinished, CancelFailed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskHandleView {
    #[serde(rename = "ref")]
    pub task_ref: String,
    pub status: TaskHandleStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub running_s: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idle_s: Option<u64>,
}
impl TaskHandleView {
    pub fn new(task_ref: String, status: TaskHandleStatus) -> Self {
        Self { task_ref, status, running_s: None, idle_s: None }
    }
}
