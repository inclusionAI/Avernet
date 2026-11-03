use serde::{Deserialize, Serialize};

/// Gateway Principal verification trust and signing-key lookup configuration.
///
/// The signing key itself is intentionally not configuration: bootstrap resolves
/// it from `signing_key_secret` through the configured SecretAccessPort, or
/// falls back to `signing_key_env` at process startup for compatibility.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewayPrincipalConfig {
    /// Accepted JWT issuers. A token whose `iss` claim matches any entry is
    /// accepted. Defaults to both `gateway` and `backend` so tokens minted by
    /// either issuer are trusted out of the box.
    #[serde(default = "default_gateway_principal_issuers")]
    pub issuers: Vec<String>,

    #[serde(default = "default_gateway_principal_audience")]
    pub audience: String,

    #[serde(default = "default_gateway_principal_key_id")]
    pub key_id: String,

    #[serde(default = "default_gateway_principal_signing_key_env")]
    pub signing_key_env: String,

    #[serde(default)]
    pub signing_key_secret: Option<String>,
}

impl Default for GatewayPrincipalConfig {
    fn default() -> Self {
        Self {
            issuers: default_gateway_principal_issuers(),
            audience: default_gateway_principal_audience(),
            key_id: default_gateway_principal_key_id(),
            signing_key_env: default_gateway_principal_signing_key_env(),
            signing_key_secret: None,
        }
    }
}

impl GatewayPrincipalConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.issuers.is_empty() {
            return Err("gateway_principal.issuers must not be empty".to_string());
        }
        let mut seen: Vec<&str> = Vec::with_capacity(self.issuers.len());
        for issuer in &self.issuers {
            if issuer.trim().is_empty() {
                return Err("gateway_principal.issuers must not contain blank entries".to_string());
            }
            if seen.iter().any(|prior| *prior == issuer) {
                return Err("gateway_principal.issuers must not contain duplicates".to_string());
            }
            seen.push(issuer);
        }
        for (field, value) in [
            ("audience", &self.audience),
            ("key_id", &self.key_id),
            ("signing_key_env", &self.signing_key_env),
        ] {
            if value.trim().is_empty() {
                return Err(format!("gateway_principal.{field} must not be blank"));
            }
        }
        if self
            .signing_key_secret
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err("gateway_principal.signing_key_secret must not be blank".to_string());
        }
        Ok(())
    }
}

fn default_gateway_principal_issuers() -> Vec<String> {
    vec!["gateway".to_string(), "backend".to_string()]
}

fn default_gateway_principal_audience() -> String {
    "bcs".to_string()
}

fn default_gateway_principal_key_id() -> String {
    "bare".to_string()
}

fn default_gateway_principal_signing_key_env() -> String {
    "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE".to_string()
}

/// Group-session WebSocket JWT signing-key lookup configuration.
///
/// The signing key material is resolved through the configured SecretAccessPort
/// using `signing_key_secret` as the logical secret name.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GroupSessionWsConfig {
    #[serde(default = "default_group_session_ws_signing_key_secret")]
    pub signing_key_secret: String,
}

impl Default for GroupSessionWsConfig {
    fn default() -> Self {
        Self {
            signing_key_secret: default_group_session_ws_signing_key_secret(),
        }
    }
}

impl GroupSessionWsConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.signing_key_secret.trim().is_empty() {
            return Err("group_session_ws.signing_key_secret must not be blank".to_string());
        }
        Ok(())
    }
}

fn default_group_session_ws_signing_key_secret() -> String {
    "bcn-group-session-ws-jwt".to_string()
}
