//! Team-manager synchronization configuration contract (plan Task 13,
//! spec §1.3 Gate 0 record 2, spec §6.1).
//!
//! The trusted team-manager sync slice is enabled EXPLICITLY: with
//! `enabled = false` (the default) nothing mounts and no credential lane
//! exists. Declaring `enabled = true` binds the deployment to provide
//! non-blank HMAC signing-key material — resolved from the process
//! environment ([`TeamManagerSyncConfig::signing_key_env`], the default
//! reference) or from the secret backend
//! ([`TeamManagerSyncConfig::signing_key_secret`], a secret-provider key)
//! — and a DECLARED-ENABLED-but-missing/blank key is a startup
//! configuration error, never an anonymous pass-through. Resolved secret
//! material is never logged.

use serde::{Deserialize, Serialize};

/// Default process-environment reference for the signing key.
pub const DEFAULT_TEAM_MANAGER_SYNC_SIGNING_KEY_ENV: &str = "BCS_TEAM_MANAGER_SYNC_SIGNING_KEY";

/// Trusted team-manager synchronization boundary configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TeamManagerSyncConfig {
    /// Whether the trusted team-manager sync lane is enabled. Disabled
    /// (default) keeps the team write routes unmounted entirely.
    #[serde(default)]
    pub enabled: bool,

    /// Process-environment variable name holding the HMAC signing key
    /// (material reference; blank values never count as material).
    #[serde(default = "default_team_manager_signing_key_env")]
    pub signing_key_env: String,

    /// Optional secret-backend reference for the signing key (e.g.
    /// Mist). A non-blank literal resolution wins over the process
    /// environment only when the backend holds the entry; the resolver
    /// fails the startup when neither source yields material.
    #[serde(default)]
    pub signing_key_secret: Option<String>,
}

impl Default for TeamManagerSyncConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            signing_key_env: default_team_manager_signing_key_env(),
            signing_key_secret: None,
        }
    }
}

fn default_team_manager_signing_key_env() -> String {
    DEFAULT_TEAM_MANAGER_SYNC_SIGNING_KEY_ENV.to_string()
}

impl TeamManagerSyncConfig {
    /// Shape validation of the section itself. The blank-material and
    /// missing-material errors surface at secret resolution (bootstrap),
    /// where the startup fails instead of mounting an anonymous lane.
    pub fn validate(&self) -> Result<(), String> {
        if self.signing_key_env.trim().is_empty() {
            return Err("team_manager_sync.signing_key_env must not be blank".to_string());
        }
        if self
            .signing_key_secret
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(
                "team_manager_sync.signing_key_secret must not be blank when present"
                    .to_string(),
            );
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_keep_the_slice_disabled_and_env_referenced() {
        let config = TeamManagerSyncConfig::default();
        assert!(!config.enabled);
        assert_eq!(
            config.signing_key_env,
            DEFAULT_TEAM_MANAGER_SYNC_SIGNING_KEY_ENV
        );
        assert_eq!(config.signing_key_secret, None);
        assert!(config.validate().is_ok());
        let _ = serde_json::to_string(&config).expect("serializable");
    }

    #[test]
    fn enabled_section_parses_scope_references() {
        let config: TeamManagerSyncConfig = serde_json::from_str(
            r#"{"enabled": true, "signing_key_env": "CUSTOM_KEY_ENV", "signing_key_secret": "mist://team-manager"}"#,
        )
        .expect("parses");
        assert!(config.enabled);
        assert_eq!(config.signing_key_env, "CUSTOM_KEY_ENV");
        assert_eq!(config.signing_key_secret.as_deref(), Some("mist://team-manager"));
        assert!(config.validate().is_ok());
    }

    #[test]
    fn unknown_keys_are_rejected() {
        let error = serde_json::from_str::<TeamManagerSyncConfig>(
            r#"{"enabled": true, "membership_version": true}"#,
        );
        assert!(error.is_err());
        let error = serde_json::from_str::<TeamManagerSyncConfig>(r#"{"on": true}"#);
        assert!(error.is_err());
    }

    #[test]
    fn invalid_field_types_are_rejected() {
        let error = serde_json::from_str::<TeamManagerSyncConfig>(r#"{"enabled": "yes"}"#);
        assert!(error.is_err());
        let error = serde_json::from_str::<TeamManagerSyncConfig>(r#"{"signing_key_env": 3}"#);
        assert!(error.is_err());
    }

    #[test]
    fn blank_references_are_shape_errors() {
        let config = TeamManagerSyncConfig {
            enabled: true,
            signing_key_env: "  ".to_string(),
            signing_key_secret: None,
        };
        let error = config.validate().expect_err("blank env reference rejected");
        assert!(error.contains("team_manager_sync.signing_key_env"));

        let config = TeamManagerSyncConfig {
            enabled: true,
            signing_key_env: DEFAULT_TEAM_MANAGER_SYNC_SIGNING_KEY_ENV.to_string(),
            signing_key_secret: Some("  ".to_string()),
        };
        let error = config
            .validate()
            .expect_err("blank secret reference rejected");
        assert!(error.contains("team_manager_sync.signing_key_secret"));
    }
}