use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A2aRunStatus {
    pub run_id: String,
    pub status: String,
    pub response: Option<Value>,
}
