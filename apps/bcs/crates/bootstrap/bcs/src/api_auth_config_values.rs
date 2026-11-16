//! Value-domain guards for the `[api.auth]` config subtree (spec §4.2).
//!
//! The shared parse entry (`config_loader::parse_config_content`) normalizes
//! TOML and JSON documents into `serde_json::Value` before any deep-merge or
//! env expansion. These guards check ONLY the `api.auth` subtree of a parsed
//! but still-native value and reject everything outside the unambiguous
//! `api.auth` value domain:
//!
//! - allowed: String, Bool, i64-representable integers, and arrays/objects
//!   recursively composed of those;
//! - TOML: Datetime and Float (including NaN/Inf) are rejected *before* the
//!   `serde_json::to_value` conversion;
//! - JSON: Null, floats, and integers outside the i64 range are rejected
//!   *before* the overlay merge;
//! - an absent `api.auth` subtree is fine, an explicit null is not;
//! - the restriction covers registered-but-not-enabled plugin tables and
//!   applies even when a later overlay would replace the value. Legacy config
//!   subtrees are unaffected;
//! - integers never round-trip through f64 or strings, and quoted date
//!   strings stay strings (the descriptor schema decides whether the format
//!   is accepted).
//!
//! Error contract (spec C3): messages carry a field path plus a category
//! only — raw values are never echoed.

/// Guards a parsed TOML document before its `serde_json::to_value` conversion.
pub(crate) fn validate_api_auth_toml_value(root: &toml::Value) -> Result<(), String> {
    let Some(api) = root.get("api") else {
        return Ok(());
    };
    let Some(auth) = api.get("auth") else {
        return Ok(());
    };
    validate_toml_subtree(auth, "api.auth")
}

fn validate_toml_subtree(value: &toml::Value, path: &str) -> Result<(), String> {
    match value {
        toml::Value::String(_) | toml::Value::Integer(_) | toml::Value::Boolean(_) => Ok(()),
        toml::Value::Array(items) => {
            for (idx, item) in items.iter().enumerate() {
                validate_toml_subtree(item, &format!("{path}[{idx}]"))?;
            }
            Ok(())
        }
        toml::Value::Table(table) => {
            for (key, child) in table.iter() {
                validate_toml_subtree(child, &format!("{path}.{key}"))?;
            }
            Ok(())
        }
        toml::Value::Datetime(_) => Err(format!(
            "{path}: TOML datetime values are not allowed in the api.auth value domain (declare the expected string format in the plugin schema instead) (category: datetime)"
        )),
        toml::Value::Float(_) => Err(format!(
            "{path}: floating-point values are not allowed in the api.auth value domain (category: floating-point)"
        )),
    }
}

/// Guards a parsed JSON document before the overlay deep-merge.
pub(crate) fn validate_api_auth_json_value(root: &serde_json::Value) -> Result<(), String> {
    let Some(api) = root.get("api") else {
        return Ok(());
    };
    let Some(auth) = api.get("auth") else {
        return Ok(());
    };
    validate_json_subtree(auth, "api.auth")
}

fn validate_json_subtree(value: &serde_json::Value, path: &str) -> Result<(), String> {
    match value {
        serde_json::Value::String(_) | serde_json::Value::Bool(_) => Ok(()),
        serde_json::Value::Number(number) => {
            if number.as_i64().is_some() {
                Ok(())
            } else if number.as_u64().is_some() {
                Err(format!(
                    "{path}: integers outside the i64 range are not allowed in the api.auth value domain (category: out-of-range-integer)"
                ))
            } else {
                Err(format!(
                    "{path}: floating-point values are not allowed in the api.auth value domain (category: floating-point)"
                ))
            }
        }
        serde_json::Value::Array(items) => {
            for (idx, item) in items.iter().enumerate() {
                validate_json_subtree(item, &format!("{path}[{idx}]"))?;
            }
            Ok(())
        }
        serde_json::Value::Object(map) => {
            for (key, child) in map.iter() {
                validate_json_subtree(child, &format!("{path}.{key}"))?;
            }
            Ok(())
        }
        serde_json::Value::Null => Err(format!(
            "{path}: explicit null values are not allowed in the api.auth value domain (absent and null are not equivalent) (category: null)"
        )),
    }
}
