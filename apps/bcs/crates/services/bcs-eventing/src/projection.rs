//! Immutable canonical Event payload projection.

use std::collections::BTreeMap;

use bcs_service_api::types::{EventEnvelope, EventPayloadMode};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::EventCatalog;

pub const DEFAULT_MAX_CONTENT_BYTES: usize = 64 * 1024;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum EventProjectionError {
    #[error("event type is not registered in the public Event Catalog")]
    UnknownEventType,
    #[error("canonical Event contains an invalid content projection")]
    InvalidContent,
    #[error("projected Event exceeds the configured body size limit")]
    PayloadTooLarge,
    #[error("projected Event serialization failed")]
    Serialization,
}

pub fn project_event(
    event: &EventEnvelope,
    catalog: &EventCatalog,
    mode: EventPayloadMode,
    max_event_body_bytes: usize,
) -> Result<Vec<u8>, EventProjectionError> {
    let catalog_event = catalog
        .event(&event.event_type)
        .ok_or(EventProjectionError::UnknownEventType)?;
    let mut projected = event.clone();
    let mut data =
        serde_json::to_value(&projected.data).map_err(|_| EventProjectionError::Serialization)?;
    remove_sensitive_fields(&mut data);
    let Value::Object(data_object) = data else {
        return Err(EventProjectionError::InvalidContent);
    };
    projected.data = data_object.into_iter().collect::<BTreeMap<_, _>>();

    for field in &catalog_event.content_fields {
        let Some(value) = projected.data.get_mut(field) else {
            continue;
        };
        *value = project_content(value, mode)?;
    }
    if mode == EventPayloadMode::MetadataOnly
        && event.event_type == "message.created"
        && let Some(attachments) = projected.data.get_mut("attachments")
    {
        *attachments = Value::Array(Vec::new());
    }

    let body = serde_json::to_vec(&projected).map_err(|_| EventProjectionError::Serialization)?;
    if body.len() > max_event_body_bytes {
        return Err(EventProjectionError::PayloadTooLarge);
    }
    Ok(body)
}

fn project_content(value: &Value, mode: EventPayloadMode) -> Result<Value, EventProjectionError> {
    let normalized = normalize_content(value)?;
    let content_type = normalized
        .get("content_type")
        .and_then(Value::as_str)
        .unwrap_or("text/plain")
        .to_string();
    let text = normalized.get("text");
    let json = normalized.get("json");
    if text.is_some() && json.is_some() {
        return Err(EventProjectionError::InvalidContent);
    }
    let (content_value, is_json) = if let Some(text) = text {
        (text.clone(), false)
    } else if let Some(json) = json {
        (json.clone(), true)
    } else if mode == EventPayloadMode::MetadataOnly {
        (Value::String(String::new()), false)
    } else {
        return Err(EventProjectionError::InvalidContent);
    };
    let canonical_bytes = if is_json {
        serde_json::to_vec(&content_value).map_err(|_| EventProjectionError::Serialization)?
    } else {
        content_value
            .as_str()
            .ok_or(EventProjectionError::InvalidContent)?
            .as_bytes()
            .to_vec()
    };
    let declared_size = normalized
        .get("size_bytes")
        .and_then(Value::as_u64)
        .unwrap_or(canonical_bytes.len() as u64)
        .max(canonical_bytes.len() as u64);
    let already_truncated = normalized
        .get("truncated")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let mut result = Map::new();
    result.insert(
        "included".to_string(),
        Value::Bool(mode == EventPayloadMode::Full),
    );
    result.insert("content_type".to_string(), Value::String(content_type));
    result.insert("size_bytes".to_string(), Value::from(declared_size));
    if mode == EventPayloadMode::MetadataOnly {
        result.insert("truncated".to_string(), Value::Bool(already_truncated));
        return Ok(Value::Object(result));
    }

    if is_json {
        if canonical_bytes.len() > DEFAULT_MAX_CONTENT_BYTES {
            return Err(EventProjectionError::PayloadTooLarge);
        }
        result.insert("json".to_string(), content_value);
        result.insert(
            "delivered_bytes".to_string(),
            Value::from(canonical_bytes.len() as u64),
        );
        result.insert(
            "sha256".to_string(),
            Value::String(hex_digest(&canonical_bytes)),
        );
        result.insert("truncated".to_string(), Value::Bool(already_truncated));
        return Ok(Value::Object(result));
    }

    let text = content_value
        .as_str()
        .ok_or(EventProjectionError::InvalidContent)?;
    let delivered = truncate_utf8(text, DEFAULT_MAX_CONTENT_BYTES);
    let truncated = already_truncated || delivered.len() < text.len();
    result.insert("text".to_string(), Value::String(delivered.to_string()));
    result.insert(
        "delivered_bytes".to_string(),
        Value::from(delivered.len() as u64),
    );
    result.insert(
        "sha256".to_string(),
        Value::String(hex_digest(delivered.as_bytes())),
    );
    result.insert("truncated".to_string(), Value::Bool(truncated));
    Ok(Value::Object(result))
}

fn normalize_content(value: &Value) -> Result<Map<String, Value>, EventProjectionError> {
    match value {
        Value::String(text) => Ok(Map::from_iter([
            (
                "content_type".to_string(),
                Value::String("text/plain".to_string()),
            ),
            ("size_bytes".to_string(), Value::from(text.len() as u64)),
            ("text".to_string(), Value::String(text.clone())),
            ("truncated".to_string(), Value::Bool(false)),
        ])),
        Value::Object(object) => Ok(object.clone()),
        _ => Err(EventProjectionError::InvalidContent),
    }
}

fn truncate_utf8(value: &str, max_bytes: usize) -> &str {
    if value.len() <= max_bytes {
        return value;
    }
    let mut boundary = max_bytes;
    while boundary > 0 && !value.is_char_boundary(boundary) {
        boundary -= 1;
    }
    &value[..boundary]
}

fn hex_digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn remove_sensitive_fields(value: &mut Value) {
    match value {
        Value::Object(object) => {
            object.retain(|key, nested| {
                if is_sensitive_key(key) {
                    return false;
                }
                remove_sensitive_fields(nested);
                true
            });
        }
        Value::Array(values) => {
            for nested in values {
                remove_sensitive_fields(nested);
            }
        }
        _ => {}
    }
}

fn is_sensitive_key(key: &str) -> bool {
    matches!(
        key,
        "secret"
            | "token"
            | "password"
            | "authorization"
            | "api_key"
            | "access_key"
            | "private_key"
            | "prompt"
            | "thinking"
            | "tool_arguments"
            | "raw_tool_arguments"
            | "object_handle"
            | "internal_url"
            | "share_token"
            | "owner_bot_id"
            | "physical_message_id"
    ) || key.ends_with("_secret")
        || key.ends_with("_token")
        || key.ends_with("_password")
        || key.ends_with("_api_key")
}
