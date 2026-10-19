//! Decoding for persisted Bot metadata, independent of API request decoding.

use serde::de::{DeserializeOwned, Error};
use serde_json::Value;

/// Accept object text and the single extra JSON string layer produced by legacy
/// database updates. Do not recursively decode fields: quotes, newlines and
/// backslashes inside a field are ordinary data. Deeper encoding is corruption
/// requiring explicit repair, not a different API/storage representation.
///
/// Errors never include the stored value, which may contain private credentials.
pub fn decode_bot_info<T: DeserializeOwned>(raw: &str) -> Result<T, serde_json::Error> {
    let decode = || {
        let value: Value = serde_json::from_str(raw)?;
        let value = match value {
            Value::String(inner) => serde_json::from_str(&inner)?,
            value => value,
        };
        if !value.is_object() {
            return Err(serde_json::Error::custom("expected Bot metadata object"));
        }
        serde_json::from_value(value)
    };
    decode().map_err(|_| serde_json::Error::custom("invalid Bot metadata object"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::BotCapabilities;
    use serde_json::json;

    #[test]
    fn object_and_legacy_wrapper_preserve_field_contents() {
        let value = json!({"summary":"中文\"quotes\"\\path\nline", "future":{"keep":true}});
        let object = value.to_string();
        let wrapped = serde_json::to_string(&object).unwrap();
        for raw in [&object, &wrapped] {
            assert_eq!(decode_bot_info::<Value>(raw).unwrap(), value);
            let caps = decode_bot_info::<BotCapabilities>(raw).unwrap();
            assert_eq!(caps.summary.as_deref(), value["summary"].as_str());
        }
    }

    #[test]
    fn invalid_shapes_and_field_types_fail_without_exposing_contents() {
        let deep = serde_json::to_string(&serde_json::to_string("{}").unwrap()).unwrap();
        for raw in ["broken-private", "null", "[]", "42", "true", &deep,
                    r#"{"summary":{"secret":"synthetic-private"}}"#] {
            let error = decode_bot_info::<BotCapabilities>(raw).unwrap_err();
            assert_eq!(error.to_string(), "invalid Bot metadata object");
        }
    }
}
