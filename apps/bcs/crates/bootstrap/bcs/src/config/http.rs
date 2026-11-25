use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ProviderHttpConfig {
    /// Select HTTP delivery identity reads; writes always maintain gateway bindings.
    #[serde(default)]
    pub downlink_detection_source: bcs_domain::bot_provider::DownlinkDetectionSource,
    /// Inbound HTTP header names that BCS may forward to HTTP provider webhooks.
    /// Empty by default; matching is case-insensitive.
    #[serde(default)]
    pub bypass_headers: Vec<String>,
    /// Non-sensitive routing headers explicitly approved for durable queues.
    #[serde(default)]
    pub queue_persistable_headers: Vec<String>,
}

impl ProviderHttpConfig {
    pub fn validate(&self) -> Result<(), String> {
        bcs_config_api::queued_provider_headers::validate_config(&self.bypass_headers, &self.queue_persistable_headers)?;
        for raw_name in &self.bypass_headers {
            let name = raw_name.trim();
            if name.is_empty() {
                return Err(
                    "provider_http.bypass_headers must not contain empty header names".to_string(),
                );
            }
            axum::http::HeaderName::try_from(name).map_err(|_| {
                format!("provider_http.bypass_headers contains invalid header name '{raw_name}'")
            })?;
            if is_reserved_provider_bypass_header(name) {
                return Err(format!(
                    "provider_http.bypass_headers contains reserved header name '{raw_name}'"
                ));
            }
        }
        Ok(())
    }
}

fn is_reserved_provider_bypass_header(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    matches!(
        lower.as_str(),
        "authorization"
            | "cookie"
            | "host"
            | "content-length"
            | "content-type"
            | "x-bcs-bot-token"
            | "x-bcs-service-key"
    ) || lower == "bcn"
        || lower.starts_with("bcn-")
        || lower.starts_with("x-bcn-")
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct CorsConfig {
    #[serde(default)]
    pub allowed_origins: Vec<String>,
}
