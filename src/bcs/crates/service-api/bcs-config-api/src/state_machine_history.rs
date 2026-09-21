use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StateMachineHistoryReadSource {
    #[default]
    Runtime,
    Messages,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct StateMachineHistoryConfig {
    pub persistence_enabled: bool,
    pub read_source: StateMachineHistoryReadSource,
}

impl StateMachineHistoryConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.read_source == StateMachineHistoryReadSource::Messages && !self.persistence_enabled {
            return Err("state_machine_history.read_source=messages requires persistence_enabled=true".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn messages_reads_require_persistence_and_runtime_remains_default() {
        let mut config = StateMachineHistoryConfig::default();
        assert!(!config.persistence_enabled);
        assert!(config.validate().is_ok());
        config.persistence_enabled = true;
        assert!(config.validate().is_ok());
        config.read_source = StateMachineHistoryReadSource::Messages;
        assert!(config.validate().is_ok());
        config.persistence_enabled = false;
        assert!(config.validate().is_err());
    }
}
