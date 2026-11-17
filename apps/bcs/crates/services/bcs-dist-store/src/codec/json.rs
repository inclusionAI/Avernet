use super::invalid;
use bcs_service_api::port::repo::distributed::common::StoreError;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::Value;

/// JSON document of a merged model type (`SessionSpec`, `TaskSnapshot`, …).
pub fn encode_json<T: Serialize>(value: &T) -> Result<Value, StoreError> {
    serde_json::to_value(value).map_err(|_| invalid("json_encode"))
}

pub fn decode_json<T: DeserializeOwned>(value: Value) -> Result<T, StoreError> {
    serde_json::from_value(value).map_err(|_| invalid("json_decode"))
}
