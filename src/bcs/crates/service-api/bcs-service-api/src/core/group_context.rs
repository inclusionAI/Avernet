//! Group Context core service trait — pure domain logic contract.

use async_trait::async_trait;
use bcs_domain::group_context::{ContextEntry, ContextView, RetrievalItem, TemplateView};

// ── Status ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct StatusCommand {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub domain: Option<String>,
    pub limit: u32,
}

#[derive(Debug, Clone)]
pub struct StatusResult {
    pub contexts: Vec<ContextView>,
    pub templates: Vec<TemplateView>,
}

// ── Create by template ─────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct CreateByTemplateCommand {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub template_id: String,
    pub domain: String,
    pub key: String,
    pub content: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct CreateResult {
    pub entry: ContextEntry,
}

// ── Update content ─────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct UpdateContentCommand {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub entry_id: String,
    pub new_content: serde_json::Value,
    pub expected_version: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct UpdateResult {
    pub new_entry: ContextEntry,
    pub old_entry_id: String,
}

// ── Retrieve ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct RetrieveCommand {
    pub tenant_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub domain: Option<String>,
    pub query: Option<String>,
    pub limit: u32,
}

#[derive(Debug, Clone)]
pub struct RetrieveResult {
    pub items: Vec<RetrievalItem>,
}

// ── Core service trait ─────────────────────────────────────────────────────

#[async_trait]
pub trait GroupContextCoreService: Send + Sync {
    /// Return the current state — active contexts and usable templates.
    async fn status(&self, cmd: StatusCommand) -> Result<StatusResult, String>;

    /// Create a new context entry governed by a policy template.
    async fn create_by_template(
        &self,
        cmd: CreateByTemplateCommand,
    ) -> Result<CreateResult, String>;

    /// Update the content of an existing context entry (CAS or last-write-wins).
    async fn update_content(
        &self,
        cmd: UpdateContentCommand,
    ) -> Result<UpdateResult, String>;

    /// Retrieve context entries matching the query.
    async fn retrieve(&self, cmd: RetrieveCommand) -> Result<RetrieveResult, String>;
}