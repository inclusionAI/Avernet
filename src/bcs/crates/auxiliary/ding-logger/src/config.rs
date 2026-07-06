use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupLoggerConfig {
    /// Enable the logger (default: false).
    #[serde(default)]
    pub enabled: bool,

    /// DingTalk App Key (client_id).
    pub client_id: String,

    /// DingTalk App Secret (client_secret).
    pub client_secret: String,

    /// Allowlist of openConversationId values to monitor.
    pub group_ids: Vec<String>,
}

impl GroupLoggerConfig {
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.group_ids.is_empty() {
            anyhow::bail!("[ding-logger] group_ids must not be empty — configure at least one group ID");
        }
        if self.client_id.is_empty() {
            anyhow::bail!("[ding-logger] client_id must not be empty");
        }
        if self.client_secret.is_empty() {
            anyhow::bail!("[ding-logger] client_secret must not be empty");
        }
        Ok(())
    }
}
