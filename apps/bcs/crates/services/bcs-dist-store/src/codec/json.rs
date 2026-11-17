use super::{invalid, supported};
use bcs_service_api::port::repo::distributed::common::StoreError;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::Value;

/// JSON document of a merged model type (`SessionSpec`, `TaskSnapshot`, …).
pub fn encode_json<T: Serialize>(value: &T) -> Result<Value, StoreError> {
    serde_json::to_value(value).map_err(|_| invalid("json_encode"))
}

/// Decodes a stored document. A document with a `schema_version` field must
/// carry a supported version; it is checked before the shape is decoded.
pub fn decode_json<T: DeserializeOwned>(value: Value) -> Result<T, StoreError> {
    if let Some(version) = value.get("schema_version") {
        let version = version
            .as_u64()
            .and_then(|v| u32::try_from(v).ok())
            .ok_or_else(|| invalid("json_schema_version"))?;
        supported(version)?;
    }
    serde_json::from_value(value).map_err(|_| invalid("json_decode"))
}
