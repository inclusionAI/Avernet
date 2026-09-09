use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use async_trait::async_trait;
use bcs_cache_api::{CachePlugin, CacheSetMode};
use bcs_service_api::port::{CoordinationClaim, CoordinationContext, CoordinationIntentPort,
    CoordinationLease, CoordinationResult};
use bcs_service_api::{ServiceError, ServiceResult};
use serde::Deserialize;
use serde_json::{Map, Value, json};

/// Uses the cache selected by bootstrap; deployments share it with the producer.
pub struct CoordinationCacheStore { cache: Arc<dyn CachePlugin> }
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
impl CoordinationCacheStore {
    pub fn new(cache: Arc<dyn CachePlugin>) -> Self { Self { cache } }

    fn key(id: &str, suffix: &str) -> ServiceResult<String> {
        if !id.strip_prefix("bcs_intent_").is_some_and(|s| s.len() == 32
            && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {
            return Err(error("invalid_coordination_reference"));
        }
        Ok(format!("bcs:coordination:v2:{id}:{suffix}"))
    }

    async fn read(&self, id: &str, suffix: &str, deadline: u64) -> ServiceResult<Option<Value>> {
        let key = Self::key(id, suffix)?;
        for attempt in 0..3 {
            let remaining = deadline.saturating_sub(now_ms()).min(3000);
            if remaining == 0 { return Err(error("coordination_deadline_exceeded")); }
            if let Ok(Ok(value)) = tokio::time::timeout(Duration::from_millis(remaining), self.cache.get_value(&key)).await {
                return value.map(|bytes| {
                    if bytes.len() > 2 * 1024 * 1024 { return Err(error("coordination_payload_too_large")); }
                    serde_json::from_slice(&bytes).map_err(|_| error("invalid_coordination_payload"))
                }).transpose();
            }
            if attempt < 2 {
                let delay = if attempt == 0 { 200 } else { 500 };
                if now_ms() + delay >= deadline { return Err(error("coordination_deadline_exceeded")); }
                tokio::time::sleep(Duration::from_millis(delay)).await;
            }
        }
        Err(error("coordination_cache_unavailable"))
    }

    // A timed-out write may have committed. Never retry a claim to grant execution.
    async fn create(&self, id: &str, suffix: &str, value: &Value, deadline: u64) -> ServiceResult<bool> {
        let remaining = deadline.saturating_sub(now_ms()).min(3000);
        if remaining == 0 { return Err(error("coordination_deadline_exceeded")); }
        let bytes = serde_json::to_vec(value).map_err(|_| error("invalid_coordination_record"))?;
        tokio::time::timeout(Duration::from_millis(remaining), self.cache.set_value(
            &Self::key(id, suffix)?, bytes, Some(Duration::from_secs(172800)), CacheSetMode::InsertOnly,
        )).await.map_err(|_| error("coordination_cache_timeout"))?
            .map_err(|_| error("coordination_cache_unavailable"))
    }
}
#[async_trait]
impl CoordinationIntentPort for CoordinationCacheStore {
    async fn resolve_and_claim(&self, id: &str, tool: &str,
        context: &CoordinationContext, deadline_ms: u64) -> ServiceResult<CoordinationClaim> {
        let value = self.read(id, "payload", deadline_ms).await?
            .ok_or_else(|| error("intent_unavailable"))?;
        let p: Payload = serde_json::from_value(value).map_err(|_| error("invalid_coordination_payload"))?;
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
        let receipt = self.read(id, "result", deadline_ms).await?;
        let existing = self.read(id, "claim", deadline_ms).await?;
        if receipt.is_some() && existing.is_none() { return Err(error("claim_unavailable")); }
        let token = uuid::Uuid::new_v4().simple().to_string();
        let record = json!({"context": context, "claim_token": token, "claimed_at_ms": now_ms()});
        if self.create(id, "claim", &record, deadline_ms).await? {
            return Ok(CoordinationClaim::Acquired(CoordinationLease { arguments: p.arguments, claim_token: token }));
        }
        let claim = self.read(id, "claim", deadline_ms).await?.ok_or_else(|| error("claim_unavailable"))?;
        if claim["context"] != json!(context) { return Err(error("intent_context_conflict")); }
        let result = self.read(id, "result", deadline_ms).await?.map(serde_json::from_value)
            .transpose().map_err(|_| error("invalid_coordination_receipt"))?;
        Ok(CoordinationClaim::Duplicate(result))
    }

    async fn finish(&self, id: &str, context: &CoordinationContext, token: &str,
        result: &CoordinationResult) -> ServiceResult<()> {
        let deadline = now_ms() + 10000;
        let claim = self.read(id, "claim", deadline).await?.ok_or_else(|| error("claim_unavailable"))?;
        if claim["context"] != json!(context) || claim["claim_token"] != token {
            return Err(error("claim_conflict"));
        }
        let value = json!(result);
        for attempt in 0..3 {
            match self.create(id, "result", &value, deadline).await {
                Ok(true) => return Ok(()),
                Ok(false) => {
                    if self.read(id, "result", deadline).await?.as_ref() == Some(&value) { return Ok(()); }
                    return Err(error("result_conflict"));
                }
                Err(e) if attempt == 2 => return Err(e),
                Err(_) => tokio::time::sleep(Duration::from_millis(200)).await,
            }
        }
        Err(error("coordination_cache_unavailable"))
    }
}
