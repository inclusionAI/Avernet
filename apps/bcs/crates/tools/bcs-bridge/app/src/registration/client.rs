use std::time::Duration;

use anyhow::{Context, bail};
use reqwest::{Client, Response};
use serde::Deserialize;
use url::Url;

/// HTTP client for one registration API prefix.
pub struct RegistrationClient { client: Client, base: Url, headers: reqwest::header::HeaderMap, legacy_registration: bool }

pub(crate) fn http_url(value: &str, allow_query: bool) -> anyhow::Result<Url> {
    let url = Url::parse(value).map_err(|_| anyhow::anyhow!("Expected an absolute HTTP(S) URL"))?;
    if !matches!(url.scheme(), "https" | "http") || url.host_str().is_none()
        || !url.username().is_empty() || url.password().is_some() || url.fragment().is_some()
        || (!allow_query && url.query().is_some()) {
        bail!("URL must use HTTP(S), contain a host, and have no credentials, fragment or unsupported query");
    }
    Ok(url)
}

pub(crate) fn ws_url(value: &str) -> anyhow::Result<Url> {
    let url = Url::parse(value).map_err(|_| anyhow::anyhow!("Expected an absolute ws/wss URL"))?;
    if !matches!(url.scheme(), "ws" | "wss") || url.host_str().is_none()
        || !url.username().is_empty() || url.password().is_some()
        || url.query().is_some() || url.fragment().is_some() {
        bail!("URL must use ws(s), contain a host, and have no credentials, query or fragment");
    }
    Ok(url)
}

impl RegistrationClient {
    pub fn new(prefix: &str) -> anyhow::Result<Self> {
        let base = http_url(prefix, false)?;
        let client = Client::builder().timeout(Duration::from_secs(30)).connect_timeout(Duration::from_secs(10))
            .redirect(reqwest::redirect::Policy::none()).retry(reqwest::retry::never())
            .build().map_err(|_| anyhow::anyhow!("Cannot initialize registration HTTP client"))?;
        // Preserve system proxy settings: an egress proxy may supply agent authentication.
        Ok(Self { client, base, headers: reqwest::header::HeaderMap::new(), legacy_registration: false })
    }

    /// Use the legacy POST /register endpoint and its bare registration response.
    /// Selected explicitly by distributions; never retry a registration on another route.
    pub fn with_legacy_registration(mut self) -> Self {
        self.legacy_registration = true;
        self
    }

    /// Attach headers to every request to this API prefix. Redirects stay disabled.
    pub fn with_headers(mut self, headers: reqwest::header::HeaderMap) -> Self {
        self.headers = headers;
        self
    }

    fn endpoint(&self, path: &str) -> anyhow::Result<Url> {
        // A root-relative Url::join would discard a path prefix such as /gateway/prefix.
        Url::parse(&format!("{}/{}", self.base.as_str().trim_end_matches('/'), path))
            .map_err(|_| anyhow::anyhow!("Cannot construct registration API URL"))
    }

    /// GET `path` below the API prefix and return the bounded success body.
    /// `unreachable` is the error reported when the request cannot be sent;
    /// transport details are not exposed.
    pub async fn get(&self, path: &str, operation: &str, unreachable: &str) -> anyhow::Result<Vec<u8>> {
        let response = self.client.get(self.endpoint(path)?).headers(self.headers.clone()).send().await
            .map_err(|_| anyhow::anyhow!("{unreachable}"))?;
        body(response, operation).await
    }

    pub(super) async fn update_webhook(&self, provider: &str, reference: &str, bot_id: &str, webhook: &str, admin: &str) -> anyhow::Result<()> {
        let mut url = self.endpoint("providers")?;
        url.path_segments_mut().map_err(|_| anyhow::anyhow!("Invalid Provider API URL"))?
            .push(provider).push("bots").push(reference);
        let response = self.client.patch(url).headers(self.headers.clone()).bearer_auth(admin)
            .json(&serde_json::json!({"webhook_url": webhook})).send().await
            .map_err(|_| anyhow::anyhow!("Webhook update outcome is unknown; original local config retained. Verify the remote endpoint before restarting"))?;
        let bytes = body(response, "Webhook update").await?;
        let result: serde_json::Value = serde_json::from_slice(&bytes)
            .map_err(|_| anyhow::anyhow!("Invalid webhook update response; verify the remote endpoint before restarting"))?;
        if result.get("provider_id").and_then(|v| v.as_str()) != Some(provider)
            || result.get("provider_bot_ref").and_then(|v| v.as_str()) != Some(reference)
            || result.get("bot_uuid").and_then(|v| v.as_str()) != Some(bot_id)
            || result.get("webhook_url").and_then(|v| v.as_str()) != Some(webhook) {
            bail!("Webhook update returned a mismatched binding; original local config retained. Verify the remote endpoint before restarting");
        }
        Ok(())
    }

    pub(crate) async fn register(&self, token: &str, name: &str, agent_code: &str, mode: &str, webhook: Option<&str>, provider_id: &str, slug: Option<&str>) -> anyhow::Result<BotRegistration> {
        let mut url = self.endpoint(if self.legacy_registration { "register" } else { "openapi/v1/collaboration/register" })?;
        let provider_bot_ref = composed_ref(agent_code, slug)?;
        let mut pairs = vec![
            ("token", token), ("bot-name", name), ("provider_bot_ref", provider_bot_ref.as_str()), ("mode", mode),
        ];
        if let Some(webhook) = webhook { pairs.push(("webhook_url", webhook)); }
        url.query_pairs_mut().extend_pairs(pairs);
        let response = self.client.post(url).headers(self.headers.clone()).send().await
            .map_err(|_| anyhow::anyhow!("Registration outcome is unknown; a Bot may already exist. Check agents/me before creating another Bot"))?;
        if response.status() == reqwest::StatusCode::CONFLICT && slug.is_some() {
            let bytes = read_body(response, "Registration").await?;
            let error: serde_json::Value = serde_json::from_slice(&bytes).unwrap_or_default();
            let confirmed = if self.legacy_registration {
                error.get("error").and_then(serde_json::Value::as_str) == Some("registration_conflict")
            } else {
                error.get("code").and_then(serde_json::Value::as_u64) == Some(40900)
                    && error.pointer("/data/error_code").and_then(serde_json::Value::as_str) == Some("registration_conflict")
            };
            if confirmed { return Err(RegistrationConflict.into()); }
            bail!("Registration API returned HTTP 409");
        }
        let bytes = body(response, "Registration").await?;
        #[derive(Deserialize)]
        struct Envelope { code: u32, data: Option<serde_json::Value> }
        let data = if self.legacy_registration {
            serde_json::from_slice(&bytes)
                .map_err(|_| anyhow::anyhow!("Invalid registration response; remote registration may have completed"))?
        } else {
            let response: Envelope = serde_json::from_slice(&bytes)
                .map_err(|_| anyhow::anyhow!("Invalid registration response; remote registration may have completed"))?;
            if response.code != 20100 { bail!("Registration rejected (code {})", response.code); }
            response.data.context("Registration response is missing data")?
        };
        let registration: BotRegistration = serde_json::from_value(data)
            .map_err(|_| anyhow::anyhow!("Registration succeeded but returned incomplete credentials"))?;
        if [&registration.bot_uuid, &registration.bot_token, &registration.registration.provider_id]
            .iter().any(|value| value.trim().is_empty())
            || registration.registration.provider_id != provider_id
            || registration.registration.provider_bot_ref != provider_bot_ref
            || registration.registration.mode != mode
            || slug.is_some_and(|s| !registration.bot_uuid.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                || registration.registration.auth_mode.as_deref() != Some("agentpass-subbots")
                || registration.registration.agent_code.as_deref() != Some(agent_code)
                || registration.registration.bot_slug.as_deref() != Some(s)) {
            bail!("Registration succeeded but returned invalid credentials or a mismatched binding");
        }
        Ok(registration)
    }
}

/// Compose the stable Provider reference used for this registration request.
pub(super) fn composed_ref(parent: &str, slug: Option<&str>) -> anyhow::Result<String> {
    if !super::valid_provider_bot_ref(parent) { bail!("Invalid Provider identity"); }
    let reference = if let Some(slug) = slug {
        if slug.is_empty() || slug.len() > 32 || !slug.as_bytes()[0].is_ascii_alphanumeric()
            || slug.bytes().any(|b| !(b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')) {
            bail!("Invalid generated Bot suffix");
        }
        format!("{parent}:{slug}")
    } else { parent.to_owned() };
    if reference.len() > 128 { bail!("Composed provider_bot_ref exceeds 128 bytes"); }
    Ok(reference)
}

#[derive(Debug)]
pub(super) struct RegistrationConflict;
impl std::fmt::Display for RegistrationConflict {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Registration API returned HTTP 409 (registration_conflict)")
    }
}
impl std::error::Error for RegistrationConflict {}

async fn body(response: Response, operation: &str) -> anyhow::Result<Vec<u8>> {
    if !response.status().is_success() { bail!("{operation} API returned HTTP {}", response.status().as_u16()); }
    read_body(response, operation).await
}

async fn read_body(mut response: Response, operation: &str) -> anyhow::Result<Vec<u8>> {
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| anyhow::anyhow!("Cannot read {operation} response"))? {
        if body.len() + chunk.len() > 1024 * 1024 { bail!("{operation} response exceeds 1 MiB"); }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

#[derive(Deserialize)]
pub struct BotRegistration {
    pub bot_uuid: String,
    pub bot_token: String,
    pub registration: RegistrationScope,
}

#[derive(Deserialize)]
pub struct RegistrationScope {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub mode: String,
    #[serde(default)]
    pub auth_mode: Option<String>,
    #[serde(default)]
    pub agent_code: Option<String>,
    #[serde(default)]
    pub bot_slug: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_and_overridden_prefixes_are_preserved_for_both_endpoints() {
        for base in ["https://bcs.example.com/gateway/prefix", "http://127.0.0.1:21100/custom/prefix/"] {
            let client = RegistrationClient::new(base).unwrap();
            for path in ["api/v1/collaboration/bots/me", "openapi/v1/collaboration/register"] {
                assert_eq!(client.endpoint(path).unwrap().as_str(), format!("{}/{path}", base.trim_end_matches('/')));
            }
        }
    }
    #[tokio::test]
    async fn prepared_auth_headers_are_sent_to_identity_and_registration() {
        use std::io::{Read, Write};
        for legacy in [false, true] {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            for method in ["GET", "POST"] {
                let (mut stream, _) = listener.accept().unwrap();
                stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut bytes = Vec::new();
                let mut byte = [0u8; 1];
                while !bytes.ends_with(b"\r\n\r\n") {
                    stream.read_exact(&mut byte).unwrap();
                    bytes.push(byte[0]);
                }
                let request = String::from_utf8(bytes).unwrap().to_ascii_lowercase();
                assert!(request.starts_with(&method.to_ascii_lowercase()));
                assert!(request.contains("authorization: Bearer test-execution".to_ascii_lowercase().as_str()));
                assert!(request.contains("x-test-context: test-context"));
                if method == "POST" {
                    let route = if legacy { "/bcs/register?" } else { "/bcs/openapi/v1/collaboration/register?" };
                    assert!(request.starts_with(&format!("post {route}")), "{request}");
                }
                let body = if method == "GET" { "{}" } else {
                    r#"{"code":20100,"data":{"bot_uuid":"bot_test","bot_token":"test-bot-token","registration":{"provider_id":"prv_test","provider_bot_ref":"agent_test","mode":"plugin"}}}"#
                };
                let legacy_body;
                let body = if legacy && method == "POST" {
                    legacy_body = serde_json::from_str::<serde_json::Value>(body).unwrap()["data"].to_string();
                    legacy_body.as_str()
                } else { body };
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
            }
        });
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(reqwest::header::AUTHORIZATION, "Bearer test-execution".parse().unwrap());
        headers.insert("x-test-context", "test-context".parse().unwrap());
        let client = RegistrationClient {
            client: Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap(),
            base: Url::parse(&format!("http://{address}/bcs/")).unwrap(),
            headers: reqwest::header::HeaderMap::new(),
            legacy_registration: false,
        }.with_headers(headers);
        let client = if legacy { client.with_legacy_registration() } else { client };
        client.get("api/v1/collaboration/bots/me", "Identity", "Request failed").await.unwrap();
        client.register("test-register-token", "Test Bot", "agent_test", "plugin", None, "prv_test", None).await.unwrap();
        server.join().unwrap();
        }
    }

}
