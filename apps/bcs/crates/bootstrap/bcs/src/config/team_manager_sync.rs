//! Resolved runtime configuration for the trusted team-manager sync slice
//! (plan Task 13, spec §1.3 Gate 0 record 2, spec §6.1).
//!
//! Resolution rules (binding for bootstrap):
//! - `enabled = false` (default): the slice stays unmounted — no team
//!   write route exists and no verifier is built.
//! - `enabled = true` with BLANK/absent signing-key material: a startup
//!   configuration ERROR (`BcsError::InvalidConfig`), never an anonymous
//!   pass-through. The deployment declared the lane, so it must provide
//!   the key.
//! - Resolved material is wrapped in [`secrecy::Secret`] and never logged
//!   or serialized.

use bcs_config_api::TeamManagerSyncConfig;
use secrecy::{ExposeSecret, Secret};

/// The resolved, fail-closed state of the team-manager sync boundary.
#[derive(Debug, Clone, Default)]
pub struct TeamManagerSyncRuntime {
    /// Whether the team-manager sync slice may mount at all.
    pub enabled: bool,
    /// The HMAC signing key for service credentials; present only when
    /// enabled. Kept as a [`Secret`] so composition-root debug output
    /// never embeds the key.
    pub signing_key: Option<Secret<String>>,
}

impl TeamManagerSyncRuntime {
    /// Whether the team-manager write routes may be mounted.
    pub fn is_mounted(&self) -> bool {
        self.enabled
            && self
                .signing_key
                .as_ref()
                .is_some_and(|key| !key.expose_secret().trim().is_empty())
    }
}

/// Resolve the section with already-resolved key material (`None` = no
/// material was injected from the environment/secret backend).
///
/// The caller (bootstrap composition root) owns material resolution:
/// process environment via the configured variable name, or the secret
/// backend via the configured reference. This function only enforces the
/// fail-closed contract so tests and the wiring share one source of
/// truth.
pub fn resolve_team_manager_sync(
    config: &TeamManagerSyncConfig,
    material: Option<&str>,
) -> Result<TeamManagerSyncRuntime, String> {
    config
        .validate()
        .map_err(|detail| format!("invalid [team_manager_sync] section: {detail}"))?;
    if !config.enabled {
        return Ok(TeamManagerSyncRuntime::default());
    }
    let key = material
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "team_manager_sync is enabled but no team-manager signing key was \
             resolved: provide the signing key through the configured \
             environment reference (team_manager_sync.signing_key_env) or the \
             secret backend (team_manager_sync.signing_key_secret)"
                .to_string()
        })?;
    Ok(TeamManagerSyncRuntime {
        enabled: true,
        signing_key: Some(Secret::new(key.to_string())),
    })
}

#[cfg(test)]
mod tests {
    use bcs_config_api::DEFAULT_TEAM_MANAGER_SYNC_SIGNING_KEY_ENV;

    use super::*;

    fn enabled_section() -> TeamManagerSyncConfig {
        TeamManagerSyncConfig {
            enabled: true,
            ..TeamManagerSyncConfig::default()
        }
    }

    #[test]
    fn disabled_section_never_requires_material() {
        let runtime = resolve_team_manager_sync(&TeamManagerSyncConfig::default(), None)
            .expect("disabled resolves");
        assert!(!runtime.enabled);
        assert!(!runtime.is_mounted());
        // Declaring the explicit enable flag is the ONLY way to mount.
        let runtime = resolve_team_manager_sync(
            &TeamManagerSyncConfig {
                enabled: false,
                ..TeamManagerSyncConfig::default()
            },
            Some("some-key"),
        )
        .expect("resolves");
        assert!(!runtime.is_mounted());
    }

    #[test]
    fn enabled_without_material_is_a_configuration_error() {
        for material in [None, Some(""), Some("   ")] {
            let error = resolve_team_manager_sync(&enabled_section(), material)
                .expect_err("enabled-but-missing material must fail");
            assert!(error.contains("team_manager_sync is enabled"), "{error}");
        }
    }

    #[test]
    fn enabled_with_material_mounts_with_a_secret_key() {
        let runtime =
            resolve_team_manager_sync(&enabled_section(), Some("  real-key-material  "))
                .expect("enabled resolves");
        assert!(runtime.is_mounted());
        // The key is never part of the debug surface.
        assert!(!format!("{runtime:?}").contains("real-key-material"));
        let key = runtime.signing_key.expect("key present");
        assert_eq!(key.expose_secret(), "real-key-material");
    }

    #[test]
    fn empty_env_reference_is_a_shape_error() {
        let config = TeamManagerSyncConfig {
            enabled: true,
            signing_key_env: String::new(),
            signing_key_secret: None,
        };
        let error = resolve_team_manager_sync(&config, Some("key"))
            .expect_err("shape error must surface");
        assert!(error.contains("invalid [team_manager_sync] section"));
        let _ = DEFAULT_TEAM_MANAGER_SYNC_SIGNING_KEY_ENV;
    }
}