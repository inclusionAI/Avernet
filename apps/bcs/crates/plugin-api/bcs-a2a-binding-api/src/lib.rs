//! Deployment-installed custom AgentCard bindings. No provider is enabled by default.
use std::{sync::{Arc, OnceLock}, time::Duration};
use async_trait::async_trait;
use futures::stream::BoxStream;
use serde_json::{json, Value};

pub type Events = BoxStream<'static, Result<a2a::StreamResponse, a2a::A2AError>>;

/// Adapter contract: normalize a remote protocol into the existing A2A task driver.
/// Never replay a side-effecting submission or silently switch transports.
#[async_trait]
pub trait Client: Send + Sync {
    async fn send_message(&self, request: &a2a::SendMessageRequest) -> Result<a2a::SendMessageResponse, a2a::A2AError>;
    async fn send_streaming_message(&self, request: &a2a::SendMessageRequest) -> Result<Events, a2a::A2AError>;
    async fn get_task(&self, request: &a2a::GetTaskRequest) -> Result<a2a::Task, a2a::A2AError>;
    async fn cancel_task(&self, request: &a2a::CancelTaskRequest) -> Result<a2a::Task, a2a::A2AError>;
    async fn subscribe_to_task(&self, request: &a2a::SubscribeToTaskRequest) -> Result<Events, a2a::A2AError>;
}

/// Admission and route policy are shared by registration, refresh and delivery.
/// Methods return safe diagnostic codes, never credentials or remote payloads.
pub trait Binding: Send + Sync {
    fn name(&self) -> &str;
    /// Validate the declaration and claimed streaming capability without network I/O.
    /// Return a stable, non-secret routing key (at most 256 bytes).
    fn resolve(&self, declaration: &Value, streaming: bool) -> Result<String, String>;
    /// Must change if adapter identity/version, target or routing configuration changes.
    fn fingerprint(&self, key: &str) -> Result<String, String>;
    fn client(&self, key: &str, tenant: Option<&str>, bearer: Option<&str>, extensions: &str,
        timeout: Duration) -> Result<Arc<dyn Client>, String>;
}

#[derive(Default)]
pub struct Registry { bindings: Vec<Arc<dyn Binding>> }
impl Registry {
    pub fn new(bindings: Vec<Arc<dyn Binding>>) -> Result<Self, String> {
        let mut names = std::collections::HashSet::new();
        for binding in &bindings {
            let name = binding.name();
            if name.is_empty() || name.len() > 64 || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
                || matches!(name, "HTTP+JSON" | "JSONRPC" | "GRPC" | "CUSTOM") || !names.insert(name.to_owned()) {
                return Err("INVALID_OR_DUPLICATE_BINDING".into());
            }
        }
        Ok(Self { bindings })
    }

    /// Project only for selection/SDK validation. Persist and return the original Card.
    /// Unknown bindings are skipped; malformed enabled declarations fail closed.
    pub fn project(&self, raw: &Value) -> Result<Value, String> {
        let entries = raw.get("supportedInterfaces").and_then(Value::as_array).ok_or("MISSING_INTERFACES")?;
        let streaming = raw.pointer("/capabilities/streaming").and_then(Value::as_bool) == Some(true);
        let mut projected = Vec::new();
        for entry in entries {
            let name = entry.get("protocolBinding").and_then(Value::as_str).ok_or("MISSING_BINDING")?;
            if matches!(name, "HTTP+JSON" | "JSONRPC" | "GRPC") { projected.push(entry.clone()); continue; }
            if entry.get("protocolVersion").and_then(Value::as_str) != Some("1.0") { continue; }
            let Some(binding) = self.bindings.iter().find(|b| b.name() == name) else { continue; };
            let key = binding.resolve(entry, streaming)?;
            if key.is_empty() || key.len() > 256 { return Err("INVALID_ROUTE_KEY".into()); }
            binding.fingerprint(&key)?;
            let mut entry = entry.clone();
            entry["protocolBinding"] = json!("CUSTOM");
            entry["url"] = json!(format!("custom-a2a://route/{}", hex::encode(serde_json::to_vec(&json!([name, key])).map_err(|_| "INVALID_ROUTE_KEY")?)));
            projected.push(entry);
        }
        let mut result = raw.clone();
        result["supportedInterfaces"] = Value::Array(projected);
        Ok(result)
    }

    fn route(&self, url: &str) -> Result<(&Arc<dyn Binding>, String), String> {
        if url.len() > 1024 { return Err("INVALID_CUSTOM_ROUTE".into()); }
        let encoded = url.strip_prefix("custom-a2a://route/").ok_or("INVALID_CUSTOM_ROUTE")?;
        let bytes = hex::decode(encoded).map_err(|_| "INVALID_CUSTOM_ROUTE")?;
        let [name, key]: [String; 2] = serde_json::from_slice(&bytes).map_err(|_| "INVALID_CUSTOM_ROUTE")?;
        let binding = self.bindings.iter().find(|b| b.name() == name).ok_or("BINDING_UNAVAILABLE")?;
        Ok((binding, key))
    }
    pub fn fingerprint(&self, url: &str) -> Result<String, String> {
        let (binding, key) = self.route(url)?;
        binding.fingerprint(&key)
    }
    pub fn client(&self, url: &str, tenant: Option<&str>, bearer: Option<&str>, extensions: &str,
        timeout: Duration) -> Result<Arc<dyn Client>, String> {
        let (binding, key) = self.route(url)?;
        binding.client(&key, tenant, bearer, extensions, timeout)
    }
}

static REGISTRY: OnceLock<Registry> = OnceLock::new();
static EMPTY: Registry = Registry { bindings: Vec::new() };
/// Install once in the composition root before serving requests. Immutable for
/// the process lifetime; restart to change routes. No per-request environment reads.
pub fn install(registry: Registry) -> Result<(), String> {
    REGISTRY.set(registry).map_err(|_| "BINDINGS_ALREADY_INSTALLED".into())
}
pub fn registry() -> &'static Registry { REGISTRY.get().unwrap_or(&EMPTY) }

#[cfg(test)]
mod tests;
