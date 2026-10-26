//! Short-lived credentials are separate from durable message/delivery JSON.
use crate::BcsMessageFlow;
use bcs_cache_api::CacheSetMode;
use bcs_protocol::BcsFrame;
use bcs_service_api::{BotDeliveryTarget, ServiceError, ServiceResult, WebSendCommand};
use bcs_service_api::application::caller_execution::CallerExecution;
use serde::{Deserialize, Serialize};
use std::time::Duration;

const TTL_MS: u64 = 300_000;

/// Non-secret reference scoped to the original channel message and direct Bot.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ExecutionRef {
    reference: String,
    binding_id: String,
    source_message_id: String,
    target_bot_id: String,
    user_id: String,
    group_id: String,
    session_id: String,
}

impl ExecutionRef {
    pub(crate) fn from_command(cmd: &WebSendCommand) -> Option<Self> {
        cmd.caller_execution.as_ref().map(|c| Self {
            reference: c.reference.clone(), binding_id: c.binding_id.clone(),
            source_message_id: c.source_message_id.clone(), target_bot_id: c.target_bot_id.clone(),
            user_id: c.user_id.clone(), group_id: cmd.group_id.clone(),
            session_id: cmd.session_id.clone().unwrap_or_default(),
        })
    }
    fn key(&self) -> String { format!("bcs:caller-execution:v1:{}", self.reference) }
}

// Deliberately no Debug; only this private cache record may serialize the token.
#[derive(Serialize, Deserialize)]
struct CachedExecution {
    scope: ExecutionRef,
    expires_at_ms: u64,
    token_type: String,
    token: String,
}

fn invalid() -> ServiceError {
    ServiceError::InvalidOperation {
        message: "caller credential unavailable, expired or outside its direct Bot scope; resend the message".into(),
        request_id: None,
    }
}

pub(crate) async fn store(flow: &BcsMessageFlow, cmd: &WebSendCommand) -> ServiceResult<()> {
    let Some(scope) = ExecutionRef::from_command(cmd) else { return Ok(()); };
    let c: &CallerExecution = cmd.caller_execution.as_ref().ok_or_else(invalid)?;
    if c.token.expose().trim().is_empty() || c.token_type != "iam-token" || c.user_id.trim().is_empty()
        || c.binding_id.is_empty() || c.source_message_id.is_empty() || c.reference.is_empty()
        || scope.session_id.is_empty() || cmd.source_im_message_id.as_deref() != Some(&c.source_message_id) {
        return Err(invalid());
    }
    let cache = flow.execution_cache.as_ref().ok_or_else(invalid)?;
    let key = scope.key();
    let record = CachedExecution { scope, expires_at_ms: bcs_protocol::now_ms().saturating_add(TTL_MS),
        token_type: c.token_type.clone(), token: c.token.expose().to_string() };
    let value = serde_json::to_vec(&record).map_err(|_| invalid())?;
    if !cache.set_value(&key, value, Some(Duration::from_millis(TTL_MS)), CacheSetMode::InsertOnly)
        .await.map_err(|_| invalid())? { return Err(invalid()); }
    Ok(())
}

pub(crate) async fn apply(
    flow: &BcsMessageFlow, scope: &ExecutionRef, target: &BotDeliveryTarget,
    bot_id: &str, group_id: &str, session_id: &str, source_message_id: Option<&str>,
    frame: &mut BcsFrame,
) -> ServiceResult<u64> {
    if !target.is_http_provider() || scope.target_bot_id != bot_id || scope.group_id != group_id
        || scope.session_id != session_id || source_message_id != Some(scope.source_message_id.as_str()) {
        return Err(invalid());
    }
    let bytes = flow.execution_cache.as_ref().ok_or_else(invalid)?.get_value(&scope.key()).await
        .map_err(|_| invalid())?.ok_or_else(invalid)?;
    let record: CachedExecution = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
    if &record.scope != scope || record.expires_at_ms <= bcs_protocol::now_ms()
        || record.token_type != "iam-token" || record.token.trim().is_empty() { return Err(invalid()); }
    let BcsFrame::Request(request) = frame else { return Err(invalid()); };
    if request.method != "chat.send" { return Err(invalid()); }
    let params = request.params.as_mut().and_then(|p| p.as_object_mut()).ok_or_else(invalid)?;
    let extensions = params.entry("extensions").or_insert_with(|| serde_json::json!({}));
    let extensions = extensions.as_object_mut().ok_or_else(invalid)?;
    extensions.insert("caller_context".into(), serde_json::json!({"subject": {"kind": "human", "ref": scope.user_id}}));
    extensions.insert("caller_execution_token".into(), serde_json::json!({"type": record.token_type, "token": record.token}));
    Ok(record.expires_at_ms)
}
