//! Optional expected caller identity. A selector is never an authentication credential.
use axum::http::HeaderMap;
use crate::AuthError;

pub const BCN_ACTOR_BOT_ID_HEADER: &str = "x-bcn-actor-bot-id";

pub fn expected_actor_id(headers: &HeaderMap) -> Result<Option<&str>, AuthError> {
    let mut values = headers.get_all(BCN_ACTOR_BOT_ID_HEADER).iter();
    let Some(value) = values.next() else { return Ok(None); };
    if values.next().is_some() {
        return Err(AuthError::InvalidToken("duplicate Actor header".into()));
    }
    let value = value.to_str().map_err(|_| AuthError::InvalidToken("invalid Actor header".into()))?;
    if value.is_empty() || value.len() > 256 || value.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err(AuthError::InvalidToken("invalid Actor header".into()));
    }
    Ok(Some(value))
}

pub fn validate_actor(expected: Option<&str>, actual: &str) -> Result<(), AuthError> {
    if expected.is_some_and(|id| id != actual) {
        return Err(AuthError::Forbidden("Actor does not match authenticated Bot".into()));
    }
    Ok(())
}
