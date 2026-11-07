//! Per-source parameter tables for the registered `[api.auth]` sources
//! (V1 API auth plugin chain, plan Task 12).
//!
//! One place owns each registered source's parameter CONTRACT:
//!
//! - the `validate_*` functions are the registry descriptors' structural
//!   schema (Task 12's `validate` pointer): they run on the public
//!   value-domain JSON both for chain-ENABLED tables and for
//!   registered-but-not-enabled tables, which never gain secret access;
//! - the `*_params` extraction helpers read the SAME validated tables
//!   (plain typed reads — no TOML/JSON/float/string round-trip, the
//!   registry contract) so the build phase receives identical values;
//! - the `*_secret` helpers resolve only what an ENABLED source names,
//!   through [`SecretAccessPort`], and their error text is a logical
//!   field path plus a category — never a raw configuration value, a
//!   resolved value, or provider-echoed text.

use std::sync::Arc;

use bcs_auth_api::OAuthProvider;
use bcs_service_api::port::secret::SecretAccessPort;

/// Gateway source defaults (spec §4.2): `issuers` defaults to
/// `["gateway", "backend"]`, `audience` to `bcs`, `key_id` to `bare`.
pub(crate) const GATEWAY_DEFAULT_ISSUERS: &[&str] = &["gateway", "backend"];
pub(crate) const GATEWAY_DEFAULT_AUDIENCE: &str = "bcs";
pub(crate) const GATEWAY_DEFAULT_KEY_ID: &str = "bare";

/// Typed parameters extracted (with defaults applied) from a validated
/// `[api.auth.gateway]` table. The signing reference stays a LOGICAL
/// secret name until an enabled build resolves it.
pub(crate) struct GatewaySourceParams {
    pub issuers: Vec<String>,
    pub audience: String,
    pub key_id: String,
    pub signing_key_secret: String,
}

/// The only keys the gateway table accepts (deny-everything-else).
const GATEWAY_TABLE_KEYS: &[&str] =
    &["issuers", "audience", "key_id", "signing_key_secret"];

/// Structural schema for `[api.auth.gateway]`: `signing_key_secret` is
/// required and non-blank; `issuers` (array of non-blank, non-repeating
/// strings), `audience`, and `key_id` are optional non-blank strings
/// carrying the spec defaults; unknown keys reject.
pub(crate) fn validate_gateway_table(table: &serde_json::Value) -> Result<(), String> {
    let path = "api.auth.gateway";
    let Some(table) = table.as_object() else {
        return Err(format!("{path}: must be a table (category: invalid_type)"));
    };
    for key in table.keys() {
        if !GATEWAY_TABLE_KEYS.contains(&key.as_str()) {
            return Err(format!(
                "{path}.{key}: unknown field (category: unknown_field)"
            ));
        }
    }
    require_non_blank_string(table.get("signing_key_secret"), &format!("{path}.signing_key_secret"))?;
    if let Some(issuers) = table.get("issuers") {
        let Some(list) = issuers.as_array() else {
            return Err(format!("{path}.issuers: must be an array (category: invalid_type)"));
        };
        if list.is_empty() {
            return Err(format!("{path}.issuers: must not be empty (category: blank_value)"));
        }
        let mut seen: Vec<&str> = Vec::with_capacity(list.len());
        for issuer in list {
            let value = issuer.as_str().ok_or_else(|| {
                format!("{path}.issuers: entries must be strings (category: invalid_type)")
            })?;
            if value.trim().is_empty() {
                return Err(format!(
                    "{path}.issuers: entries must not be blank (category: blank_value)"
                ));
            }
            if seen.contains(&value) {
                return Err(format!(
                    "{path}.issuers: duplicate issuer (category: duplicate_value)"
                ));
            }
            seen.push(value);
        }
    }
    // `audience` / `key_id` are optional with spec defaults; when PRESENT
    // they must still be non-blank strings.
    for field in ["audience", "key_id"] {
        match table.get(field) {
            None => {}
            Some(value) => match value.as_str() {
                Some(text) if !text.trim().is_empty() => {}
                Some(_) => {
                    return Err(format!("{path}.{field}: must not be blank (category: blank_value)"))
                }
                None => {
                    return Err(format!(
                        "{path}.{field}: must be a string (category: invalid_type)"
                    ))
                }
            },
        }
    }
    Ok(())
}

/// Extract the gateway trust parameters with the spec defaults applied.
/// The table was already schema-validated; a chain entry without a table
/// is a required-field failure, not a default-everything success (spec:
/// the chain's sources must be constructible from configuration).
pub(crate) fn gateway_source_params(
    table: &serde_json::Value,
) -> Result<GatewaySourceParams, String> {
    let path = "api.auth.gateway";
    let Some(table) = table.as_object() else {
        return Err(format!(
            "{path}.signing_key_secret: required and must not be empty (category: missing_field)"
        ));
    };
    let issuers = table
        .get("issuers")
        .map(|value| {
            value
                .as_array()
                .expect("registry validation guarantees the array shape")
                .iter()
                .map(|issuer| {
                    issuer
                        .as_str()
                        .expect("registry validation guarantees string entries")
                        .to_string()
                })
                .collect::<Vec<String>>()
        })
        .unwrap_or_else(|| GATEWAY_DEFAULT_ISSUERS.iter().map(|s| s.to_string()).collect());
    let audience = optional_non_blank(table.get("audience"))
        .unwrap_or_else(|| GATEWAY_DEFAULT_AUDIENCE.to_string());
    let key_id =
        optional_non_blank(table.get("key_id")).unwrap_or_else(|| GATEWAY_DEFAULT_KEY_ID.to_string());
    let signing_key_secret = optional_non_blank(table.get("signing_key_secret")).ok_or_else(
        || {
            format!(
                "{path}.signing_key_secret: required and must not be empty (category: missing_field)"
            )
        },
    )?;
    Ok(GatewaySourceParams {
        issuers,
        audience,
        key_id,
        signing_key_secret,
    })
}

/// The supported OAuth instance kinds share one shape family: the standard
/// client pair, plus alipay's key pair.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum OAuthTableSchema {
    /// `client_id` + `client_secret_secret` (github / google / wechat).
    ClientCredentials,
    /// `client_id` + `private_key_secret` + `alipay_public_key_secret`.
    AlipayKeys,
}

/// Structural schema for the standard OAuth instance tables:
/// `client_id` and `client_secret_secret` required and non-blank;
/// everything else rejects.
pub(crate) fn validate_oauth_client_table(
    name: &str,
    table: &serde_json::Value,
) -> Result<(), String> {
    validate_oauth_table_known_shape(name, table, &["client_id", "client_secret_secret"])
}

/// Structural schema for `[api.auth.alipay]`: the app id plus the two
/// logical key references; everything else rejects.
pub(crate) fn validate_alipay_table(name: &str, table: &serde_json::Value) -> Result<(), String> {
    validate_oauth_table_known_shape(
        name,
        table,
        &["client_id", "private_key_secret", "alipay_public_key_secret"],
    )
}

fn validate_oauth_table_known_shape(
    name: &str,
    table: &serde_json::Value,
    allowed: &[&str],
) -> Result<(), String> {
    let path = format!("api.auth.{name}");
    let Some(table) = table.as_object() else {
        return Err(format!("{path}: must be a table (category: invalid_type)"));
    };
    for key in table.keys() {
        if !allowed.contains(&key.as_str()) {
            return Err(format!(
                "{path}.{key}: unknown field (category: unknown_field)"
            ));
        }
    }
    for field in allowed {
        require_non_blank_string(table.get(*field), &format!("{path}.{field}"))?;
    }
    Ok(())
}

/// The OAuth instance's public client coordinates plus the logical secret
/// references it declared (field name -> logical secret reference).
pub(crate) struct OAuthClientParams {
    pub client_id: String,
    pub secret_refs: Vec<(&'static str, String)>,
}

/// Extract the client coordinates from an already-validated table.
pub(crate) fn oauth_client_params(
    name: &str,
    table: &serde_json::Value,
    schema: OAuthTableSchema,
) -> Result<OAuthClientParams, String> {
    let fields: &[&str] = match schema {
        OAuthTableSchema::ClientCredentials => &["client_id", "client_secret_secret"],
        OAuthTableSchema::AlipayKeys => &[
            "client_id",
            "private_key_secret",
            "alipay_public_key_secret",
        ],
    };
    let client_id = optional_non_blank(table.get("client_id")).ok_or_else(|| {
        format!(
            "api.auth.{name}.client_id: required and must not be empty (category: missing_field)"
        )
    })?;
    let mut secret_refs = Vec::new();
    for field in fields.iter().skip(1) {
        let reference = optional_non_blank(table.get(*field)).ok_or_else(|| {
            format!(
                "api.auth.{name}.{field}: required and must not be empty (category: missing_field)"
            )
        })?;
        secret_refs.push((*field, reference));
    }
    Ok(OAuthClientParams {
        client_id,
        secret_refs,
    })
}

/// Resolve one logical secret reference for an ENABLED source. The error is
/// the field path plus a category; the reference NAME and the resolved
/// material never appear in it (spec C3 / §8.4).
pub(crate) async fn resolve_source_secret(
    reference: &str,
    field_path: &str,
    secret_access: &Arc<dyn SecretAccessPort>,
) -> Result<String, String> {
    let record = secret_access
        .get_secret(reference)
        .await
        .map_err(|_| format!("{field_path}: referenced secret unavailable (category: secret_unavailable)"))?;
    if record.value.trim().is_empty() {
        return Err(format!(
            "{field_path}: referenced secret resolved to empty material (category: blank_value)"
        ));
    }
    Ok(record.value)
}

/// Build the concrete `OAuthProvider` client for one ENABLED OAuth instance
/// (the compile-time closed set: github / google / wechat / alipay). The
/// parameters arrive non-blank and the schema variant selects the
/// per-provider kind; a key/construction failure fails startup with a
/// field-path-only message. Provider errors never reach here (clients are
/// constructed, not called).
pub(crate) async fn build_enabled_oauth_client(
    name: &str,
    table: &serde_json::Value,
    schema: OAuthTableSchema,
    secret_access: &Arc<dyn SecretAccessPort>,
) -> Result<Arc<dyn OAuthProvider>, String> {
    let params = oauth_client_params(name, table, schema)?;
    match schema {
        OAuthTableSchema::ClientCredentials => {
            let [(_field, reference)]: [_; 1] = params.secret_refs.try_into().ok().expect(
                "registry validation guarantees exactly one client-secret reference",
            );
            let client_secret =
                resolve_source_secret(&reference, &format!("api.auth.{name}.client_secret_secret"), secret_access)
                    .await?;
            build_provider_by_kind(name, &params.client_id, client_secret, None, None)
        }
        OAuthTableSchema::AlipayKeys => {
            let references: Vec<(&'static str, String)> = params.secret_refs;
            let get = |field: &str| {
                references
                    .iter()
                    .find(|(key, _)| *key == field)
                    .map(|(_, value)| value.clone())
                    .unwrap_or_default()
            };
            let private_reference = get("private_key_secret");
            let public_reference = get("alipay_public_key_secret");
            let private_key_pem = resolve_source_secret(
                &private_reference,
                &format!("api.auth.{name}.private_key_secret"),
                secret_access,
            )
            .await?;
            let alipay_public_key_pem = resolve_source_secret(
                &public_reference,
                &format!("api.auth.{name}.alipay_public_key_secret"),
                secret_access,
            )
            .await?;
            build_provider_by_kind(
                name,
                &params.client_id,
                String::new(),
                Some(private_key_pem),
                Some(alipay_public_key_pem),
            )
        }
    }
}

/// The single `match` over the closed provider kind set — the same
/// construction discipline as `auth_wiring::build_oauth_provider`, fed from
/// the `[api.auth]` tables instead of the legacy `[auth.oauth]` section.
/// Errors carry the field path and a category only (never key material).
fn build_provider_by_kind(
    name: &str,
    client_id: &str,
    client_secret: String,
    private_key_pem: Option<String>,
    alipay_public_key_pem: Option<String>,
) -> Result<Arc<dyn OAuthProvider>, String> {
    match name {
        "github" => Ok(Arc::new(bcs_auth_github::GitHubOAuthProvider::new(
            bcs_auth_github::GitHubOAuthConfig {
                client_id: client_id.to_string(),
                client_secret,
            },
        ))),
        "google" => Ok(Arc::new(bcs_auth_google::GoogleOAuthProvider::new(
            bcs_auth_google::GoogleOAuthConfig {
                client_id: client_id.to_string(),
                client_secret,
            },
        ))),
        "wechat" => {
            if client_secret.trim().is_empty() {
                return Err(format!(
                    "api.auth.{name}.client_secret_secret: resolved material is empty (category: blank_value)"
                ));
            }
            Ok(Arc::new(bcs_auth_wechat::WeChatOAuthProvider::new(
                bcs_auth_wechat::WeChatConfig {
                    appid: client_id.to_string(),
                    secret: client_secret,
                },
            )))
        }
        "alipay" => {
            let Some(private_key_pem) = private_key_pem else {
                return Err(format!(
                    "api.auth.{name}.private_key_secret: required (category: missing_field)"
                ));
            };
            let Some(alipay_public_key_pem) = alipay_public_key_pem else {
                return Err(format!(
                    "api.auth.{name}.alipay_public_key_secret: required (category: missing_field)"
                ));
            };
            let provider = bcs_auth_alipay::AlipayOAuthProvider::new(
                bcs_auth_alipay::AlipayConfig {
                    app_id: client_id.to_string(),
                    private_key_pem,
                    alipay_public_key_pem,
                },
            )
            .map_err(|_| {
                format!(
                    "api.auth.{name}: provider keys rejected by the provider implementation (category: invalid_provider_keys)"
                )
            })?;
            Ok(Arc::new(provider))
        }
        other => Err(format!(
            "api.auth.{other}: source is not a constructible OAuth provider kind (category: unknown_source)"
        )),
    }
}

/// Type check used by the schemas: present-and-non-blank, or absent.
fn require_non_blank_string(
    value: Option<&serde_json::Value>,
    path: &str,
) -> Result<(), String> {
    match value {
        None => Err(format!("{path}: required and must not be empty (category: missing_field)")),
        Some(value) => match value.as_str() {
            Some(text) if !text.trim().is_empty() => Ok(()),
            Some(_) => Err(format!("{path}: must not be blank (category: blank_value)")),
            None => Err(format!("{path}: must be a string (category: invalid_type)")),
        },
    }
}

/// Read an optional non-blank string field (registry validation already
/// guaranteed the type when the table is present).
fn optional_non_blank(value: Option<&serde_json::Value>) -> Option<String> {
    value
        .and_then(|value| value.as_str())
        .filter(|text| !text.trim().is_empty())
        .map(str::to_string)
}
