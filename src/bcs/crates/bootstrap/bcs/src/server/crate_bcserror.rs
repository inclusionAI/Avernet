//! server implementation.
use super::*;


// ============================================================================
// Error handling
// ============================================================================

impl IntoResponse for crate::BcsError {
    fn into_response(self) -> axum::response::Response {
        let (status, message) = match &self {
            Self::SessionNotFound(id) => {
                (StatusCode::NOT_FOUND, format!("Session not found: {}", id))
            }
            // Issue #4 (E.6 regression 2026-04-29): GroupNotFound previously
            // fell through to the catch-all `_ => 500` arm, so `GET /groups/{id}`
            // for any non-existent (or just-deleted) group returned HTTP 500
            // instead of 404. The error body already says "Group not found",
            // so the only fix needed is the status-code mapping.
            Self::GroupNotFound(id) => (StatusCode::NOT_FOUND, format!("Group not found: {}", id)),
            Self::BotNotFound(id) => (StatusCode::NOT_FOUND, format!("Bot not found: {}", id)),
            Self::BotNotRegistered(id) => (
                StatusCode::NOT_FOUND,
                format!("Bot '{}' is not registered", id),
            ),
            Self::BotAlreadyConnected(id) => (
                StatusCode::CONFLICT,
                format!("Bot '{}' already has an active WebSocket connection", id),
            ),
            Self::BotNotConnected(id) => (
                StatusCode::NOT_FOUND,
                format!("Bot '{}' is not connected via WebSocket", id),
            ),
            Self::InvalidRequest(msg) => {
                (StatusCode::BAD_REQUEST, format!("Invalid request: {}", msg))
            }
            Self::NotFriends { bot, driver } => (
                StatusCode::FORBIDDEN,
                format!(
                    "Bot '{}' is protected and not a friend of '{}'",
                    bot, driver
                ),
            ),
            Self::BotPrivate(id) => (
                StatusCode::FORBIDDEN,
                format!(
                    "Bot '{}' is in private mode and cannot participate in collaboration network",
                    id
                ),
            ),
            Self::InvalidSessionToken => (
                StatusCode::UNAUTHORIZED,
                "Invalid or expired session token".to_string(),
            ),
            Self::Forbidden(msg) => (StatusCode::FORBIDDEN, msg.clone()),
            Self::Unauthorized(msg) => (StatusCode::UNAUTHORIZED, msg.clone()),
            Self::WsProtocolError(msg) => (
                StatusCode::BAD_REQUEST,
                format!("WebSocket protocol error: {}", msg),
            ),
            Self::InvalidFrameFormat(msg) => (
                StatusCode::BAD_REQUEST,
                format!("Invalid frame format: {}", msg),
            ),
            Self::ProposalNotFound(id) => (
                StatusCode::NOT_FOUND,
                format!("Proposal not found or expired: {}", id),
            ),
            Self::BotDirectoryNotFound(path) => (
                StatusCode::NOT_FOUND,
                format!("Bot directory not found: {}", path),
            ),
            Self::InvalidConfig(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::InvalidOperation(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyGroups(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyMembers(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            Self::TooManyMessages(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            _ => (StatusCode::INTERNAL_SERVER_ERROR, self.to_string()),
        };

        let variant = self.variant_name();
        if status.is_server_error() {
            tracing::error!(status = %status.as_u16(), error_type = %variant, backtrace = %std::backtrace::Backtrace::force_capture(), "{message}");
        } else if status.is_client_error() && status != StatusCode::NOT_FOUND {
            tracing::warn!(status = %status.as_u16(), error_type = %variant, "{message}");
        }

        let body = Json(serde_json::json!({
            "error": message,
            "status": status.as_u16()
        }));

        (status, body).into_response()
    }
}



pub async fn resolve_config_secrets(config: &mut BcsConfig, access: &dyn SecretAccessPort) -> crate::Result<()> {
    if let Some(value) = resolve_secret_value(config.auth_sdk.secret_key_secret.as_deref(), access, "auth_sdk.secret_key_secret").await? {
        config.auth_sdk.secret_key = Some(value);
    }
    if let Some(value) = resolve_secret_value(config.llm.api_key_secret.as_deref(), access, "llm.api_key_secret").await? {
        config.llm.api_key = Some(Secret::new(value));
        config.llm.api_key_env = None;
    }
    if let Some(value) = resolve_secret_value(
        config.bcsfuse.authorization_ref.as_deref(),
        access,
        "bcsfuse.authorization_ref",
    )
    .await?
    {
        config.bcsfuse.set_resolved_authorization(value);
    }
    if config.invite.token_secret_secret.as_deref().is_some_and(|v| !v.trim().is_empty()) {
        config.invite.token_secret = resolve_token_secret_secret(config.invite.token_secret_secret.as_deref(), access, "invite.token_secret_secret").await?;
    }
    if config.session_files.share.token_secret_secret.as_deref().is_some_and(|v| !v.trim().is_empty()) {
        config.session_files.share.token_secret = resolve_token_secret_secret(config.session_files.share.token_secret_secret.as_deref(), access, "session_files.share.token_secret_secret").await?;
    }
    for account in &mut config.dingtalk_accounts {
        if let Some(value) = resolve_secret_value(account.client_secret_secret.as_deref(), access, "dingtalk_accounts.client_secret_secret").await? {
        account.client_secret = Some(Secret::new(value));
        }
    }
    if let Some(logger) = config.group_logger.as_mut() {
        if let Some(value) = resolve_secret_value(logger.client_secret_secret.as_deref(), access, "group_logger.client_secret_secret").await? {
        logger.client_secret = value;
        }
    }
    // Any `<key>_secret` option inside a human_notify provider entry is a
    // secret-provider reference (e.g. Mist): resolve it here and inject the
    // plain value as `<key>`, then drop the reference. A non-blank literal
    // `<key>` wins over the reference so explicit values are never clobbered.
    // `client_secret_secret` (DingTalk notifier) and `signing_key_secret`
    // (work-order notifier) are instances of this rule. The DingTalk
    // notifier's literal secret field is `client_secret`, which itself ends
    // in `_secret`; it is excluded below so it is never mistaken for a
    // reference to a (nonexistent) `client` literal.
    for provider in &mut config.human_notify.providers {
        let provider_name = provider.name.clone();
        let reference_keys: Vec<String> = provider
            .options
            .keys()
            .filter(|key| key.ends_with("_secret") && key.as_str() != "client_secret")
            .cloned()
            .collect();
        for reference_key in reference_keys {
            let Some(reference) = provider
                .options
                .get(&reference_key)
                .and_then(|value| value.as_str())
                .map(str::trim)
                .filter(|value| !value.is_empty())
            else {
                continue;
            };
            let target_key = reference_key
                .strip_suffix("_secret")
                .expect("reference keys end with _secret")
                .to_string();
            let literal_wins = provider
                .options
                .get(&target_key)
                .and_then(|value| value.as_str())
                .is_some_and(|value| !value.trim().is_empty());
            if literal_wins {
                continue;
            }
            let field = format!("human_notify.providers.{provider_name}.{reference_key}");
            if let Some(value) = resolve_secret_value(Some(reference), access, &field).await? {
                provider
                    .options
                    .insert(target_key, serde_json::Value::String(value));
                provider.options.remove(&reference_key);
            }
        }
    }
    if let Some(oauth) = config.auth.oauth.as_mut() {
        for (provider_name, provider) in &mut oauth.providers {
        let field = format!("auth.oauth.providers.{provider_name}.client_secret_secret");
        if let Some(value) = resolve_secret_value(provider.client_secret_secret.as_deref(), access, &field).await? {
            provider.client_secret = Some(Secret::new(value));
        }
        }
    }
    Ok(())
}
