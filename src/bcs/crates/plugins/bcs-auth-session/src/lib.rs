//! Session bot-token auth plugin (priority 30).
//!
//! Handles non-JWT bearer tokens / `X-BCS-Bot-Token`, resolving them via the
//! injected `BotLookupPort`.

use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;

use bcs_auth_api::{
    is_jwt_format, AuthError, AuthPlugin, AuthPrincipal, AuthSource, BotLookupPort,
};

/// Extract a bot session token: `X-BCS-Bot-Token` wins, else a non-JWT Bearer.
pub fn extract_bot_token(headers: &HeaderMap) -> Option<String> {
    if let Some(v) = headers.get("x-bcs-bot-token").and_then(|v| v.to_str().ok()) {
        let t = v.trim();
        if !t.is_empty() {
            return Some(t.to_string());
        }
    }
    let auth = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())?;
    let token = auth.strip_prefix("Bearer ").unwrap_or(auth).trim();
    if token.is_empty() || is_jwt_format(token) {
        return None;
    }
    Some(token.to_string())
}

pub struct SessionTokenPlugin {
    lookup: Arc<dyn BotLookupPort>,
}

impl SessionTokenPlugin {
    pub fn new(lookup: Arc<dyn BotLookupPort>) -> Self {
        Self { lookup }
    }
}

#[async_trait]
impl AuthPlugin for SessionTokenPlugin {
    fn can_authenticate(&self, headers: &HeaderMap) -> bool {
        extract_bot_token(headers).is_some()
    }

    async fn authenticate(
        &self,
        headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        let Some(token) = extract_bot_token(headers) else {
            return Ok(None);
        };
        match self.lookup.find_bot_by_token(&token).await? {
            Some(info) => {
                let mut p = AuthPrincipal::new(AuthSource::SessionToken);
                p.bot_uuid = Some(info.bot_uuid);
                p.owner_id = info.owner_id;
                p.token = Some(token);
                Ok(Some(p))
            }
            None => Ok(None),
        }
    }

    fn priority(&self) -> u8 {
        30
    }

    fn name(&self) -> &'static str {
        "session"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;
    use bcs_auth_api::BotInfo;

    struct FakeLookup {
        bot: Option<BotInfo>,
    }

    #[async_trait]
    impl BotLookupPort for FakeLookup {
        async fn find_bot_by_token(&self, _t: &str) -> Result<Option<BotInfo>, AuthError> {
            Ok(self.bot.clone())
        }
    }

    fn headers_with_bearer(v: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert(axum::http::header::AUTHORIZATION, HeaderValue::from_str(v).unwrap());
        h
    }

    #[test]
    fn extracts_non_jwt_bearer() {
        // Uses a synthetic test UUID (all-zero + 1) to avoid credential-scan false positives.
        let h = headers_with_bearer("Bearer 00000000-0000-0000-0000-000000000001");
        assert_eq!(
            extract_bot_token(&h).as_deref(),
            Some("00000000-0000-0000-0000-000000000001")
        );
    }

    #[test]
    fn ignores_jwt_bearer() {
        let h = headers_with_bearer("Bearer eyJh.eyJz.sig");
        assert!(extract_bot_token(&h).is_none());
    }

    #[test]
    fn x_bcs_bot_token_wins() {
        let mut h = headers_with_bearer("Bearer eyJh.eyJz.sig");
        h.insert("x-bcs-bot-token", HeaderValue::from_static("raw-token"));
        assert_eq!(extract_bot_token(&h).as_deref(), Some("raw-token"));
    }

    #[tokio::test]
    async fn authenticates_known_token() {
        let lookup = Arc::new(FakeLookup {
            bot: Some(BotInfo {
                bot_uuid: "bot-1".into(),
                owner_id: Some("o-1".into()),
            }),
        });
        let plugin = SessionTokenPlugin::new(lookup);
        let h = headers_with_bearer("Bearer raw-uuid-token");
        let p = plugin.authenticate(&h).await.unwrap().unwrap();
        assert_eq!(p.bot_uuid.as_deref(), Some("bot-1"));
        assert_eq!(p.owner_id.as_deref(), Some("o-1"));
    }

    #[tokio::test]
    async fn unknown_token_is_none() {
        let lookup = Arc::new(FakeLookup { bot: None });
        let plugin = SessionTokenPlugin::new(lookup);
        let h = headers_with_bearer("Bearer raw-uuid-token");
        assert!(plugin.authenticate(&h).await.unwrap().is_none());
    }
}
