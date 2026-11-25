use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetryConfig {
    #[serde(default = "default_telemetry_enabled")]
    pub enabled: bool,

    #[serde(default = "default_telemetry_service_name")]
    pub service_name: String,

    #[serde(default)]
    pub otlp_traces_endpoint: Option<String>,

    #[serde(default)]
    pub extra_headers: BTreeMap<String, String>,
}

impl Default for TelemetryConfig {
    fn default() -> Self {
        Self {
            enabled: default_telemetry_enabled(),
            service_name: default_telemetry_service_name(),
            otlp_traces_endpoint: None,
            extra_headers: BTreeMap::new(),
        }
    }
}

impl TelemetryConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.service_name.trim().is_empty() {
            return Err("telemetry.service_name must not be empty".to_string());
        }
        if let Some(endpoint) = self.otlp_traces_endpoint.as_deref() {
            let endpoint = reqwest::Url::parse(endpoint)
                .map_err(|error| format!("telemetry.otlp_traces_endpoint is invalid: {error}"))?;
            if !matches!(endpoint.scheme(), "http" | "https") {
                return Err("telemetry.otlp_traces_endpoint must use http or https".to_string());
            }
        }
        for (name, value) in &self.extra_headers {
            axum::http::HeaderName::try_from(name.as_str()).map_err(|_| {
                format!("telemetry.extra_headers contains invalid header name '{name}'")
            })?;
            axum::http::HeaderValue::try_from(value.as_str()).map_err(|_| {
                format!("telemetry.extra_headers contains an invalid value for '{name}'")
            })?;
        }
        Ok(())
    }
}

fn default_telemetry_enabled() -> bool {
    true
}

fn default_telemetry_service_name() -> String {
    "bcn".to_string()
}
