use bcs_protocol::{BcsFrame, ErrorShape, ResponseFrame};
use serde_json::Value;
use tokio::sync::mpsc;
use super::{Result, WebWsDispatchError};

pub(super) async fn send_ok(tx: &mpsc::Sender<String>, req_id: &str, payload: Value) -> Result<()> {
    let response = ResponseFrame::ok(req_id, payload);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        WebWsDispatchError::WsProtocolError(format!("Failed to send response: {}", e))
    })?;
    Ok(())
}

pub(super) async fn send_error(
    tx: &mpsc::Sender<String>,
    req_id: &str,
    code: &str,
    message: &str,
) -> Result<()> {
    send_error_shape(
        tx,
        req_id,
        ErrorShape {
            code: code.to_string(),
            message: message.to_string(),
            details: None,
            retryable: false,
            retry_after_ms: None,
        },
    )
    .await
}

pub(super) async fn send_error_shape(
    tx: &mpsc::Sender<String>,
    req_id: &str,
    error: ErrorShape,
) -> Result<()> {
    let response = ResponseFrame::err(req_id, error);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(json).await.map_err(|e| {
        WebWsDispatchError::WsProtocolError(format!("Failed to send error response: {}", e))
    })?;
    Ok(())
}
