//! Explicit, opt-in business-failure diagnostics, separate from argument-free operation logs.
use std::{future::Future, sync::{Mutex, atomic::{AtomicBool, Ordering}}};
use serde::{de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor}, Deserializer};
use serde_json::Value;

pub const TARGET: &str = "bcs_business_warning";
pub const MAX_PAYLOAD_BYTES: usize = 64 * 1024;

/// A bounded transport snapshot. Never Debug-print this value: it holds a credential
/// solely for redacting occurrences in the body and error strings before logging.
pub struct WarningContext {
    request_id: String,
    operation: String,
    token: String,
    payload: Mutex<Option<Vec<u8>>>,
    emitted: AtomicBool,
}
impl WarningContext {
    pub fn new(request_id: String, operation: String, token: String, payload: Vec<u8>) -> Self {
        Self { request_id, operation, token, payload: Mutex::new(Some(payload)), emitted: AtomicBool::new(false) }
    }
    pub fn without_payload(mut self) -> Self { *self.payload.get_mut().unwrap_or_else(std::sync::PoisonError::into_inner) = None; self }
}
tokio::task_local! { static WARNING_CONTEXT: WarningContext; }

pub async fn with_warning_context<F: Future>(context: WarningContext, future: F) -> F::Output {
    WARNING_CONTEXT.scope(context, future).await
}

/// Called only after the adapter's existing bounded body read. This preserves
/// authentication, protocol and body-limit ordering instead of pre-reading bodies.
pub fn capture_payload(bytes: &[u8]) {
    let _ = WARNING_CONTEXT.try_with(|context| {
        if let Ok(mut payload) = context.payload.lock() {
            if payload.is_none() { *payload = Some(bytes.to_vec()); }
        }
    });
}

pub fn token_prefix(token: &str) -> String {
    if token.chars().count() <= 4 { return "****".into(); }
    let prefix: String = token.chars().take(4).filter(|c| c.is_ascii_graphic()).collect();
    format!("{prefix}****")
}
fn credential_key(key: &str) -> bool {
    let key: String = key.chars().filter(|c| c.is_ascii_alphanumeric()).flat_map(char::to_lowercase).collect();
    matches!(key.as_str(), "authorization" | "proxyauthorization" | "cookie" | "setcookie" |
        "token" | "providertoken" | "provideradmintoken" | "accesstoken" | "refreshtoken" |
        "ctoken" | "password" | "secret" | "clientsecret" | "apikey")
}
fn scrub(text: &str, token: &str) -> String {
    let text = if token.is_empty() { text.to_owned() } else { text.replace(token, "<redacted>") };
    text.replace('\n', "\\n").replace('\r', "\\r")
}
// Serialize during parsing so duplicate keys remain visible in rejected requests.
// A Value round trip would silently discard all but the last duplicate.
struct RedactedJson<'a> { token: &'a str }
impl<'de> DeserializeSeed<'de> for RedactedJson<'_> {
    type Value = String;
    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<String, D::Error> {
        deserializer.deserialize_any(self)
    }
}
impl<'de> Visitor<'de> for RedactedJson<'_> {
    type Value = String;
    fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str("JSON") }
    fn visit_bool<E: de::Error>(self, v: bool) -> Result<String, E> { Ok(v.to_string()) }
    fn visit_i64<E: de::Error>(self, v: i64) -> Result<String, E> { Ok(v.to_string()) }
    fn visit_u64<E: de::Error>(self, v: u64) -> Result<String, E> { Ok(v.to_string()) }
    fn visit_f64<E: de::Error>(self, v: f64) -> Result<String, E> { Ok(v.to_string()) }
    fn visit_unit<E: de::Error>(self) -> Result<String, E> { Ok("null".into()) }
    fn visit_str<E: de::Error>(self, v: &str) -> Result<String, E> {
        let text = if self.token.is_empty() { v.to_owned() } else { v.replace(self.token, "<redacted>") };
        Ok(Value::String(text).to_string())
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<String, A::Error> {
        let mut values = Vec::new();
        while let Some(value) = seq.next_element_seed(RedactedJson { token: self.token })? { values.push(value); }
        Ok(format!("[{}]", values.join(",")))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<String, A::Error> {
        let mut fields = Vec::new();
        while let Some(key) = map.next_key::<String>()? {
            let value = if credential_key(&key) {
                map.next_value::<de::IgnoredAny>()?;
                "\"<redacted>\"".into()
            } else { map.next_value_seed(RedactedJson { token: self.token })? };
            let key = if self.token.is_empty() { key } else { key.replace(self.token, "<redacted>") };
            fields.push(format!("{}:{value}", Value::String(key)));
        }
        Ok(format!("{{{}}}", fields.join(",")))
    }
}

fn payload(bytes: &[u8], token: &str) -> (String, bool, bool) {
    if bytes.is_empty() { return (String::new(), false, true); }
    // Invalid JSON cannot be safely field-redacted. Never fall back to raw bytes.
    let mut parser = serde_json::Deserializer::from_slice(bytes);
    let result = RedactedJson { token }.deserialize(&mut parser)
        .and_then(|text| { parser.end()?; Ok(text) });
    let Ok(mut text) = result else {
        return ("<omitted: invalid JSON; cannot safely redact>".into(), false, false);
    };
    let truncated = text.len() > MAX_PAYLOAD_BYTES;
    if truncated {
        let mut end = MAX_PAYLOAD_BYTES;
        while !text.is_char_boundary(end) { end -= 1; }
        text.truncate(end);
    }
    (text, truncated, true)
}

/// Emit at most once in the current request, before a facade discards typed details.
/// Without a transport scope, still record the error and ambient request identity,
/// but do not mislabel a reconstructed command as an original wire payload.
pub fn warn_current(code: &str, field: &str, reason: &str) {
    WARNING_CONTEXT.try_with(|context| emit(Some(context), code, field, reason))
        .unwrap_or_else(|_| emit(None, code, field, reason));
}
fn emit(context: Option<&WarningContext>, code: &str, field: &str, reason: &str) {
    if context.is_some_and(|c| c.emitted.swap(true, Ordering::Relaxed)) { return; }
    let request_id = context.map(|c| c.request_id.clone())
        .unwrap_or_else(crate::current_request_id);
    let token = context.map_or("", |c| c.token.as_str());
    let snapshot = context.and_then(|c| c.payload.lock().ok());
    let bytes = snapshot.as_deref().and_then(Option::as_deref).unwrap_or_default();
    let (request_payload, payload_truncated, payload_redacted) = payload(bytes, token);
    let correlation = bcs_tracing_api::current_log_correlation();
    let (trace_id, rpc_id) = correlation.as_ref().map(|c| {
        let [(_, trace), (_, rpc)] = c.fields(); (trace, rpc)
    }).unwrap_or(("", ""));
    tracing::warn!(target: "bcs_business_warning",
        log_class = "business_key", event_code = "a2a.management.failed",
        request_id = %request_id, trace_id, rpc_id,
        operation = %context.map_or("a2a.management", |c| c.operation.as_str()),
        error_code = %scrub(code, token), field = %scrub(field, token), reason = %scrub(reason, token),
        provider_token_prefix = %token_prefix(token),
        payload_available = snapshot.as_deref().is_some_and(Option::is_some), payload_redacted,
        payload_original_bytes = bytes.len(), payload_logged_bytes = request_payload.len(),
        payload_truncated, request_payload = %request_payload,
        "AgentCard management failed");
}

#[cfg(test)]
#[path="business_warning_tests.rs"]
mod tests;
