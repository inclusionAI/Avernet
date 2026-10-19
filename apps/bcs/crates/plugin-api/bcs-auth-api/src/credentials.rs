//! Reject ambiguous credentials before they can select different Bot identities.
use axum::http::HeaderMap;
use crate::{AuthError, AuthPrincipal};

pub fn validate_bot_credentials(headers: &HeaderMap) -> Result<(), AuthError> {
    for name in ["authorization", "x-bcs-bot-token", "x-avernet-bot-token"] {
        if headers.get_all(name).iter().count() > 1 { return Err(conflict()); }
    }
    let first = value(headers,"x-bcs-bot-token");
    let second = value(headers,"x-avernet-bot-token");
    if first.is_some() && second.is_some() && first != second { return Err(conflict()); }
    Ok(())
}

pub(crate) fn validate_resolved_credentials(headers: &HeaderMap, principal: &AuthPrincipal) -> Result<(), AuthError> {
    if principal.source_name.as_deref() == Some("AgentPass") && principal.bot_uuid.is_some() {
        let dedicated = value(headers,"x-bcs-bot-token").or_else(|| value(headers,"x-avernet-bot-token"));
        if dedicated.is_some() && dedicated != principal.token.as_deref() { return Err(conflict()); }
    }
    Ok(())
}
fn value<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|value| value.to_str().ok()).map(str::trim).filter(|value| !value.is_empty())
}
fn conflict() -> AuthError { AuthError::InvalidToken("Conflicting Bot credentials".into()) }
