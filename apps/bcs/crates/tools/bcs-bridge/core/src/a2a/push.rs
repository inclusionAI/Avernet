//! Push notification delivery (spec §4.3.3, §13.2): at-least-once, ordered per
//! config, bounded retries with exponential backoff.
use std::sync::Arc;
use std::time::Duration;

use bcs_a2a_protocol::{TaskPushNotificationConfig, A2A_JSON_CONTENT_TYPE};
use futures::StreamExt;
use tokio_util::sync::CancellationToken;

use super::task::{A2aTask, Cursor};
use crate::config::ProviderConfig;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_ATTEMPTS: u32 = 8;
const MAX_BACKOFF: Duration = Duration::from_secs(30);
/// Header carrying the config's `token` so receivers can validate the source.
pub const NOTIFICATION_TOKEN_HEADER: &str = "X-A2A-Notification-Token";

/// Webhooks may target the registering BCS (`api_url` origin) or an explicit
/// allowlisted prefix. Everything else is refused to prevent SSRF.
pub fn url_allowed(config: &ProviderConfig, url: &str) -> bool {
    let Ok(target) = url::Url::parse(url) else { return false };
    if !matches!(target.scheme(), "http" | "https") || !target.username().is_empty() || target.password().is_some() {
        return false;
    }
    let same_origin = config.api_url.as_deref().and_then(|api| url::Url::parse(api).ok())
        .is_some_and(|api| api.origin() == target.origin());
    same_origin || config.a2a.as_ref().is_some_and(|a2a| a2a.push_url_allowlist.iter().any(|prefix| url.starts_with(prefix.as_str())))
}

/// Deliver every event after `cursor` to `config.url` until the task ends,
/// the config is deleted, or delivery of one event exhausts its retries.
pub fn spawn_dispatcher(http: reqwest::Client, task: Arc<A2aTask>, config: TaskPushNotificationConfig, cursor: Cursor, stop: CancellationToken) {
    tokio::spawn(async move {
        let mut events = Box::pin(task.subscribe(cursor));
        loop {
            let next = tokio::select! {
                _ = stop.cancelled() => return,
                next = events.next() => next,
            };
            let Some((seq, event)) = next else { return };
            let body = match serde_json::to_vec(&event) {
                Ok(body) => body,
                Err(error) => { tracing::error!(%error, task_id = %task.id, "cannot encode push event"); return; }
            };
            if !deliver(&http, &config, &body, &stop).await {
                tracing::warn!(task_id = %task.id, seq, url = %config.url, "push notification delivery abandoned after retries");
                return;
            }
        }
    });
}

async fn deliver(http: &reqwest::Client, config: &TaskPushNotificationConfig, body: &[u8], stop: &CancellationToken) -> bool {
    let mut backoff = Duration::from_millis(500);
    for attempt in 1..=MAX_ATTEMPTS {
        let mut request = http.post(&config.url).timeout(REQUEST_TIMEOUT)
            .header(reqwest::header::CONTENT_TYPE, A2A_JSON_CONTENT_TYPE).body(body.to_vec());
        if let Some(authorization) = config.authorization() { request = request.header(reqwest::header::AUTHORIZATION, authorization); }
        if let Some(token) = &config.token { request = request.header(NOTIFICATION_TOKEN_HEADER, token); }
        match request.send().await {
            Ok(response) if response.status().is_success() => return true,
            Ok(response) if response.status().is_client_error() && response.status() != reqwest::StatusCode::TOO_MANY_REQUESTS => {
                tracing::warn!(status = %response.status(), url = %config.url, "push receiver rejected notification");
                return false;
            }
            Ok(response) => tracing::debug!(status = %response.status(), attempt, "push notification retry"),
            Err(error) => tracing::debug!(%error, attempt, "push notification retry"),
        }
        if attempt == MAX_ATTEMPTS { break; }
        tokio::select! {
            _ = stop.cancelled() => return false,
            _ = tokio::time::sleep(backoff) => {},
        }
        backoff = (backoff * 2).min(MAX_BACKOFF);
    }
    false
}
