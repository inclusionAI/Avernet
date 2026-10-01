//! HTTP adapter that asks backend to sync a human->bot friend relationship to
//! AceAgent. Forwards the gateway principal to authenticate at the backend
//! (same as HttpFriendConnectNotificationPort); does NOT send the openapi
//! Authorization/Cookie identity.

use async_trait::async_trait;
use bcs_service_api::port::{
    FriendAuthSyncAction, FriendAuthSyncCommand, FriendAuthSyncPort,
};
use bcs_service_api::{ServiceError, ServiceResult};
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tracing::{info, warn};

const SYNC_TIMEOUT: Duration = Duration::from_secs(10);

const SYNC_PATH: &str = "/api/internal/bot-friend-auth/sync";

#[derive(Debug, Clone)]
pub struct HttpFriendAuthSyncPort {
    client: reqwest::Client,
    base_url: reqwest::Url,
}

impl HttpFriendAuthSyncPort {
    pub fn new(base_url: &str) -> Result<Self, ServiceError> {
        let base_url = base_url.trim();
        if base_url.is_empty() {
            return Err(ServiceError::InternalError(
                "friend-auth-sync base url must not be blank".to_string(),
            ));
        }
        let base_url = reqwest::Url::parse(base_url).map_err(|error| {
            ServiceError::InternalError(format!(
                "invalid friend-auth-sync base url '{base_url}': {error}"
            ))
        })?;
        if !matches!(base_url.scheme(), "http" | "https") {
            return Err(ServiceError::InternalError(format!(
                "friend-auth-sync base url must use http or https: {base_url}"
            )));
        }
        Ok(Self {
            client: reqwest::Client::new(),
            base_url,
        })
    }

    fn sync_url(&self) -> Result<reqwest::Url, ServiceError> {
        self.base_url.join(SYNC_PATH).map_err(|error| {
            ServiceError::InternalError(format!(
                "failed to build friend-auth-sync url from '{}': {error}",
                self.base_url
            ))
        })
    }
}

// TC's transport address contract has exactly two non-empty components.
// Do not accept empty components, whitespace, or additional separators.
fn tc_bot_identity(actor_id: &str) -> Option<(&str, &str)> {
    let (bot_id, work_no) = actor_id.split_once(':')?;
    if bot_id.is_empty() || bot_id.chars().any(char::is_whitespace)
        || work_no.is_empty() || work_no.contains(':') || work_no.chars().any(char::is_whitespace)
    {
        return None;
    }
    Some((bot_id, work_no))
}

#[derive(Deserialize)]
struct FriendAuthSyncResponse {
    synced: bool,
}

#[derive(Debug, Serialize)]
struct FriendAuthSyncRequest {
    bot_id: String,
    owner_work_no: String,
    human_work_no: String,
    action: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
}

impl HttpFriendAuthSyncPort {
    fn build_request(
        &self,
        command: &FriendAuthSyncCommand,
    ) -> Result<reqwest::RequestBuilder, ServiceError> {
        let url = self.sync_url()?;
        let (bot_id, owner_work_no) = tc_bot_identity(&command.bot_id).ok_or_else(|| {
            ServiceError::InternalError("friend-auth-sync requires a TC bot_id:workNo".to_string())
        })?;
        let payload = FriendAuthSyncRequest {
            bot_id: bot_id.to_string(),
            owner_work_no: owner_work_no.to_string(),
            human_work_no: command.human_work_no.clone(),
            action: command.action.as_str(),
            request_id: command.request_id.clone(),
        };
        let mut request = self.client.post(url).timeout(SYNC_TIMEOUT);
        // Only forward the gateway principal + trace ids. Openapi
        // Authorization/Cookie identity is intentionally NOT sent.
        if let Some(auth) = command.request_auth.as_ref() {
            for (name, value) in &auth.forwarded_headers {
                let lower = name.to_ascii_lowercase();
                if lower == "x-avernet-principal"
                    || lower == "x-request-id"
                    || lower == "x-trace-id"
                {
                    if let Ok(header_name) = reqwest::header::HeaderName::try_from(name.as_str()) {
                        request = request.header(header_name, value.as_str());
                    }
                }
            }
        }
        Ok(request.json(&payload))
    }
}

#[async_trait]
impl FriendAuthSyncPort for HttpFriendAuthSyncPort {
    async fn sync(&self, command: FriendAuthSyncCommand) -> ServiceResult<()> {
        // TC addresses bots as bot_id:workNo. Other BCS bots are not TC
        // resources and must never reach the TC authorization endpoint.
        if tc_bot_identity(&command.bot_id).is_none() {
            return Ok(());
        }
        info!(
            action = command.action.as_str(),
            bot_id = %command.bot_id,
            human_work_no = %command.human_work_no,
            "sending friend-auth-sync"
        );
        let build = self.build_request(&command);
        let request = match build {
            Ok(rb) => rb,
            Err(e) => return Err(e),
        };
        let response = request.send().await.map_err(|error| {
            warn!(action = command.action.as_str(), %error, "friend-auth-sync request failed");
            ServiceError::InternalError(format!("friend-auth-sync request failed: {error}"))
        })?;
        if response.status().is_success() {
            let result: FriendAuthSyncResponse = response.json().await.map_err(|error| {
                ServiceError::InternalError(format!("invalid friend-auth-sync response: {error}"))
            })?;
            if !result.synced {
                return Err(ServiceError::InternalError("friend-auth-sync was not applied".to_string()));
            }
            info!("friend-auth-sync sent successfully");
            return Ok(());
        }
        let status = response.status();
        warn!(%status, "friend-auth-sync non-success");
        // The caller owns best-effort policy; the port must report failure.
        Err(ServiceError::InternalError(format!("friend-auth-sync returned {status}")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constructor_rejects_blank_and_non_http() {
        assert!(HttpFriendAuthSyncPort::new("").is_err());
        assert!(HttpFriendAuthSyncPort::new("ftp://backend.example.com").is_err());
    }

    #[test]
    fn build_url_uses_internal_sync_path() {
        let adapter = HttpFriendAuthSyncPort::new("https://backend.example.com/api/").expect("valid");
        assert_eq!(
            adapter.sync_url().unwrap().as_str(),
            "https://backend.example.com/api/internal/bot-friend-auth/sync"
        );
    }

    #[tokio::test]
    async fn forwards_only_principal_and_trace_headers() {
        let adapter = HttpFriendAuthSyncPort::new("https://backend.example.com/api/").expect("valid");
        let command = FriendAuthSyncCommand {
            env: "dev".to_string(),
            bot_id: "bot-1:85020".to_string(),
            owner_work_no: "85020".to_string(),
            human_work_no: "88123".to_string(),
            action: FriendAuthSyncAction::Grant,
            request_id: Some("r-1".to_string()),
            request_auth: Some(bcs_service_api::RequestAuthHeaders {
                authorization: Some("Bearer x".to_string()),
                cookie: Some("c=1".to_string()),
                forwarded_headers: vec![
                    ("authorization".to_string(), "Bearer x".to_string()),
                    ("cookie".to_string(), "c=1".to_string()),
                    ("x-avernet-principal".to_string(), "jwt".to_string()),
                    ("x-request-id".to_string(), "rid".to_string()),
                ],
            }),
        };
        let req = adapter.build_request(&command).unwrap().build().unwrap();
        assert_eq!(req.timeout(), Some(&SYNC_TIMEOUT));
        assert!(req.headers().get(reqwest::header::AUTHORIZATION).is_none());
        assert!(req.headers().get(reqwest::header::COOKIE).is_none());
        assert_eq!(
            req.headers().get("x-avernet-principal").and_then(|v| v.to_str().ok()),
            Some("jwt")
        );
        assert_eq!(
            req.headers().get("x-request-id").and_then(|v| v.to_str().ok()),
            Some("rid")
        );
    }
}

#[cfg(test)]
#[path = "friend_auth_sync_tests.rs"]
mod sync_contract_tests;
