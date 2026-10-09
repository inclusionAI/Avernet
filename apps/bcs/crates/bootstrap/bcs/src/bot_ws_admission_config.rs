//! `[bot_ws_admission]` configuration: rate limit for `/ws/bot` upgrades.

use std::sync::Arc;

use serde::{Deserialize, Serialize};

/// Token-bucket admission for bot WebSocket upgrades.
///
/// Spreads the reconnect storm after a BCS restart: upgrades beyond the
/// configured rate are rejected with HTTP 429 + `Retry-After` and bots retry
/// on their reconnect interval. The limit is per BCS process.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BotWsAdmissionConfig {
    /// Enable admission control. Disabled by default.
    #[serde(default)]
    pub enabled: bool,
    /// Accepted upgrades per second once the burst is consumed.
    #[serde(default = "default_rate_per_sec")]
    pub rate_per_sec: u32,
    /// Bucket capacity: upgrades accepted back-to-back from a full bucket.
    #[serde(default = "default_burst")]
    pub burst: u32,
}

impl Default for BotWsAdmissionConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            rate_per_sec: default_rate_per_sec(),
            burst: default_burst(),
        }
    }
}

impl BotWsAdmissionConfig {
    pub fn validate(&self) -> Result<(), String> {
        if !self.enabled {
            return Ok(());
        }
        if self.rate_per_sec == 0 {
            return Err("bot_ws_admission.rate_per_sec must be greater than zero".to_string());
        }
        if self.burst == 0 {
            return Err("bot_ws_admission.burst must be greater than zero".to_string());
        }
        Ok(())
    }

    /// Build the limiter, or `None` when admission control is disabled.
    pub fn build(&self) -> Option<Arc<bcs_ws::bot::BotWsAdmission>> {
        self.enabled.then(|| {
            Arc::new(bcs_ws::bot::BotWsAdmission::new(self.rate_per_sec, self.burst))
        })
    }
}

fn default_rate_per_sec() -> u32 {
    20
}

fn default_burst() -> u32 {
    50
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_by_default() {
        let config = BotWsAdmissionConfig::default();
        assert!(!config.enabled);
        assert!(config.validate().is_ok());
        assert!(config.build().is_none());
    }

    #[test]
    fn parses_from_bcs_config_toml() {
        let config: crate::BcsConfig = toml::from_str(
            r#"
            bots_base_dir = "/bots"

            [bot_ws_admission]
            enabled = true
            rate_per_sec = 5
            burst = 10
            "#,
        )
        .expect("parse bot_ws_admission");

        assert!(config.bot_ws_admission.enabled);
        assert_eq!(config.bot_ws_admission.rate_per_sec, 5);
        assert_eq!(config.bot_ws_admission.burst, 10);
        assert!(config.bot_ws_admission.build().is_some());
    }

    #[test]
    fn enabled_with_zero_rate_or_burst_is_rejected() {
        let zero_rate = BotWsAdmissionConfig {
            enabled: true,
            rate_per_sec: 0,
            burst: 1,
        };
        assert!(zero_rate.validate().unwrap_err().contains("rate_per_sec"));

        let zero_burst = BotWsAdmissionConfig {
            enabled: true,
            rate_per_sec: 1,
            burst: 0,
        };
        assert!(zero_burst.validate().unwrap_err().contains("burst"));
    }

    #[test]
    fn invalid_config_file_fails_to_load() {
        let tmp = tempfile::TempDir::new().expect("temp config dir");
        std::fs::write(
            tmp.path().join("bcs-config.toml"),
            r#"
            bots_base_dir = "/bots"

            [bot_ws_admission]
            enabled = true
            rate_per_sec = 0
            "#,
        )
        .expect("write config");

        let err = crate::BcsConfig::try_load_with_env(Some(&tmp.path().to_path_buf()))
            .expect_err("zero rate rejected");
        assert!(err.contains("bot_ws_admission.rate_per_sec must be greater than zero"));
    }
}
