//! bot.connect / bot.status handshake and status handling.

use std::collections::HashMap;
use std::sync::Arc;

use bcs_protocol::{
    BCS_DEFAULT_PROTOCOL_VERSION, BCS_MIN_SUPPORTED_VERSION, BCS_PROTOCOL_VERSION,
    BotConnectCapabilities, BotConnectParams as WireBotConnectParams, BotConnectResponse,
    BotStatus as WsBotStatus, BotStatusParams,
};
use bcs_service_api::{
    BotRuntimeConnectCommand, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand, ServiceError,
};
use serde_json::Value;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

use super::error::{map_bot_use_case_error, send_error, send_ok};
use super::{BotDispatchState, BotWsDispatchError, Result};
use crate::bot::normalize_client_kind;

const BOT_DELIVERY_IS_PROVIDER_CODE: &str = "bot_delivery_is_provider";
const BOT_DELIVERY_IS_PROVIDER_MESSAGE: &str = "Bot delivery is configured for HttpProvider; WebSocket uplink is no longer accepted for this bot";

/// Handle bot.connect request (initial connection handshake).
///
/// This method is used for the initial connection handshake:
/// - If token is valid and bot exists → return is_new: false with bot_id (reconnection)
/// - If token is empty/invalid → return is_new: true with new token (onboarding needed)
/// - If bot_id is provided (preconfigured) → use that bot_id instead of generating one
///
/// For new bots, BCS will send a chat.send event telling the bot to onboard.
/// For existing bots, capabilities are loaded from storage.
pub(super) async fn handle_bot_connect(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &mut Option<String>,
) -> Result<()> {
    // Parse params
    if registered_bot_id.is_some() {
        send_error(tx, req_id, "already_connected", "This socket already has a Bot identity").await?;
        return Ok(());
    }
    let params: WireBotConnectParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid connect params: {}", e))
    })?;

    // Protocol version negotiation
    let requested_version = params
        .protocol_version
        .unwrap_or(BCS_DEFAULT_PROTOCOL_VERSION);
    if requested_version < BCS_MIN_SUPPORTED_VERSION || requested_version > BCS_PROTOCOL_VERSION {
        send_error(
            tx,
            req_id,
            "unsupported_protocol_version",
            &format!(
                "Protocol version {} is not supported. Supported range: {}-{}",
                requested_version, BCS_MIN_SUPPORTED_VERSION, BCS_PROTOCOL_VERSION
            ),
        )
        .await?;
        return Ok(());
    }

    if let Some(bot_id) = params.bot_id.as_deref() {
        if reject_provider_delivery_websocket(state, tx, req_id, bot_id).await? {
            return Ok(());
        }
    }

    let requested_client_kind = normalize_client_kind(params.client_kind);
    let result = state
        .bot_runtime
        .connect_streaming(BotRuntimeConnectCommand {
            caller_actor_id: state.expected_actor_id.clone(),
            token: params.token,
            bot_id: params.bot_id,
            protocol_version: Some(requested_version),
            client_kind: requested_client_kind,
        })
        .await
        .map_err(map_bot_use_case_error)?;

    if reject_provider_delivery_websocket(state, tx, req_id, &result.bot_uuid).await? {
        state
            .bot_runtime
            .disconnect_streaming(BotRuntimeDisconnectCommand {
                bot_id: result.bot_uuid,
            })
            .await
            .map_err(map_bot_use_case_error)?;
        return Ok(());
    }

    // Update registered_bot_id
    *registered_bot_id = Some(result.bot_uuid.clone());

    let negotiated_client_kind = result.negotiated_client_kind.clone();

    state
        .bot_connections
        .connect_with_protocol(
            result.bot_uuid.clone(),
            tx.clone(),
            requested_version,
            negotiated_client_kind.clone(),
        )
        .await;

    // Send response with env for child processes
    let mut env_map = HashMap::new();
    env_map.insert("BCN_BOT_UUID".to_string(), result.bot_uuid.clone());
    env_map.insert("BCN_BOT_TOKEN".to_string(), result.token.clone());

    let response = BotConnectResponse {
        is_new: result.is_new,
        token: result.token,
        bot_uuid: result.bot_uuid,
        protocol_version: requested_version,
        min_supported_version: BCS_MIN_SUPPORTED_VERSION,
        deprecation: None,
        capabilities: Some(BotConnectCapabilities::for_connection(
            requested_version,
            matches!(negotiated_client_kind.as_deref(), Some("native_mcp" | "mcporter_mcp")),
        )),
        env: Some(env_map),
    };
    send_ok(tx, req_id, serde_json::to_value(response)?).await?;

    Ok(())
}

async fn reject_provider_delivery_websocket(
    state: &Arc<BotDispatchState>,
    tx: &mpsc::Sender<String>,
    req_id: &str,
    bot_id: &str,
) -> Result<bool> {
    match state.bot_runtime.is_provider_downlink_bot(bot_id).await {
        Ok(true) => {
            send_error(
                tx,
                req_id,
                BOT_DELIVERY_IS_PROVIDER_CODE,
                BOT_DELIVERY_IS_PROVIDER_MESSAGE,
            )
            .await?;
            Ok(true)
        }
        Ok(false) | Err(ServiceError::BotNotFound(_)) => Ok(false),
        Err(err) => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                bot_id = %bot_id,
                error = %err,
                "provider delivery guard failed; allowing WebSocket connect"
            );
            Ok(false)
        }
    }
}

/// Handle bot.status request.
pub(super) async fn handle_bot_status(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    // Must be registered first
    let bot_id = match registered_bot_id {
        Some(id) => id.clone(),
        None => {
            send_error(
                tx,
                req_id,
                "not_registered",
                "Bot must register before sending status",
            )
            .await?;
            return Ok(());
        }
    };

    // Parse params
    let params: BotStatusParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid status params: {}", e))
    })?;

    debug!(
        bot_id = %bot_id,
        status = ?params.status,
        dynamic_summary = ?params.dynamic_summary,
        load = ?params.load,
        "Processing bot.status"
    );

    // Convert WS status to internal format
    let status_str = match params.status.unwrap_or(WsBotStatus::Idle) {
        WsBotStatus::Idle => "idle",
        WsBotStatus::Busy => "busy",
        WsBotStatus::Error => "error",
    };

    let status = bcs_service_api::BotDynamicStatus {
        status: status_str.to_string(),
        dynamic_summary: params.dynamic_summary,
        load: params.load,
        updated_at: Some(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
        ),
    };

    let outcome = state
        .bot_runtime
        .update_runtime_status(BotRuntimeStatusCommand {
            caller_actor_id: Some(bot_id.clone()),
            bot_id: bot_id.clone(),
            status,
        })
        .await
        .map_err(map_bot_use_case_error)?;

    if !outcome.updated {
        warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Failed to update status - bot not found in registry");
        send_error(tx, req_id, "not_registered", "Bot not found in registry").await?;
        return Ok(());
    }

    info!(
        bot_id = %bot_id,
        status = status_str,
        "Bot status updated via WebSocket"
    );

    // Send success response
    send_ok(tx, req_id, serde_json::json!({"updated": true})).await?;

    Ok(())
}
