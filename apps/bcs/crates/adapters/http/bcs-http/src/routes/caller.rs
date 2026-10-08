use axum::http::{HeaderMap, Uri};

#[cfg(test)]
use axum::http::header;

use crate::{error::HttpAdapterError, state::HttpAppState};

pub(super) fn bot_token_from_headers(headers: &HeaderMap) -> Option<String> {
    if let Some(token) = headers
        .get("X-BCS-Bot-Token")
        .and_then(|value| value.to_str().ok())
        .filter(|token| !token.is_empty())
    {
        return Some(token.to_string());
    }

    crate::headers::extract_bearer_token(headers)
}

pub(super) async fn bot_id_from_headers(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Option<String> {
    state.bot_uuid_from_headers(headers).await
}

pub(super) async fn require_bot_id_from_headers(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Result<String, HttpAdapterError> {
    bot_id_from_headers(state, headers)
        .await
        .ok_or_else(|| HttpAdapterError::Unauthorized("valid bot token is required".to_string()))
}

pub(super) async fn authenticated_bot_from_headers(
    state: &HttpAppState,
    headers: &HeaderMap,
) -> Result<String, HttpAdapterError> {
    let bot_id = require_bot_id_from_headers(state, headers).await?;
    validate_container_header(state, headers, &bot_id)?;
    Ok(bot_id)
}

pub(super) async fn caller_actor_id_from_headers(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<String> {
    if let Some(bot_id) = state.bot_uuid_from_headers(headers).await {
        return Some(bot_id);
    }

    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{staff_no}"))
}

pub(super) async fn require_caller_actor_id_from_headers(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, HttpAdapterError> {
    caller_actor_id_from_headers(state, headers, uri)
        .await
        .ok_or_else(|| {
            HttpAdapterError::Unauthorized(
                "valid bot token or human identity is required".to_string(),
            )
        })
}

pub(super) fn container_header_matches(
    state: &HttpAppState,
    headers: &HeaderMap,
    requester_bot_id: &str,
) -> bool {
    let Some(container_bot_id) = headers
        .get("x-agentclaw-bolt-id")
        .and_then(|value| value.to_str().ok())
    else {
        return true;
    };

    !state.strict_container_validation || requester_bot_id.contains(container_bot_id)
}

pub(super) fn validate_container_header(
    state: &HttpAppState,
    headers: &HeaderMap,
    requester_bot_id: &str,
) -> Result<(), HttpAdapterError> {
    if container_header_matches(state, headers, requester_bot_id) {
        return Ok(());
    }

    Err(HttpAdapterError::Unauthorized(
        "valid bot token is required".to_string(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_bot_token_from_x_bcs_bot_token() {
        let mut headers = HeaderMap::new();
        headers.insert("X-BCS-Bot-Token", "bot-123".parse().unwrap());

        assert_eq!(
            bot_token_from_headers(&headers),
            Some("bot-123".to_string())
        );
    }

    #[test]
    fn extracts_bearer_token_when_x_bcs_bot_token_is_absent() {
        let mut headers = HeaderMap::new();
        headers.insert(header::AUTHORIZATION, "Bearer bearer-123".parse().unwrap());

        assert_eq!(
            bot_token_from_headers(&headers),
            Some("bearer-123".to_string())
        );
    }

    #[test]
    fn x_bcs_bot_token_takes_precedence_over_bearer_token() {
        let mut headers = HeaderMap::new();
        headers.insert("X-BCS-Bot-Token", "bot-123".parse().unwrap());
        headers.insert(header::AUTHORIZATION, "Bearer bearer-123".parse().unwrap());

        assert_eq!(
            bot_token_from_headers(&headers),
            Some("bot-123".to_string())
        );
    }

    #[test]
    fn empty_x_bcs_bot_token_falls_back_to_bearer_token() {
        let mut headers = HeaderMap::new();
        headers.insert("X-BCS-Bot-Token", "".parse().unwrap());
        headers.insert(header::AUTHORIZATION, "Bearer bearer-123".parse().unwrap());

        assert_eq!(
            bot_token_from_headers(&headers),
            Some("bearer-123".to_string())
        );
    }

    #[test]
    fn malformed_bearer_token_is_ignored() {
        let mut headers = HeaderMap::new();
        headers.insert(header::AUTHORIZATION, "Basic bearer-123".parse().unwrap());

        assert_eq!(bot_token_from_headers(&headers), None);
    }

    #[test]
    fn bearer_scheme_is_case_insensitive() {
        for scheme in ["bearer", "Bearer", "BEARER", "BeArEr"] {
            let mut headers = HeaderMap::new();
            headers.insert(
                header::AUTHORIZATION,
                format!("{scheme} bearer-123").parse().unwrap(),
            );

            assert_eq!(
                bot_token_from_headers(&headers),
                Some("bearer-123".to_string()),
                "scheme {scheme:?} should be accepted"
            );
        }
    }

    #[test]
    fn empty_bearer_token_returns_none() {
        let mut headers = HeaderMap::new();
        headers.insert(header::AUTHORIZATION, "Bearer ".parse().unwrap());

        assert_eq!(bot_token_from_headers(&headers), None);
    }
}
