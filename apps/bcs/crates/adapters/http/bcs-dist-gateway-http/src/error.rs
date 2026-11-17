//! Application errors as A2A errors.
//!
//! | `GatewayError` | JSON-RPC code |
//! | --- | --- |
//! | `NotFound` | -32001 TaskNotFound |
//! | `NotCancelable` | -32002 TaskNotCancelable |
//! | `AccessDenied` | -32600, "access denied" |
//! | `Conflict`, `Invalid` | -32602 |
//! | `Quarantined` | -32603, "session unavailable" |
//! | `Unavailable`, `OutcomeUnknown` | -32603 with a `retryable` detail |

use a2a::{A2AError, JsonRpcError, TypedDetail, error_code};
use bcs_service_api::application::dist_gateway::GatewayError;
use serde_json::{Value, json};
use std::collections::HashMap;

fn retryable(error: A2AError, code: &str) -> A2AError {
    let detail: HashMap<String, Value> =
        HashMap::from([("retryable".to_owned(), json!(true)), ("code".to_owned(), json!(code))]);
    error.with_details(vec![TypedDetail::from_struct(detail)])
}

pub(crate) fn a2a_error(error: &GatewayError) -> A2AError {
    match error {
        GatewayError::NotFound => A2AError::new(error_code::TASK_NOT_FOUND, "task not found"),
        GatewayError::NotCancelable => A2AError::new(
            error_code::TASK_NOT_CANCELABLE,
            "task is finished and cannot be canceled",
        ),
        GatewayError::AccessDenied => A2AError::invalid_request("access denied"),
        GatewayError::Conflict => {
            A2AError::invalid_params("the same message id was already used with different content")
        }
        GatewayError::Invalid { code } => A2AError::invalid_params(format!("invalid input: {code}")),
        GatewayError::Quarantined => A2AError::internal("session unavailable"),
        GatewayError::Unavailable { code } => retryable(A2AError::internal("dependency unavailable"), code),
        GatewayError::OutcomeUnknown { .. } => retryable(
            A2AError::internal("admission outcome unknown; retry the same request"),
            "outcome_unknown",
        ),
    }
}

pub(crate) fn rpc_error(error: &GatewayError) -> JsonRpcError {
    a2a_error(error).to_jsonrpc_error()
}
