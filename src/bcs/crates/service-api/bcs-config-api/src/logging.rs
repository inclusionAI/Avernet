//! Logging output configuration.

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

/// Logging configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoggingConfig {
    #[serde(default = "default_log_level")]
    pub default_level: String,
    #[serde(default = "default_true")]
    pub console: bool,
    #[serde(default)]
    pub modules: HashMap<String, String>,
    #[serde(default)]
    pub tags: HashMap<String, String>,
    #[serde(default = "default_log_outputs")]
    pub outputs: Vec<LogOutputConfig>,
}

/// A single log file output configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogOutputConfig {
    pub name: String,
    pub path: String,
    pub file: String,
    #[serde(default = "default_log_level")]
    pub level: String,
    #[serde(default = "default_rotation")]
    pub rotation: String,
    #[serde(default)]
    pub format: LogOutputFormat,
    pub targets: Vec<String>,
    #[serde(default)]
    pub max_keep_days: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogOutputFormat {
    Text,
    Json,
    /// Only the event message, without timestamp/level/target/field names.
    Raw,
}

impl Default for LogOutputFormat {
    fn default() -> Self {
        Self::Text
    }
}

fn default_log_level() -> String {
    "info".into()
}
fn default_rotation() -> String {
    "daily".into()
}
pub(super) fn default_true() -> bool {
    true
}

fn default_log_outputs() -> Vec<LogOutputConfig> {
    vec![
        LogOutputConfig {
            name: "message-delivery".to_string(),
            path: "./logs".to_string(),
            file: "message-delivery.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Raw,
            targets: vec!["bcs_message_delivery_monitor".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "common-error".to_string(),
            path: "./logs".to_string(),
            file: "common-error.log".to_string(),
            level: "error".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["*".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "messages".to_string(),
            path: "./logs".to_string(),
            file: "bcs-messages.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Json,
            targets: vec!["bcs_message".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "chat-digest".to_string(),
            path: "./logs".to_string(),
            file: "bcs-chat-digest.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["bcs_chat_digest".to_string()],
            max_keep_days: 7,
        },
        LogOutputConfig {
            name: "group-messages".to_string(),
            path: "./logs".to_string(),
            file: "group-messages.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["ding_group_message".to_string()],
            max_keep_days: 30,
        },
    ]
}

impl Default for LoggingConfig {
    fn default() -> Self {
        Self {
            default_level: "info".into(),
            console: true,
            modules: HashMap::new(),
            tags: HashMap::new(),
            outputs: default_log_outputs(),
        }
    }
}
