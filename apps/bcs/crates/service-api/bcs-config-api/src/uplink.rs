//! Deployment-wide authorization for built-in WebSocket coordination profiles.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UplinkProfile {
    NativeMcp,
    McporterMcp,
}

impl UplinkProfile {
    pub fn from_client_kind(kind: &str) -> Option<Self> {
        match kind {
            "native_mcp" => Some(Self::NativeMcp),
            "mcporter_mcp" => Some(Self::McporterMcp),
            _ => None,
        }
    }
}

/// Empty by default. Enabling a profile authorizes every authenticated uplink
/// Bot in this deployment to request it; this is not a per-Bot entitlement.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UplinkConfig {
    #[serde(default)]
    pub allowed_profiles: Vec<UplinkProfile>,
}

impl UplinkConfig {
    pub fn negotiate(&self, version: Option<u32>, kind: Option<String>) -> Option<String> {
        match kind {
            Some(kind) => match UplinkProfile::from_client_kind(&kind) {
                Some(profile) if version == Some(3) && self.allowed_profiles.contains(&profile) => {
                    Some(kind)
                }
                Some(_) => None,
                None => Some(kind), // Existing non-MCP client kinds retain their behavior.
            },
            None => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profiles_are_explicit_versioned_and_default_closed() {
        let enabled = UplinkConfig {
            allowed_profiles: vec![UplinkProfile::NativeMcp, UplinkProfile::McporterMcp],
        };
        for kind in ["native_mcp", "mcporter_mcp"] {
            assert_eq!(UplinkConfig::default().negotiate(Some(3), Some(kind.into())), None);
            for version in [None, Some(1), Some(2), Some(4)] {
                assert_eq!(enabled.negotiate(version, Some(kind.into())), None);
            }
            assert_eq!(enabled.negotiate(Some(3), Some(kind.into())), Some(kind.into()));
        }
        assert_eq!(enabled.negotiate(Some(3), None), None);
        assert_eq!(enabled.negotiate(Some(2), Some("plugin".into())), Some("plugin".into()));
        assert!(serde_json::from_str::<UplinkConfig>(r#"{"allowed_profiles":["arbitrary"]}"#).is_err());
        assert!(serde_json::from_str::<UplinkConfig>(r#"{"tool_name_mapping":{}}"#).is_err());
    }
}
