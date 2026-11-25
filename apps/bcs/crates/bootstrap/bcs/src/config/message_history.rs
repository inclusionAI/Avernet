use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MessageHistoryConfig {
    /// Groups created at or after this timestamp (ms) use the new message store path.
    /// Groups created before this timestamp fall back to the legacy history path.
    /// Default: 0 (all groups use new path).
    #[serde(default = "default_message_history_cutoff")]
    pub cutoff_timestamp: u64,

    /// ManagerWorker sessions created at or after this timestamp (ms) use the
    /// new message store path. Default disables ManagerWorker DB reads until a
    /// rollout timestamp is explicitly configured.
    #[serde(default = "default_manager_worker_message_history_cutoff")]
    pub manager_worker_cutoff_timestamp: u64,

    /// StateMachine history uses messages when persistence is enabled and the
    /// Session's original created_at is >= this UTC epoch-millisecond cutoff.
    /// Default: 0 (all Sessions use messages when persistence is enabled).
    #[serde(default)]
    pub state_machine_cutoff_timestamp: u64,

    /// Max visible history messages for a newly joined participant.
    /// Default: 100.
    #[serde(default = "default_new_participant_visible_limit")]
    pub new_participant_visible_limit: u64,

    /// Default page size when the caller does not specify a limit.
    /// Default: 50.
    #[serde(default = "default_message_page_limit")]
    pub default_page_limit: u32,

    /// Hard cap on page size to prevent excessive queries.
    /// Default: 100.
    #[serde(default = "default_message_max_page_limit")]
    pub max_page_limit: u32,
}

impl Default for MessageHistoryConfig {
    fn default() -> Self {
        Self {
            cutoff_timestamp: default_message_history_cutoff(),
            manager_worker_cutoff_timestamp: default_manager_worker_message_history_cutoff(),
            state_machine_cutoff_timestamp: 0,
            new_participant_visible_limit: default_new_participant_visible_limit(),
            default_page_limit: default_message_page_limit(),
            max_page_limit: default_message_max_page_limit(),
        }
    }
}

fn default_message_history_cutoff() -> u64 {
    0
}

fn default_manager_worker_message_history_cutoff() -> u64 {
    u64::MAX
}

fn default_new_participant_visible_limit() -> u64 {
    100
}

fn default_message_page_limit() -> u32 {
    50
}

fn default_message_max_page_limit() -> u32 {
    100
}
