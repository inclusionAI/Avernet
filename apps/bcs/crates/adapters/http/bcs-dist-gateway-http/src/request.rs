//! Inbound JSON-RPC envelope. The method selects its A2A parameter type at extraction.
use a2a::{
    A2AError, CancelTaskRequest, GetTaskRequest, JsonRpcId, ListTasksRequest, SendMessageRequest,
    SubscribeToTaskRequest,
};
use serde::{Deserialize, de::IgnoredAny};

#[derive(Deserialize)]
pub(crate) struct RpcRequest {
    pub jsonrpc: String,
    pub id: JsonRpcId,
    #[serde(flatten)]
    pub call: Method,
}

#[derive(Deserialize)]
#[serde(tag = "method")]
pub(crate) enum Method {
    SendMessage(Arguments<SendMessageRequest>),
    SendStreamingMessage(Arguments<SendMessageRequest>),
    GetTask(Arguments<GetTaskRequest>),
    SubscribeToTask(Arguments<SubscribeToTaskRequest>),
    CancelTask(Arguments<CancelTaskRequest>),
    ListTasks(Arguments<ListTasksRequest>),
    #[serde(untagged)]
    Other {
        method: String,
    },
}

/// Retain the envelope and request id for invalid params so authentication and
/// protocol version checks still take precedence over the JSON-RPC error.
#[derive(Deserialize)]
#[serde(untagged)]
pub(crate) enum Arguments<T> {
    Parsed { params: Named<T> },
    Invalid(IgnoredAny),
}

/// A2A's JSON-RPC binding uses named fields. Flattening the SDK type into a
/// map rejects positional arrays, even for one-field SDK request structs.
#[derive(Deserialize)]
pub(crate) struct Named<T> {
    #[serde(flatten)]
    value: T,
}

impl<T> Arguments<T> {
    pub fn parse(self) -> Result<T, A2AError> {
        match self {
            Self::Parsed { params } => Ok(params.value),
            Self::Invalid(_) => Err(A2AError::invalid_params(
                "expected named parameters matching the A2A method",
            )),
        }
    }
}
