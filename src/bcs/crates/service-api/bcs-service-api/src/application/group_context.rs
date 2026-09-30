//! Group Context application service trait — use-case entry point called by
//! delivery adapters (HTTP routes, WS frame handlers, BCN plugin tools).

use async_trait::async_trait;
use bcs_domain::group_context::{ContextView, Origin, RetrievalItem, TemplateView};

// ── Status ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct StatusRequest {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub domain: Option<String>,
    pub limit: Option<u32>,
}

impl StatusRequest {
    /// Build the framework-injected origin from this request. The application
    /// layer is the only place that constructs an `Origin` — callers below this
    /// layer receive the already-constructed origin.
    pub fn into_origin(self) -> Origin {
        Origin {
            tenant_id: self.tenant_id,
            group_id: self.group_id,
            session_id: self.session_id,
            run_id: self.run_id,
            actor_id: self.actor_id,
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct StatusResponse {
    pub contexts: Vec<ContextView>,
    pub templates: Vec<TemplateView>,
}

// ── Create by template ─────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Deserialize)]
pub struct CreateByTemplateRequest {
    pub template_id: String,
    pub domain: String,
    pub key: String,
    pub content: serde_json::Value,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CreateResponse {
    pub entry_id: String,
    pub version: u64,
    pub created_at_ms: Option<u64>,
}

// ── Update content ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Deserialize)]
pub struct UpdateContentRequest {
    pub entry_id: String,
    pub new_content: serde_json::Value,
    #[serde(default)]
    pub expected_version: Option<u64>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct UpdateResponse {
    pub old_entry_id: String,
    pub new_entry_id: String,
    pub new_version: u64,
}

// ── Retrieve ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Deserialize)]
pub struct RetrieveRequest {
    #[serde(default)]
    pub domain: Option<String>,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
}

fn default_limit() -> u32 {
    20
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct RetrieveResponse {
    pub items: Vec<RetrievalItem>,
}

// ── Application service trait ──────────────────────────────────────────────

#[async_trait]
pub trait GroupContextService: Send + Sync {
    /// Return active contexts and available templates for the given scope.
    async fn status(&self, request: StatusRequest) -> Result<StatusResponse, String>;

    /// Create a new context entry governed by a policy template.
    async fn create_by_template(
        &self,
        origin: Origin,
        request: CreateByTemplateRequest,
    ) -> Result<CreateResponse, String>;

    /// Update the content of an existing context entry.
    async fn update_content(
        &self,
        origin: Origin,
        request: UpdateContentRequest,
    ) -> Result<UpdateResponse, String>;

    /// Retrieve context entries matching the query.
    async fn retrieve(
        &self,
        origin: Origin,
        request: RetrieveRequest,
    ) -> Result<RetrieveResponse, String>;
}