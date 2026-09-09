use std::time::{Duration, SystemTime, UNIX_EPOCH};
use async_trait::async_trait;
use bcs_service_api::port::{CoordinationClaim, CoordinationContext, CoordinationIntentPort,
    CoordinationLease, CoordinationResult};
use bcs_service_api::{ServiceError, ServiceResult};
use reqwest::{Client, Method, StatusCode, Url};
use serde::Deserialize;
use serde_json::{Map, Value, json};

pub struct CoordinationHttpClient {
    client: Client,
    base: Url,
    token: String,
}

fn error(code: &str) -> ServiceError { ServiceError::InternalError(code.to_string()) }
fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

#[derive(Deserialize)]
struct Payload {
    intent_id: String,
    v: u64,
    tool: String,
    arguments: Map<String, Value>,
    created_at_ms: u64,
    expires_at_ms: u64,
}

#[derive(Deserialize)]
struct ReadResponse { payload: Payload }

#[derive(Deserialize)]
struct ClaimResponse {
    acquired: bool,
    claim_token: Option<String>,
    result: Option<CoordinationResult>,
}

impl CoordinationHttpClient {
    pub fn new(base_url: &str, token: String) -> ServiceResult<Self> {
        let base = Url::parse(base_url).map_err(|_| error("invalid_coordination_endpoint"))?;
        if !matches!(base.scheme(), "http" | "https") || base.host_str().is_none()
            || !base.username().is_empty() || base.password().is_some()
            || base.query().is_some() || base.fragment().is_some() || token.len() < 32
        {
            return Err(error("invalid_coordination_config"));
        }
        let client = Client::builder().redirect(reqwest::redirect::Policy::none())
            // Isolate claim/status/finish exchanges from stale pooled connections
            // after an ambiguous response; these are low-volume control calls.
            .pool_max_idle_per_host(0)
            .timeout(Duration::from_secs(3)).build()
            .map_err(|_| error("coordination_client_initialization_failed"))?;
        Ok(Self { client, base, token })
    }

    fn url(&self, id: &str, operation: &str) -> ServiceResult<Url> {
        if !id.strip_prefix("bcs_intent_").is_some_and(|s| s.len() == 32
            && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {
            return Err(error("invalid_coordination_reference"));
        }
        self.base.join(&format!("/internal/bcs-coordination/v1/intents/{id}{operation}"))
            .map_err(|_| error("invalid_coordination_endpoint"))
    }

    async fn request(&self, method: Method, url: Url, body: Option<&Value>,
        attempts: usize, deadline_ms: u64) -> ServiceResult<Value> {
        for attempt in 0..attempts {
            let remaining = deadline_ms.saturating_sub(now_ms()).min(3000);
            if remaining == 0 { return Err(error("coordination_deadline_exceeded")); }
            let mut request = self.client.request(method.clone(), url.clone())
                .bearer_auth(&self.token).timeout(Duration::from_millis(remaining));
            if let Some(body) = body { request = request.json(body); }
            let result = async {
                let mut response = request.send().await.map_err(|_| (true, "coordination_transport_error"))?;
                let status = response.status();
                let mut bytes = Vec::new();
                while let Some(chunk) = response.chunk().await.map_err(|_| (true, "coordination_transport_error"))? {
                    if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
                        return Err((false, "coordination_response_too_large"));
                    }
                    bytes.extend_from_slice(&chunk);
                }
                match status {
                    StatusCode::NOT_FOUND | StatusCode::GONE => return Err((false, "intent_unavailable")),
                    StatusCode::CONFLICT => return Err((false, "intent_conflict")),
                    status if status.is_server_error() => return Err((true, "coordination_store_unavailable")),
                    status if !status.is_success() => return Err((false, "coordination_resolver_rejected")),
                    _ => {}
                }
                serde_json::from_slice(&bytes).map_err(|_| (false, "invalid_coordination_response"))
            }.await;
            match result {
                Ok(value) => return Ok(value),
                Err((retry, code)) => {
                    if !retry || attempt + 1 == attempts { return Err(error(code)); }
                }
            }
            let delay = if attempt == 0 { 200 } else { 500 };
            if now_ms().saturating_add(delay) >= deadline_ms {
                return Err(error("coordination_deadline_exceeded"));
            }
            tokio::time::sleep(Duration::from_millis(delay)).await;
        }
        Err(error("coordination_transport_error"))
    }
}

#[async_trait]
impl CoordinationIntentPort for CoordinationHttpClient {
    async fn resolve_and_claim(&self, id: &str, tool: &str,
        context: &CoordinationContext, deadline_ms: u64) -> ServiceResult<CoordinationClaim> {
        let value = self.request(Method::GET, self.url(id, "")?, None, 3, deadline_ms).await?;
        let read: ReadResponse = serde_json::from_value(value).map_err(|_| error("invalid_coordination_payload"))?;
        let p = read.payload;
        if p.intent_id != id || p.v != 2 || p.tool != tool || p.expires_at_ms <= now_ms()
            || p.created_at_ms >= p.expires_at_ms {
            return Err(error("coordination_payload_mismatch"));
        }
        let required: &[&str] = match tool {
            "bcs_assign_task" => &["target_bot", "message"],
            "bcs_send_task_message" => &["message"],
            "bcs_task_complete" => &["summary"],
            _ => return Err(error("unknown_coordination_tool")),
        };
        if required.iter().any(|key| !p.arguments.get(*key).and_then(Value::as_str)
            .is_some_and(|v| !v.trim().is_empty()))
            || p.arguments.keys().any(|key| !required.contains(&key.as_str())
                && !(tool == "bcs_assign_task" && key == "response_mode"))
            || p.arguments.get("response_mode").is_some_and(|v|
                !matches!(v.as_str(), Some("full" | "after-last-tool-call"))) {
            return Err(error("invalid_coordination_arguments"));
        }
        let body = json!({"context": context});
        let claim = self.request(Method::POST, self.url(id, "/claim")?, Some(&body), 1, deadline_ms).await;
        let value = match claim {
            Ok(value) => value,
            Err(e) => {
                // A lost acknowledgement never grants execution permission.
                let _ = self.request(Method::GET, self.url(id, "")?, None, 1, deadline_ms).await;
                return Err(e);
            }
        };
        let claim: ClaimResponse = serde_json::from_value(value).map_err(|_| error("invalid_coordination_claim"))?;
        if claim.acquired {
            let token = claim.claim_token.filter(|t| t.len() == 32)
                .ok_or_else(|| error("invalid_coordination_claim"))?;
            Ok(CoordinationClaim::Acquired(CoordinationLease { arguments: p.arguments, claim_token: token }))
        } else {
            Ok(CoordinationClaim::Duplicate(claim.result))
        }
    }

    async fn finish(&self, id: &str, context: &CoordinationContext, token: &str,
        result: &CoordinationResult) -> ServiceResult<()> {
        let receipt = self.request(Method::POST, self.url(id, "/finish")?,
            Some(&json!({"context": context, "claim_token": token, "result": result})),
            3, now_ms() + 10000).await?;
        let receipt: CoordinationResult = serde_json::from_value(receipt)
            .map_err(|_| error("invalid_coordination_receipt"))?;
        if &receipt != result { return Err(error("coordination_receipt_mismatch")); }
        Ok(())
    }
}
