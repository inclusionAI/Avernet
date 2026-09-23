//! Typed per-source `[api.auth.<name>]` plugin-table parsing and secret
//! resolution for the V1 auth source build phase (Task 12).
//!
//! Each plugin table is parsed from its `serde_json::Value` form into a
//! minimal typed struct; every credential-bearing field is a LOGICAL secret
//! reference resolved through the [`SecretAccessPort`] at build time. Error
//! messages carry only the logical field path (`api.auth.<source>.<field>`)
//! — never a resolved value and never a backend error payload.

use std::sync::Arc;

use serde::Deserialize;

use bcs_auth_api::OAuthProvider;
use bcs_config_api::GatewayApiAuthConfig;
use bcs_service_api::port::secret::SecretAccessPort;

/// Parse the `[api.auth.gateway]` table. The gateway source is the built-in
/// typed source declared in `bcs-config-api`; no additional struct here.
pub fn parse_gateway_table(options: &serde_json::Value) -> Result<GatewayApiAuthConfig, String> {
    serde_json::from_value(options.clone())
        .map_err(|e| format!("invalid [api.auth.gateway] table: {e}"))
}

/// `[api.auth.alipay]` — minimal typed table. Field set mirrors the Task 6
/// `validate_alipay_table` allowlist.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AlipaySourceTable {
    pub client_id: String,
    pub private_key_secret: String,
    pub alipay_public_key_secret: String,
}

/// `[api.auth.github]` / `[api.auth.google]` / `[api.auth.wechat]` — minimal
/// typed table for the OAuth sources with a single client-secret reference.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SimpleOAuthSourceTable {
    pub client_id: String,
    pub client_secret_secret: String,
}

/// Resolve one logical secret reference for a source field.
///
/// Only the logical field path (and the configured secret *name*, which is
/// itself configuration) may appear in an error — the resolved VALUE and the
/// backend error payload must never surface.
pub async fn resolve_source_secret(
    secret_access: &dyn SecretAccessPort,
    source: &str,
    field: &str,
    logical_name: Option<&str>,
) -> Result<String, String> {
    // Chain-common fields (session_signing_key_secret) pass an empty source
    // name; the logical path then skips the extra segment.
    let path = if source.is_empty() {
        format!("api.auth.{field}")
    } else {
        format!("api.auth.{source}.{field}")
    };
    let name = logical_name
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("{path} is required"))?;
    let record = secret_access
        .get_secret(name)
        .await
        .map_err(|_| format!("{path} is required (secret '{name}' did not resolve)"))?;
    if record.value.trim().is_empty() {
        return Err(format!("{path} resolved to an empty secret"));
    }
    Ok(record.value)
}

fn parse_table<T: serde::de::DeserializeOwned>(
    source: &str,
    options: &serde_json::Value,
) -> Result<T, String> {
    serde_json::from_value(options.clone())
        .map_err(|e| format!("invalid [api.auth.{source}] table: {e}"))
}

/// Build the OAuth provider CLIENT for one enabled source (spec §6: the
/// composition root constructs "OAuthProvider clients + V1 auth application
/// facade"). Called only for chain-enabled sources, so disabled instances'
/// secrets are never read. Mirrors the credential handling of the legacy
/// `build_oauth_provider` but consumes the NEW `[api.auth.<source>]` tables.
pub async fn build_oauth_source_provider(
    source: &str,
    options: &serde_json::Value,
    secret_access: &std::sync::Arc<dyn SecretAccessPort>,
) -> Result<Arc<dyn OAuthProvider>, String> {
    match source {
        "github" => {
            let table: SimpleOAuthSourceTable = parse_table(source, options)?;
            let client_secret = resolve_source_secret(
                secret_access.as_ref(),
                source,
                "client_secret_secret",
                Some(&table.client_secret_secret),
            )
            .await?;
            Ok(Arc::new(bcs_auth_github::GitHubOAuthProvider::new(
                bcs_auth_github::GitHubOAuthConfig {
                    client_id: table.client_id,
                    client_secret,
                },
            )))
        }
        "google" => {
            let table: SimpleOAuthSourceTable = parse_table(source, options)?;
            let client_secret = resolve_source_secret(
                secret_access.as_ref(),
                source,
                "client_secret_secret",
                Some(&table.client_secret_secret),
            )
            .await?;
            Ok(Arc::new(bcs_auth_google::GoogleOAuthProvider::new(
                bcs_auth_google::GoogleOAuthConfig {
                    client_id: table.client_id,
                    client_secret,
                },
            )))
        }
        "wechat" => {
            let table: SimpleOAuthSourceTable = parse_table(source, options)?;
            let secret = resolve_source_secret(
                secret_access.as_ref(),
                source,
                "client_secret_secret",
                Some(&table.client_secret_secret),
            )
            .await?;
            let provider = bcs_auth_wechat::WeChatOAuthProvider::new(bcs_auth_wechat::WeChatConfig {
                appid: table.client_id,
                secret,
            });
            Ok(Arc::new(provider) as Arc<dyn OAuthProvider>)
        }
        "alipay" => {
            let table: AlipaySourceTable = parse_table(source, options)?;
            let private_key_pem = resolve_source_secret(
                secret_access.as_ref(),
                source,
                "private_key_secret",
                Some(&table.private_key_secret),
            )
            .await?;
            let alipay_public_key_pem = resolve_source_secret(
                secret_access.as_ref(),
                source,
                "alipay_public_key_secret",
                Some(&table.alipay_public_key_secret),
            )
            .await?;
            let provider = bcs_auth_alipay::AlipayOAuthProvider::new(bcs_auth_alipay::AlipayConfig {
                app_id: table.client_id,
                private_key_pem,
                alipay_public_key_pem,
            })
            .map_err(|e| format!("api.auth.{source}: invalid alipay key configuration: {e}"))?;
            Ok(Arc::new(provider) as Arc<dyn OAuthProvider>)
        }
        other => Err(format!(
            "api.auth.{other}: no production OAuth provider client is registered for this source"
        )),
    }
}
