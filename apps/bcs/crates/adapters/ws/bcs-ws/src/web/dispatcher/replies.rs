use super::*;


pub(super) async fn send_ok(tx: &mpsc::Sender<WorkbenchOutbound>, req_id: &str, payload: Value) -> Result<()> {
    let response = ResponseFrame::ok(req_id, payload);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(WorkbenchOutbound::PublicControl(json)).await.map_err(|e| {
        WebWsDispatchError::WsProtocolError(format!("Failed to send response: {}", e))
    })?;
    Ok(())
}

pub(super) async fn send_empty_human_input_final(
    tx: &mpsc::Sender<WorkbenchOutbound>,
    group_id: &str,
    session_id: Option<&str>,
    run_id: &str,
) -> Result<()> {
    // HumanInput consumes this chat.send without dispatching a Bot run. Emit a
    // terminal chat event on the same connection so the current frontend can
    // close its pending request without rendering an assistant message.
    let event = serde_json::json!({
        "type": "event",
        "event": "chat",
        "group_id": group_id,
        "bot_uuid": STATE_MACHINE_EVENT_BOT_UUID,
        "payload": {
            "run_id": run_id,
            "bcs_group_id": group_id,
            "bcs_session_id": session_id,
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [],
            },
        },
    });
    let json = serde_json::to_string(&event)?;
    tx.send(WorkbenchOutbound::PublicControl(json)).await.map_err(|error| {
        WebWsDispatchError::WsProtocolError(format!(
            "Failed to send HumanInput completion event: {}",
            error
        ))
    })?;
    Ok(())
}

pub(super) async fn send_human_input_error_event(
    tx: &mpsc::Sender<WorkbenchOutbound>,
    group_id: &str,
    session_id: Option<&str>,
    error_code: &str,
    error_message: &str,
) -> Result<()> {
    // The current group-chat SDK does not render ResponseFrame errors because
    // they have no bot_uuid. Keep the protocol response and add a chat error
    // event so the frontend can render the rejection and close its request.
    let event = serde_json::json!({
        "type": "event",
        "event": "chat",
        "group_id": group_id,
        "bot_uuid": STATE_MACHINE_EVENT_BOT_UUID,
        "payload": {
            "bcs_group_id": group_id,
            "bcs_session_id": session_id,
            "state": "error",
            "errorCode": error_code,
            "errorMessage": error_message,
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": error_message,
                }],
            },
        },
    });
    let json = serde_json::to_string(&event)?;
    tx.send(WorkbenchOutbound::PublicControl(json)).await.map_err(|error| {
        WebWsDispatchError::WsProtocolError(format!(
            "Failed to send HumanInput error event: {}",
            error
        ))
    })?;
    Ok(())
}

pub(super) async fn send_error(
    tx: &mpsc::Sender<WorkbenchOutbound>,
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
    tx: &mpsc::Sender<WorkbenchOutbound>,
    req_id: &str,
    error: ErrorShape,
) -> Result<()> {
    let response = ResponseFrame::err(req_id, error);
    let frame = BcsFrame::Response(response);
    let json = serde_json::to_string(&frame)?;
    tx.send(WorkbenchOutbound::PublicControl(json)).await.map_err(|e| {
        WebWsDispatchError::WsProtocolError(format!("Failed to send error response: {}", e))
    })?;
    Ok(())
}