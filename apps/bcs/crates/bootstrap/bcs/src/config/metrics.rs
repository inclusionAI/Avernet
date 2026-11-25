use serde::{Deserialize, Serialize};

fn default_metrics_endpoint_path() -> String {
    "/metrics".to_string()
}

fn default_metrics_mode() -> MetricsMode {
    MetricsMode::Pull
}

fn default_metrics_enabled() -> bool {
    false
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MetricsConfig {
    #[serde(default = "default_metrics_enabled")]
    pub enabled: bool,

    #[serde(default = "default_metrics_mode")]
    pub mode: MetricsMode,

    #[serde(default = "default_metrics_endpoint_path")]
    pub endpoint_path: String,
}

impl Default for MetricsConfig {
    fn default() -> Self {
        Self {
            enabled: default_metrics_enabled(),
            mode: MetricsMode::Pull,
            endpoint_path: default_metrics_endpoint_path(),
        }
    }
}

impl MetricsConfig {
    pub fn validate(&self) -> Result<(), String> {
        if !self.endpoint_path.starts_with('/') {
            return Err("metrics.endpoint_path must start with '/'".to_string());
        }
        if self.endpoint_path.contains('{') || self.endpoint_path.contains('}') {
            return Err("metrics.endpoint_path must not contain route parameters".to_string());
        }
        if self.endpoint_path == "/health"
            || self.endpoint_path == "/ws"
            || self.endpoint_path == "/ws/bot"
            || self.endpoint_path.starts_with("/ws/")
        {
            return Err(format!(
                "metrics.endpoint_path conflicts with reserved path '{}'",
                self.endpoint_path
            ));
        }
        if is_api_route_namespace(&self.endpoint_path) {
            return Err(format!(
                "metrics.endpoint_path conflicts with existing API route '{}'",
                self.endpoint_path
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MetricsMode {
    Pull,
}

fn is_api_route_namespace(path: &str) -> bool {
    // NOTE: This blocks known API route namespaces, not a complete Axum route
    // inventory. Exact route collisions still surface during router
    // construction; update this list when adding new top-level API roots.
    const API_ROOTS: &[&str] = &[
        "/me",
        "/bots",
        "/admin",
        "/actors",
        "/friends",
        "/groups",
        "/collaboration",
        "/chat",
        "/onboard",
        "/manifest",
    ];

    API_ROOTS.iter().any(|root| path_is_under_root(path, root))
}

fn path_is_under_root(path: &str, root: &str) -> bool {
    path == root
        || path
            .strip_prefix(root)
            .is_some_and(|suffix| suffix.starts_with('/'))
}
