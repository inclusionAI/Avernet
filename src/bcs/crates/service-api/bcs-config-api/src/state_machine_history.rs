use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct StateMachineHistoryConfig {
    pub persistence_enabled: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn persistence_defaults_off_and_removed_read_source_is_rejected() {
        let config: StateMachineHistoryConfig = serde_json::from_str("{}").unwrap();
        assert!(!config.persistence_enabled);
        assert!(serde_json::from_str::<StateMachineHistoryConfig>(
            r#"{"persistence_enabled":true,"read_source":"runtime"}"#,
        ).is_err());
    }
}
