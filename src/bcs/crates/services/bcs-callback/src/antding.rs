//! AntDing channel sender.
//!
//! Pure send helper. Used by both:
//! - the legacy `bcs-service-group` callback path (preserved as-is)
//! - the new [`super::dispatch::dispatch_callback`] flow that ties
//!   callbacks to `Session.callback_status`
//!
//! On success the channel returns `Ok(())`. Any non-2xx HTTP status,
//! transport error, or business-level `success=false` produces an error
//! string suitable for logging.
//!
//! Ported from legacy `bcs/src/callback/antding.rs`.

use bcs_service_api::CallbackChannelConfig;
use tracing::info;

const ANTDING_CALLBACK_URL: &str = "https://callback.example.com/webapi/function/exe";

/// Dispatch one AntDing callback. Returns Ok on success, Err with a
/// human-readable reason on failure.
///
/// `extra` is merged into the AntDing payload as channel-specific
/// overrides (e.g. `callback_target.user_id`). Pass `None` when the
/// channel config carries the full target.
pub async fn send(
    channel: &CallbackChannelConfig,
    content: &str,
    extra: Option<&serde_json::Value>,
) -> Result<(), String> {
    let CallbackChannelConfig::AntDing {
        access_key_id,
        access_key_secret,
        robot_code,
        user_id,
        open_conversation_id,
    } = channel else {
        return Err("antding sender received non-antding callback channel".to_string());
    };
    send_antding_callback(
        access_key_id,
        access_key_secret,
        robot_code,
        user_id.as_deref(),
        open_conversation_id.as_deref(),
        content,
        extra,
    )
    .await
}

async fn send_antding_callback(
    access_key_id: &str,
    access_key_secret: &str,
    robot_code: &str,
    user_id: Option<&str>,
    open_conversation_id: Option<&str>,
    content: &str,
    instance_meta: Option<&serde_json::Value>,
) -> Result<(), String> {
    let meta_target = instance_meta.and_then(|m| m.get("callback_target"));
    let user_id = meta_target
        .and_then(|ct| ct.get("user_id"))
        .and_then(|v| v.as_str())
        .or(user_id)
        .unwrap_or("");
    let open_conversation_id = meta_target
        .and_then(|ct| ct.get("open_conversation_id"))
        .and_then(|v| v.as_str())
        .or(open_conversation_id)
        .unwrap_or("");

    let inner_params = serde_json::json!({
        "accessKeyId": access_key_id,
        "accessKeySecret": access_key_secret,
        "robotCode": robot_code,
        "content": content,
        "userId": user_id,
        "openConversationId": open_conversation_id,
    });

    let body = serde_json::json!({
        "functionName": "example.callback.sendTextMessage",
        "env": "PROD",
        "params": inner_params.to_string(),
    });

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| format!("antding client build failed: {e}"))?;
    let resp = client
        .post(ANTDING_CALLBACK_URL)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("antding HTTP request failed: {e}"))?;

    let status = resp.status();
    let resp_body = resp.text().await.unwrap_or_default();

    if !status.is_success() {
        return Err(format!(
            "antding callback failed: HTTP {status}, body: {resp_body}"
        ));
    }

    if let Ok(resp_json) = serde_json::from_str::<serde_json::Value>(&resp_body) {
        // The function.alipay wrapper uses outer `success: bool`. The
        // inner `data.success` may be the string `"false"` when the
        // business operation fails (e.g. missing userId).
        let outer_ok = resp_json
            .get("success")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let data_ok = resp_json
            .get("data")
            .and_then(|d| d.get("success"))
            .map(|v| {
                // Handle both the boolean false and the string "false".
                if let Some(b) = v.as_bool() {
                    b
                } else if let Some(s) = v.as_str() {
                    s != "false"
                } else {
                    true
                }
            })
            .unwrap_or(true);

        if !outer_ok || !data_ok {
            let msg = resp_json
                .pointer("/data/message")
                .and_then(|v| v.as_str())
                .or_else(|| resp_json.get("msg").and_then(|v| v.as_str()))
                .unwrap_or("unknown error");
            return Err(format!("antding callback business error: {msg}"));
        }
    }

    info!(
        target: "antding",
        event = "antding.sent",
        user_id = %user_id,
        robot_code = %robot_code,
        http_status = %status,
        response = %resp_body,
    );
    Ok(())
}
