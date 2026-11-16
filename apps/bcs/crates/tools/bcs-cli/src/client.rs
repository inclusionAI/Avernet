//! HTTP helper used by the `bcs-cli` binaries.
//!
//! This is a tool-local client, not a public SDK. Shared wire DTOs live in
//! `bcs-protocol`.

use std::collections::{BTreeMap, HashMap};
use std::time::Duration;

#[path = "draft_client.rs"]
mod drafts;

mod files;

mod bots;
mod groups;
mod collaboration;
mod fusion;
mod sessions;
mod relations;
mod channels;
mod services;
mod chat;

use anyhow::{Context, Result, anyhow};
use reqwest::header::HeaderValue;
use serde::Deserialize;
use tracing::info;

use bcs_protocol::{BCS_CHAT_VERSION, BCS_CHAT_VERSION_HEADER, ParticipantBindingInfo};

// ============================================================================
// BCS Client
// ============================================================================

/// Default BCS URL if not configured.
pub const DEFAULT_BCS_URL: &str = "http://localhost:21000";

/// Build a reqwest HTTP client with explicit cross-host redirect policy.
///
/// Follows up to 10 redirects, but reqwest's default behavior of stripping
/// `Authorization` on cross-host redirects is preserved (this is the standard
/// reqwest behaviour when `Policy::custom` is used with `attempt.follow()`).
/// We explicitly construct the policy instead of relying on `Client::default()`
/// so the cross-host auth isolation is auditable.
fn build_http_client() -> reqwest::Client {
    let policy = reqwest::redirect::Policy::custom(move |attempt| {
        if attempt.previous().first().is_some_and(|source| source.origin() != attempt.url().origin()) {
            attempt.stop()
        } else if attempt.previous().len() >= 10 {
            attempt.error("too many redirects")
        } else {
            // cross-host or same-host: follow; reqwest strips Authorization on
            // cross-host hops by default.
            attempt.follow()
        }
    });
    reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .redirect(policy)
        .build()
        .expect("build reqwest client")
}

#[derive(Debug, Deserialize)]
pub struct BotGroupListPage {
    pub items: Vec<serde_json::Value>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

/// Inputs for creating a state-machine group from authoring YAML.
#[derive(Debug)]
pub struct CreateCustomGroupOptions {
    pub id: Option<String>,
    pub driver_bot: String,
    pub participant_bindings: BTreeMap<String, ParticipantBindingInfo>,
    pub definition_yaml: String,
    pub context: Option<String>,
    pub topic: Option<String>,
    pub auto_start_on_service_invocation: bool,
}

#[derive(Debug)]
pub struct RunSessionCollaborationOptions {
    pub session_id: String,
    pub participant_bindings: BTreeMap<String, ParticipantBindingInfo>,
    pub definition_yaml: String,
    pub input: serde_json::Value,
    pub opening_message: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
pub struct CurrentActorGroupListPage {
    pub actor_id: String,
    pub items: Vec<serde_json::Value>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

/// Client for interacting with the Bot Coordination Service.
#[derive(Debug, Clone)]
pub struct BcsClient {
    /// Base URL for the BCS.
    base_url: String,
    /// HTTP client.
    http_client: reqwest::Client,
    /// Optional Bearer token for authentication.
    token: Option<String>,
    /// Optional Cookie header for authentication (for remote BCS).
    cookie: Option<String>,
    /// Optional OAuth2 headers for office network authentication.
    oauth_headers: Option<HashMap<String, String>>,
    /// Optional client identity (e.g., "bcs-cli/0.3.0") for X-BCS-Client header.
    client_identity: Option<String>,
    /// Optional raw service key for `/services/*` routes. When set, it is sent
    /// as `X-BCS-Service-Key` for external caller attribution and replaces bot
    /// bearer / X-BCS-Bot-Token on the wire.
    service_key: Option<String>,
    /// W3C Trace Context inherited from the Bash tool process. The CLI is a
    /// transparent carrier and never creates its own span.
    traceparent: Option<HeaderValue>,
    actor_id: Option<HeaderValue>,
}

// ============================================================================
// CLI-internal chat outcome types (NOT bcs-protocol wire DTOs)
// ============================================================================

/// Structured result of a chat run poll flow, assembled by `bcs-cli` from
/// existing fields of [`bcs_protocol::ChatRunSubmitResponse`] / [`bcs_protocol::ChatRunStatusResponse`].
/// This is a CLI- internal type; it is NOT a `bcs-protocol` wire DTO and
/// introduces no new on-wire fields.
#[derive(Debug, Clone)]
pub struct ChatRunOutcome {
    pub delivery: Option<bcs_protocol::ChatRunDeliverySummary>,
    /// Did the run reach a success state?
    /// sync = `ChatRunState::Completed`; detach = `Running` or `Completed`.
    pub delivered: bool,
    /// Was the run created (`chat_async` returned a `run_id`)?
    /// False only on submit-level failure.
    pub submitted: bool,
    /// Run ID. `None` only on submit-level failure (no run was created).
    pub run_id: Option<String>,
    /// Session ID. `None` only on submit-level failure.
    pub session_id: Option<String>,
    /// Bot UUID. Filled from the submit/status response, or the CLI
    /// `--bot-uuid` arg on submit-level failure.
    pub bot_uuid: Option<String>,
    /// `running|completed|failed|cancelled|timeout|poll_error|
    ///  submit_failed|submit_indeterminate`
    pub state: String,
    /// Response content. Only meaningful for sync terminal runs.
    pub response_content: Option<String>,
    /// Error message (from server status, timeout, or poll error).
    pub error_message: Option<String>,
    /// Whether the server truncated `response.content`.
    pub content_truncated: bool,
}

/// Typed errors for `chat_async` submission. `main.rs` maps each variant to a
/// distinct [`ChatRunOutcome`] state so stdout is never empty and known IDs
/// are preserved.
#[derive(Debug)]
pub enum ChatAsyncError {
    /// `.send()` failed (connect refused/reset/client 10s timeout). A run
    /// MAY have been created server-side; its ID is unknown to the CLI.
    Transport(String),
    /// Server responded with a non-2xx status (it explicitly did not accept
    /// the run). No run was created.
    NotSuccessful { status: u16, body: String },
    /// Response body could not be deserialized as `ChatRunSubmitResponse`.
    InvalidResponse(String),
}

impl BcsClient {
    ///Create a new BCS client with the given base URL.
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: None,
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: None,
            traceparent: None,
            actor_id: None,
        }
    }

    /// Create a client with a Bearer token for authentication.
    pub fn with_token(base_url: impl Into<String>, token: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: Some(token.into()),
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: None,
            traceparent: None,
            actor_id: None,
        }
    }

    /// Create a client with Bearer token and Cookie for authentication.
    pub fn with_token_and_cookie(
        base_url: impl Into<String>,
        token: impl Into<String>,
        cookie: impl Into<String>,
    ) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: Some(token.into()),
            cookie: Some(cookie.into()),
            oauth_headers: None,
            client_identity: None,
            service_key: None,
            traceparent: None,
            actor_id: None,
        }
    }

    /// Create a client with Bearer token and OAuth2 headers for office network authentication.
    pub fn with_token_and_oauth(
        base_url: impl Into<String>,
        token: impl Into<String>,
        oauth_headers: HashMap<String, String>,
    ) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: Some(token.into()),
            cookie: None,
            oauth_headers: Some(oauth_headers),
            client_identity: None,
            service_key: None,
            traceparent: None,
            actor_id: None,
        }
    }

    /// Create a client carrying a raw service key for `/services/*` routes.
    /// The key is sent as `X-BCS-Service-Key` on every request and supersedes
    /// any bot bearer token; the server hashes it via sha256 to look up the
    /// caller (`bcs-http/src/service_key.rs`).
    pub fn with_service_key(base_url: impl Into<String>, raw_key: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: None,
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: Some(raw_key.into()),
            traceparent: None,
            actor_id: None,
        }
    }

    /// Set the raw service key used for `X-BCS-Service-Key`.
    pub fn set_service_key(&mut self, raw_key: impl Into<String>) {
        self.service_key = Some(raw_key.into());
    }

    /// Set the Bearer token for authentication.
    pub fn set_token(&mut self, token: impl Into<String>) {
        self.token = Some(token.into());
    }

    /// Set the Cookie header for authentication.
    pub fn set_cookie(&mut self, cookie: impl Into<String>) {
        self.cookie = Some(cookie.into());
    }

    /// Set the client identity for X-BCS-Client header (e.g., "bcs-cli/0.3.0").
    pub fn set_client_identity(&mut self, identity: impl Into<String>) {
        self.client_identity = Some(identity.into());
    }

    /// Configure the opaque Trace Context propagated only on business dispatch
    /// requests. The gateway owns W3C validation; polling and management
    /// requests intentionally omit it.
    pub fn set_traceparent(&mut self, value: &str) -> Result<()> {
        let header = HeaderValue::from_str(value).context("invalid TRACEPARENT header value")?;
        self.traceparent = Some(header);
        Ok(())
    }

    /// Get the current token.
    pub fn token(&self) -> Option<&str> {
        self.token.as_deref()
    }

    /// Get the current cookie.
    pub fn cookie(&self) -> Option<&str> {
        self.cookie.as_deref()
    }

    /// Set OAuth2 headers for office network authentication.
    pub fn set_oauth_headers(&mut self, headers: HashMap<String, String>) {
        self.oauth_headers = Some(headers);
    }

    /// Create a client using the MOLTIS_BCS_URL environment variable.
    pub fn from_env() -> Self {
        let url = std::env::var("MOLTIS_BCS_URL").unwrap_or_else(|_| DEFAULT_BCS_URL.to_string());
        Self::new(url)
    }

    /// Get the base URL.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Health check for BCS.
    pub async fn health_check(&self) -> Result<bool> {
        let url = format!("{}/health", self.base_url);
        let response = self
            .add_headers(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to connect to BCS")?;

        Ok(response.status().is_success())
    }

    /// Add authentication headers to a request builder.
    ///
    /// When OAuth2 headers are present (office network), the layout is:
    /// - `Authorization`: OAuth2 Bearer token (for Spanner gateway authentication)
    /// - `X-BCS-Bot-Token`: bot token (for BCS server bot identification)
    /// - `starpoint-data2` etc.: gateway device headers (passed through)
    /// - `User-Agent`: filtered out (SDK's custom UA breaks gateway routing)
    ///
    /// When no OAuth2 headers (prod), the layout is:
    /// - `Authorization: Bearer <bot_token>` (standard BCS auth)
    fn add_headers(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let builder = if let Some(actor) = &self.actor_id {
            builder.header("X-BCN-Actor-Bot-Id", actor)
        } else { builder };
        let has_oauth = self.oauth_headers.is_some();

        if has_oauth {
            let oauth_headers = self.oauth_headers.as_ref().expect("checked is_some above");
            let mut oauth_keys: Vec<&String> = oauth_headers.keys().collect();
            oauth_keys.sort();
            info!(
                "Preparing request with OAuth headers: oauth_keys={:?}, has_authorization={}, has_x_bcs_bot_token_from_sdk={}, bot_token_will_be_sent_via_x_bcs_bot_token=true",
                oauth_keys,
                oauth_headers
                    .keys()
                    .any(|k| k.eq_ignore_ascii_case("authorization")),
                oauth_headers
                    .keys()
                    .any(|k| k.eq_ignore_ascii_case("x-bcs-bot-token")),
            );
        }

        // Identity header. Service-key takes precedence: when set, send
        // `X-BCS-Service-Key` and skip the bot-token branches entirely
        // (the two are different identity spaces; never co-send).
        // - With OAuth (no svc-key): bot token goes to X-BCS-Bot-Token, OAuth takes Authorization
        // - Without OAuth (no svc-key): bot token goes to Authorization as usual
        let builder = if let Some(ref svc_key) = self.service_key {
            builder.header("X-BCS-Service-Key", svc_key.as_str())
        } else if let Some(ref token) = self.token {
            if has_oauth {
                builder.header("X-BCS-Bot-Token", token.as_str())
            } else {
                builder.bearer_auth(token)
            }
        } else {
            builder
        };

        let builder = if let Some(ref cookie) = self.cookie {
            builder.header("Cookie", cookie)
        } else {
            builder
        };

        // Inject OAuth2 headers for office network (Spanner gateway).
        // Skip User-Agent (SDK's custom UA breaks gateway routing).
        // Authorization from OAuth is kept (gateway needs it for user auth).
        let builder = if let Some(ref oauth_headers) = self.oauth_headers {
            let mut builder = builder;
            for (key, value) in oauth_headers {
                if key.eq_ignore_ascii_case("user-agent") {
                    continue;
                }
                builder = builder.header(key, value);
            }
            builder
        } else {
            builder
        };

        // Add X-BCS-Client header if client_identity is set.
        if let Some(ref identity) = self.client_identity {
            builder.header("X-BCS-Client", identity.as_str())
        } else {
            builder
        }
    }

    /// Add Bearer token to a request builder if token is set.
    fn add_auth(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        self.add_headers(builder)
    }

    fn add_chat_headers(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        self.add_auth(builder)
            .header(BCS_CHAT_VERSION_HEADER, BCS_CHAT_VERSION)
    }

    fn add_chat_dispatch_headers(
        &self,
        builder: reqwest::RequestBuilder,
        detach: bool,
        client_wait_timeout_ms: u64,
    ) -> reqwest::RequestBuilder {
        let builder = self
            .add_chat_headers(builder)
            .header("X-BCS-Client-Detach", detach.to_string())
            .header(
                "X-BCS-Client-Wait-Timeout-Ms",
                client_wait_timeout_ms.to_string(),
            );
        match &self.traceparent {
            Some(traceparent) => builder.header("traceparent", traceparent.clone()),
            None => builder,
        }
    }

    /// Helper: check response status and parse JSON body.
    async fn ensure_success(resp: reqwest::Response, label: &str) -> Result<serde_json::Value> {
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(anyhow!("{} failed ({}): {}", label, status, body));
        }
        // 204 No Content carries no JSON body (e.g. DELETE returns 204). Return
        // Null instead of failing the `json()` parse — callers that print the
        // result render `null`/skip, and the best-effort cancel ignores it.
        if resp.status() == reqwest::StatusCode::NO_CONTENT {
            return Ok(serde_json::Value::Null);
        }
        resp.json()
            .await
            .with_context(|| format!("Invalid {} response", label))
    }

    /// Helper: check response status only (no body parsing).
    async fn ensure_success_status(resp: reqwest::Response, label: &str) -> Result<()> {
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(anyhow!("{} failed ({}): {}", label, status, body));
        }
        Ok(())
    }
}

impl BcsClient {
    pub fn set_actor_id(&mut self, actor: Option<&str>) -> Result<()> {
        let caller = crate::CallerContext::new(None, actor)?;
        self.actor_id = caller.actor_id().map(HeaderValue::from_str).transpose()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests;

impl ChatRunOutcome {
    pub fn admission_succeeded(&self) -> bool {
        self.submitted && matches!(self.state.as_str(), "pending" | "submitted" | "running" | "completed")
            && self.delivery.as_ref().is_none_or(|d| !matches!(d.status.as_str(), "rejected_capacity" | "failed" | "expired" | "cancelled"))
    }
}
