//! JSON-RPC 2.0 envelope and the A2A error model (spec §3.3.2, §5.4, §9.5).
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub const JSONRPC_VERSION: &str = "2.0";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    #[serde(default)]
    pub id: Value,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

impl JsonRpcRequest {
    pub fn new(id: impl Into<Value>, method: &str, params: impl Serialize) -> Result<Self, serde_json::Error> {
        Ok(Self { jsonrpc: JSONRPC_VERSION.into(), id: id.into(), method: method.into(), params: serde_json::to_value(params)? })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    #[serde(default)]
    pub id: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
}

impl JsonRpcResponse {
    pub fn success(id: Value, result: impl Serialize) -> Result<Self, serde_json::Error> {
        Ok(Self { jsonrpc: JSONRPC_VERSION.into(), id, result: Some(serde_json::to_value(result)?), error: None })
    }

    pub fn failure(id: Value, error: JsonRpcError) -> Self {
        Self { jsonrpc: JSONRPC_VERSION.into(), id, result: None, error: Some(error) }
    }

    /// Decode `result` into `T`, or surface the JSON-RPC error.
    pub fn into_result<T: serde::de::DeserializeOwned>(self) -> Result<T, JsonRpcError> {
        if let Some(error) = self.error { return Err(error); }
        let result = self.result.ok_or_else(|| JsonRpcError::new(A2aErrorCode::InvalidAgentResponse, "response has neither result nor error"))?;
        serde_json::from_value(result).map_err(|error| JsonRpcError::new(A2aErrorCode::InvalidAgentResponse, error.to_string()))
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JsonRpcError {
    pub code: i64,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl std::fmt::Display for JsonRpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "A2A error {}: {}", self.code, self.message)
    }
}

impl std::error::Error for JsonRpcError {}

impl JsonRpcError {
    /// Build an error whose `data` carries a `google.rpc.ErrorInfo` with the
    /// A2A reason, as required for A2A-specific errors.
    pub fn new(code: A2aErrorCode, message: impl Into<String>) -> Self {
        let data = code.reason().map(|reason| json!([{
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": reason,
            "domain": "a2a-protocol.org",
        }]));
        Self { code: code.code(), message: message.into(), data }
    }

    pub fn kind(&self) -> Option<A2aErrorCode> { A2aErrorCode::from_code(self.code) }

    /// Whether the A2A `reason` is one of the standard ones; non-standard
    /// payloads still map by numeric code.
    pub fn is(&self, code: A2aErrorCode) -> bool { self.code == code.code() }
}

/// JSON-RPC and A2A error codes (spec §5.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum A2aErrorCode {
    ParseError,
    InvalidRequest,
    MethodNotFound,
    InvalidParams,
    InternalError,
    TaskNotFound,
    TaskNotCancelable,
    PushNotificationNotSupported,
    UnsupportedOperation,
    ContentTypeNotSupported,
    InvalidAgentResponse,
    ExtendedAgentCardNotConfigured,
    ExtensionSupportRequired,
    VersionNotSupported,
}

impl A2aErrorCode {
    const ALL: [Self; 14] = [Self::ParseError, Self::InvalidRequest, Self::MethodNotFound, Self::InvalidParams,
        Self::InternalError, Self::TaskNotFound, Self::TaskNotCancelable, Self::PushNotificationNotSupported,
        Self::UnsupportedOperation, Self::ContentTypeNotSupported, Self::InvalidAgentResponse,
        Self::ExtendedAgentCardNotConfigured, Self::ExtensionSupportRequired, Self::VersionNotSupported];

    pub fn code(self) -> i64 {
        match self {
            Self::ParseError => -32700,
            Self::InvalidRequest => -32600,
            Self::MethodNotFound => -32601,
            Self::InvalidParams => -32602,
            Self::InternalError => -32603,
            Self::TaskNotFound => -32001,
            Self::TaskNotCancelable => -32002,
            Self::PushNotificationNotSupported => -32003,
            Self::UnsupportedOperation => -32004,
            Self::ContentTypeNotSupported => -32005,
            Self::InvalidAgentResponse => -32006,
            Self::ExtendedAgentCardNotConfigured => -32007,
            Self::ExtensionSupportRequired => -32008,
            Self::VersionNotSupported => -32009,
        }
    }

    /// `google.rpc.ErrorInfo.reason`: the A2A error name in UPPER_SNAKE_CASE
    /// without the `Error` suffix. Generic JSON-RPC errors have none.
    pub fn reason(self) -> Option<&'static str> {
        Some(match self {
            Self::TaskNotFound => "TASK_NOT_FOUND",
            Self::TaskNotCancelable => "TASK_NOT_CANCELABLE",
            Self::PushNotificationNotSupported => "PUSH_NOTIFICATION_NOT_SUPPORTED",
            Self::UnsupportedOperation => "UNSUPPORTED_OPERATION",
            Self::ContentTypeNotSupported => "CONTENT_TYPE_NOT_SUPPORTED",
            Self::InvalidAgentResponse => "INVALID_AGENT_RESPONSE",
            Self::ExtendedAgentCardNotConfigured => "EXTENDED_AGENT_CARD_NOT_CONFIGURED",
            Self::ExtensionSupportRequired => "EXTENSION_SUPPORT_REQUIRED",
            Self::VersionNotSupported => "VERSION_NOT_SUPPORTED",
            _ => return None,
        })
    }

    pub fn from_code(code: i64) -> Option<Self> {
        Self::ALL.into_iter().find(|candidate| candidate.code() == code)
    }
}
