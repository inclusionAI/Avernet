use std::time::Duration;

use anyhow::{Context, bail};
use reqwest::{Client, Response};
use serde::Deserialize;
use url::Url;

/// HTTP client for one registration API prefix.
pub struct RegistrationClient { client: Client, base: Url }

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
        Ok(Self { client, base })
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
        let response = self.client.get(self.endpoint(path)?).send().await
            .map_err(|_| anyhow::anyhow!("{unreachable}"))?;
        body(response, operation).await
    }

    pub(crate) async fn register(&self, token: &str, name: &str, agent_code: &str, mode: &str, webhook: Option<&str>, provider_id: &str) -> anyhow::Result<BotRegistration> {
        let mut url = self.endpoint("openapi/v1/collaboration/register")?;
        let mut pairs = vec![
            ("token", token), ("bot-name", name), ("provider_bot_ref", agent_code), ("mode", mode),
        ];
        if let Some(webhook) = webhook { pairs.push(("webhook_url", webhook)); }
        url.query_pairs_mut().extend_pairs(pairs);
        let response = self.client.post(url).send().await
            .map_err(|_| anyhow::anyhow!("Registration request failed; its outcome may be unknown. Check registration status before retrying"))?;
        let bytes = body(response, "Registration").await?;
        #[derive(Deserialize)]
        struct Envelope { code: u32, data: Option<serde_json::Value> }
        let response: Envelope = serde_json::from_slice(&bytes)
            .map_err(|_| anyhow::anyhow!("Invalid registration response; remote registration may have completed"))?;
        if response.code != 20100 { bail!("Registration rejected (code {})", response.code); }
        let registration: BotRegistration = serde_json::from_value(response.data.context("Registration response is missing data")?)
            .map_err(|_| anyhow::anyhow!("Registration succeeded but returned incomplete credentials"))?;
        if [&registration.bot_uuid, &registration.bot_token, &registration.registration.provider_id]
            .iter().any(|value| value.trim().is_empty())
            || registration.registration.provider_id != provider_id
            || registration.registration.provider_bot_ref != agent_code || registration.registration.mode != mode {
            bail!("Registration succeeded but returned invalid credentials or a mismatched binding");
        }
        Ok(registration)
    }
}

async fn body(mut response: Response, operation: &str) -> anyhow::Result<Vec<u8>> {
    if !response.status().is_success() { bail!("{operation} API returned HTTP {}", response.status().as_u16()); }
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
}
