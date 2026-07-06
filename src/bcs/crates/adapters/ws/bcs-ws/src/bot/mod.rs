pub mod connection_registry;
pub mod dispatcher;
pub mod handler;

pub const BOT_WS_ENDPOINT: &str = "/ws/bot";

pub use connection_registry::BotConnectionRegistry;
pub use dispatcher::{
    dispatch_frame, BotDispatchOutcome, BotDispatchState, BotWsDispatchError, TaskCallbackHook,
};
pub use handler::handle_connection;

/// Decode the `exp` claim from a JWT token without signature verification.
/// Returns the expiry as unix timestamp (seconds), or None if not a valid JWT.
pub fn decode_jwt_exp(token: &str) -> Option<u64> {
    use base64::Engine;
    let token = token.strip_prefix("Bearer ").unwrap_or(token);
    let parts: Vec<&str> = token.splitn(3, '.').collect();
    if parts.len() != 3 {
        return None;
    }
    let payload_bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(parts[1])
        .or_else(|_| base64::engine::general_purpose::URL_SAFE.decode(parts[1]))
        .ok()?;
    let payload: serde_json::Value = serde_json::from_slice(&payload_bytes).ok()?;
    payload.get("exp")?.as_u64()
}

/// Port for backfilling agent_code/agent_token into bot_info after WS connect.
#[async_trait::async_trait]
pub trait AgentCredentialBackfillPort: Send + Sync {
    async fn backfill(
        &self,
        bot_uuid: &str,
        agent_token: Option<String>,
        agent_code_header: Option<String>,
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;

    fn make_jwt(payload_json: &str) -> String {
        let header = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(r#"{"alg":"HS256","typ":"JWT"}"#);
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload_json);
        let signature = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode("fakesig");
        format!("{header}.{payload}.{signature}")
    }

    #[test]
    fn decode_jwt_exp_valid() {
        let token = make_jwt(r#"{"sub":"bot","exp":1700000000}"#);
        assert_eq!(decode_jwt_exp(&token), Some(1700000000));
    }

    #[test]
    fn decode_jwt_exp_with_bearer_prefix() {
        let token = format!("Bearer {}", make_jwt(r#"{"exp":1800000000}"#));
        assert_eq!(decode_jwt_exp(&token), Some(1800000000));
    }

    #[test]
    fn decode_jwt_exp_no_exp_field() {
        let token = make_jwt(r#"{"sub":"bot","iat":1700000000}"#);
        assert_eq!(decode_jwt_exp(&token), None);
    }

    #[test]
    fn decode_jwt_exp_exp_not_number() {
        let token = make_jwt(r#"{"exp":"not-a-number"}"#);
        assert_eq!(decode_jwt_exp(&token), None);
    }

    #[test]
    fn decode_jwt_exp_not_jwt_no_dots() {
        assert_eq!(decode_jwt_exp("plain-token-no-dots"), None);
    }

    #[test]
    fn decode_jwt_exp_only_two_segments() {
        assert_eq!(decode_jwt_exp("header.payload"), None);
    }

    #[test]
    fn decode_jwt_exp_invalid_base64() {
        assert_eq!(decode_jwt_exp("header.!!!invalid!!!.signature"), None);
    }

    #[test]
    fn decode_jwt_exp_empty_string() {
        assert_eq!(decode_jwt_exp(""), None);
    }

    #[test]
    fn decode_jwt_exp_bearer_only() {
        assert_eq!(decode_jwt_exp("Bearer "), None);
    }
}
