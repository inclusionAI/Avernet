//! Temporary, explicitly preproduction-only HTTP caller-token diagnostics.

use bcs_protocol::ProviderWebhookRequest;

pub(super) fn log_caller_token(body: &ProviderWebhookRequest, message_id: &str) {
    if body.method != "chat.send" {
        return;
    }
    let Some(credential) = body
        .extensions
        .as_ref()
        .and_then(|extensions| extensions.get("caller_execution_token"))
    else {
        return;
    };
    if credential.get("type").and_then(serde_json::Value::as_str) != Some("iam-token") {
        return;
    }
    let Some(token) = credential
        .get("token")
        .and_then(serde_json::Value::as_str)
        .filter(|token| !token.is_empty())
    else {
        return;
    };
    // Reversible JSON quoting preserves the complete value (including whitespace)
    // without allowing control characters to inject extra log lines.
    let token_json = serde_json::json!(token).to_string();
    tracing::warn!(
        provider_id = %body.to_bot.provider_id,
        provider_bot_ref = %body.to_bot.provider_bot_ref,
        run_id = %body.id,
        session_id = %body.session_id,
        message_id,
        token_bytes = token.len(),
        caller_token_json = %token_json,
        "TEMP_PREPUB_CALLER_TOKEN"
    );
}
