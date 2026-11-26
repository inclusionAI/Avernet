use super::invalid;
use bcs_dist_model::{DispatchSpec, SessionSpec, DISPATCH_SCHEMA_VERSION, SESSION_SCHEMA_VERSION};
use std::any::TypeId;
use bcs_service_api::port::repo::distributed::common::StoreError;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::Value;

/// JSON document of a merged model type (`SessionSpec`, `TaskSnapshot`, …).
/// A document with a `schema_version` field must carry a supported version, so
/// nothing is stored that `decode_json` would refuse to read.
pub fn encode_json<T: Serialize + 'static>(value: &T) -> Result<Value, StoreError> {
    let value = serde_json::to_value(value).map_err(|_| invalid("json_encode"))?;
    check_version::<T>(&value)?;
    Ok(value)
}

/// Decodes a stored document. A document with a `schema_version` field must
/// carry a supported version; it is checked before the shape is decoded.
pub fn decode_json<T: DeserializeOwned + 'static>(value: Value) -> Result<T, StoreError> {
    check_version::<T>(&value)?;
    serde_json::from_value(value).map_err(|_| invalid("json_decode"))
}

fn check_version<T: 'static>(value: &Value) -> Result<(), StoreError> {
    if let Some(version) = value.get("schema_version") {
        let version = version
            .as_u64()
            .and_then(|v| u32::try_from(v).ok())
            .ok_or_else(|| invalid("json_schema_version"))?;
        let ty = TypeId::of::<T>();
        let expected = if ty == TypeId::of::<SessionSpec>() {
            SESSION_SCHEMA_VERSION
        } else if ty == TypeId::of::<DispatchSpec>() {
            DISPATCH_SCHEMA_VERSION
        } else {
            super::SCHEMA_VERSION
        };
        if version != expected {
            return Err(StoreError::UnsupportedFormat { format: version });
        }
    }
    Ok(())
}
